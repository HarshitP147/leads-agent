"""robots/sitemap parsing + anchor scoring -> ranked candidate links; email/LinkedIn harvest.

See docs/design-docs/discovery-and-cleaning.md (Discovery).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Literal
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

from enrich.config import get_settings
from enrich.models import ErrorRecord

if TYPE_CHECKING:
    from enrich.state import DomainState

logger = logging.getLogger(__name__)

Kind = Literal["about", "team", "company", "contact", "pricing", "leadership"]

# Highest-weight kind wins when a URL/anchor matches multiple keyword sets; order here
# also breaks ties (leadership > team > about > company > contact > pricing).
KIND_KEYWORDS: dict[Kind, tuple[float, tuple[str, ...]]] = {
    # Bare "management" was dropped (see discovery-and-cleaning.md footnote): it false-
    # positives hard on product marketing sites that sell "X management" features/agents
    # (e.g. vapi.ai's /custom-agents/*-management-agent pages all outscored real
    # leadership candidates in the M1 smoke test).
    "leadership": (
        4.0,
        (
            "leadership",
            "founders",
            "executives",
            "management-team",
            "leadership-team",
            "our-leadership",
            "exec-team",
        ),
    ),
    "team": (3.0, ("team", "people", "our-team")),
    "about": (3.0, ("about", "about-us", "story", "mission")),
    "company": (2.0, ("company", "careers")),
    "contact": (2.0, ("contact", "support", "help", "sales")),
    "pricing": (2.0, ("pricing", "plans")),
}
GUESS_PATHS: dict[Kind, str] = {
    "about": "/about",
    "company": "/company",
    "team": "/team",
    "contact": "/contact",
    "pricing": "/pricing",
}
PENALTY_SUBSTRINGS = ("/blog/", "/docs/", "/changelog", "/legal", "/terms", "/privacy")
LANG_PREFIX_RE = re.compile(r"^/[a-z]{2}(-[a-z]{2})?/")
BLOCKED_EXTENSIONS = (
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".css",
    ".js",
    ".zip",
    ".docx",
    ".mp4",
    ".webp",
    ".ico",
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
EMAIL_JUNK_SUBSTRINGS = ("sentry", "wixpress", "example.com", "example.org")
LINKEDIN_RE = re.compile(
    r"https?://([a-z]{2,3}\.)?linkedin\.com/(in|company)/[A-Za-z0-9\-_%]+/?",
    re.IGNORECASE,
)


class CandidateLink(BaseModel):
    url: str
    kind: Kind | Literal["other"]
    score: float
    discovered_by: Literal["sitemap", "anchor", "guess"]
    anchor_text: str | None = None


class FoundEmail(BaseModel):
    email: str
    source_url: str


class FoundLink(BaseModel):
    url: str
    anchor_text: str | None
    source_url: str


# --- Site-matching / URL hygiene -----------------------------------------------------


def _registrable(host: str) -> str:
    host = host.lower().rstrip(".")
    return host.removeprefix("www.")


def _same_site(url: str, domain: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    return _registrable(host) == _registrable(domain) or _registrable(host).endswith(
        "." + _registrable(domain)
    )


def _normalize_path(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _has_blocked_extension(path: str) -> bool:
    lowered = path.lower()
    return any(lowered.endswith(ext) for ext in BLOCKED_EXTENSIONS)


def _path_depth(path: str) -> int:
    return len([seg for seg in path.split("/") if seg])


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _keyword_score(
    keyword: str,
    weight: float,
    *,
    path_lower: str,
    path_tokens: set[str],
    anchor_tokens: set[str],
    last_segment_tokens: set[str],
) -> float | None:
    """Whole-token match, never substring-in-word (a raw substring check let "mission"
    match inside "submissions" during the M1 smoke test — see discovery-and-cleaning.md).
    Compound keywords (containing "-") are checked as literal phrases against the path,
    since they're already specific. A bare single-word match — in the path's final
    segment or in the anchor text — is discounted by how much of that segment/anchor it
    actually accounts for: a dedicated "/team" page (or a bare "Team" nav link) outranks
    a four-word product slug like "sales-team-agent", and a plain "About" link outranks a
    repeated marketing CTA like "Read the story →" that merely contains the word "story"
    (also caught live in the M1 smoke test, on supabase.com's customer-story cards)."""
    if "-" in keyword:
        return weight if keyword in path_lower else None

    candidates: list[float] = []
    if keyword in anchor_tokens:
        candidates.append(weight / max(len(anchor_tokens), 1))
    if keyword in path_tokens:
        if keyword in last_segment_tokens:
            candidates.append(weight / max(len(last_segment_tokens), 1))
        else:
            candidates.append(
                weight
            )  # matched a whole earlier path segment (e.g. "/team/<slug>")
    return max(candidates) if candidates else None


def _classify(path: str, anchor_text: str) -> tuple[Kind, float] | None:
    path_lower = path.lower()
    path_tokens = _tokenize(path)
    anchor_tokens = _tokenize(anchor_text)
    segments = [seg for seg in path.split("/") if seg]
    last_segment_tokens = _tokenize(segments[-1]) if segments else set()

    best: tuple[Kind, float] | None = None
    for kind, (weight, keywords) in KIND_KEYWORDS.items():
        scores = (
            _keyword_score(
                kw,
                weight,
                path_lower=path_lower,
                path_tokens=path_tokens,
                anchor_tokens=anchor_tokens,
                last_segment_tokens=last_segment_tokens,
            )
            for kw in keywords
        )
        kind_score = max((s for s in scores if s is not None), default=None)
        if kind_score is not None and (best is None or kind_score > best[1]):
            best = (kind, kind_score)
    if best is None:
        return None
    kind, weight = best
    penalty = 0.0
    if any(sub in path.lower() for sub in PENALTY_SUBSTRINGS):
        penalty += 2.0
    if LANG_PREFIX_RE.match(path):
        penalty += 1.0
    if urlparse(path).query:
        penalty += 1.0
    return kind, weight - penalty


# --- Sitemap discovery (httpx, no browser) -------------------------------------------


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        response = await client.get(url, follow_redirects=True, timeout=10.0)
        if response.status_code == 200:
            return response.text
    except httpx.HTTPError:
        pass
    return None


async def _sitemap_urls_from(
    client: httpx.AsyncClient, sitemap_url: str, *, depth: int
) -> list[str]:
    text = await _fetch_text(client, sitemap_url)
    if not text:
        return []
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError:
        return []

    root_tag = _strip_ns(root.tag)
    urls: list[str] = []

    if root_tag == "sitemapindex" and depth == 0:
        child_sitemaps = [
            loc.text.strip()
            for sitemap_el in root
            if _strip_ns(sitemap_el.tag) == "sitemap"
            for loc in sitemap_el
            if _strip_ns(loc.tag) == "loc" and loc.text
        ][:3]
        for child_url in child_sitemaps:
            urls.extend(await _sitemap_urls_from(client, child_url, depth=depth + 1))
        return urls

    if root_tag == "urlset":
        for url_el in root:
            if _strip_ns(url_el.tag) != "url":
                continue
            for loc in url_el:
                if _strip_ns(loc.tag) == "loc" and loc.text:
                    urls.append(loc.text.strip())
    return urls


async def _discover_sitemap_candidates(
    base_url: str, domain: str
) -> tuple[list[CandidateLink], ErrorRecord | None]:
    candidates: list[CandidateLink] = []
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "enrich-agent/0.1"}
        ) as client:
            sitemap_urls: list[str] = []
            robots_text = await _fetch_text(client, urljoin(base_url, "/robots.txt"))
            if robots_text:
                for line in robots_text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sitemap_urls.append(line.split(":", 1)[1].strip())
            if not sitemap_urls:
                sitemap_urls = [urljoin(base_url, "/sitemap.xml")]

            all_urls: list[str] = []
            for sitemap_url in sitemap_urls[:3]:
                all_urls.extend(await _sitemap_urls_from(client, sitemap_url, depth=0))

            for url in all_urls:
                if not _same_site(url, domain) or _has_blocked_extension(
                    urlparse(url).path
                ):
                    continue
                if _path_depth(urlparse(url).path) > 2:
                    continue
                classified = _classify(urlparse(url).path, "")
                if classified is None:
                    continue
                kind, score = classified
                if score <= 0:
                    continue
                candidates.append(
                    CandidateLink(
                        url=_normalize_path(url),
                        kind=kind,
                        score=score,
                        discovered_by="sitemap",
                    )
                )
    except httpx.HTTPError as exc:
        return candidates, ErrorRecord(
            stage="discover_links", kind="sitemap_error", message=str(exc)
        )
    return candidates, None


# --- Anchor scoring (from the fetched homepage HTML) ---------------------------------


def _discover_anchor_candidates(
    html: str, base_url: str, domain: str
) -> list[CandidateLink]:
    soup = BeautifulSoup(html, "lxml")
    candidates: list[CandidateLink] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if not _same_site(absolute, domain) or _has_blocked_extension(parsed.path):
            continue
        anchor_text = anchor.get_text(strip=True)
        classified = _classify(parsed.path, anchor_text)
        if classified is None:
            continue
        kind, score = classified
        if score <= 0:
            continue
        candidates.append(
            CandidateLink(
                url=_normalize_path(absolute),
                kind=kind,
                score=score,
                discovered_by="anchor",
                anchor_text=anchor_text or None,
            )
        )
    return candidates


def _guess_candidates(base_url: str, found_kinds: set[str]) -> list[CandidateLink]:
    return [
        CandidateLink(
            url=urljoin(base_url, path), kind=kind, score=1.0, discovered_by="guess"
        )
        for kind, path in GUESS_PATHS.items()
        if kind not in found_kinds
    ]


def _select_candidates(
    candidates: list[CandidateLink], max_pages: int
) -> list[CandidateLink]:
    best_by_path: dict[str, CandidateLink] = {}
    for candidate in candidates:
        path_key = _normalize_path(candidate.url)
        existing = best_by_path.get(path_key)
        if existing is None or candidate.score > existing.score:
            best_by_path[path_key] = candidate

    best_by_kind: dict[str, CandidateLink] = {}
    for candidate in best_by_path.values():
        existing = best_by_kind.get(candidate.kind)
        if existing is None or candidate.score > existing.score:
            best_by_kind[candidate.kind] = candidate

    remaining = sorted(
        (c for c in best_by_path.values() if best_by_kind.get(c.kind) is not c),
        key=lambda c: c.score,
        reverse=True,
    )
    ordered = list(best_by_kind.values()) + remaining
    ordered.sort(key=lambda c: c.score, reverse=True)
    return ordered[:max_pages]


# --- Email / LinkedIn harvesting (used by discover_links AND fetch_subpages) ---------


def find_emails(html: str, source_url: str, domain: str) -> list[FoundEmail]:
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, FoundEmail] = {}

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if href.lower().startswith("mailto:"):
            email = href.split(":", 1)[1].split("?")[0].strip()
            if email and not _is_junk_email(email):
                found[email.lower()] = FoundEmail(email=email, source_url=source_url)

    for match in EMAIL_RE.finditer(soup.get_text(" ")):
        email = match.group(0)
        if _is_junk_email(email):
            continue
        email_domain = email.split("@", 1)[1]
        if not (
            _same_site(f"https://{email_domain}", domain) or email.lower() in found
        ):
            continue
        found.setdefault(email.lower(), FoundEmail(email=email, source_url=source_url))

    return list(found.values())


def _is_junk_email(email: str) -> bool:
    lowered = email.lower()
    if any(junk in lowered for junk in EMAIL_JUNK_SUBSTRINGS):
        return True
    if re.search(r"@\d+x\.|\.(png|jpg|jpeg|gif|svg|webp)$", lowered):
        return True
    # long hex hash -> asset fingerprint, not a person
    return bool(re.search(r"[0-9a-f]{16,}", lowered))


def find_linkedin_links(html: str, source_url: str) -> list[FoundLink]:
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, FoundLink] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if LINKEDIN_RE.match(href):
            found.setdefault(
                href,
                FoundLink(
                    url=href,
                    anchor_text=anchor.get_text(strip=True) or None,
                    source_url=source_url,
                ),
            )
    return list(found.values())


# --- Node -----------------------------------------------------------------------------


async def discover_links(state: DomainState) -> dict:
    """Node: rank candidate subpages and harvest emails/LinkedIn links from the homepage."""
    settings = get_settings()
    domain = state["domain"]
    home = state.get("home")
    errors: list[ErrorRecord] = []

    if home is None or not home.html:
        return {
            "candidates": [],
            "candidate_emails": [],
            "linkedin_links": [],
            "errors": errors,
        }

    base_url = state.get("base_url") or home.url

    sitemap_candidates, sitemap_error = await _discover_sitemap_candidates(
        base_url, domain
    )
    if sitemap_error:
        errors.append(sitemap_error)
    anchor_candidates = _discover_anchor_candidates(home.html, base_url, domain)

    found_kinds = {c.kind for c in (*sitemap_candidates, *anchor_candidates)}
    guesses = _guess_candidates(base_url, found_kinds)

    selected = _select_candidates(
        [*sitemap_candidates, *anchor_candidates, *guesses],
        settings.max_pages_per_domain,
    )

    emails = find_emails(home.html, home.url, domain)
    linkedin_links = find_linkedin_links(home.html, home.url)

    return {
        "candidates": selected,
        "candidate_emails": emails,
        "linkedin_links": linkedin_links,
        "errors": errors,
    }
