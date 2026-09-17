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

# Content-collection index sections: their *children* are blog posts / docs pages /
# customer stories / job listings, not company-info pages, even when a stray keyword
# collides (e.g. "story" inside a customer-story CTA, "careers" inside a job posting
# under /careers/). See discovery-and-cleaning.md, "Collection sections" (16 Sep, M1
# review). The index itself (bare "/blog") is kept as low-score filler, not dropped.
COLLECTION_SECTIONS = frozenset(
    {
        "blog",
        "docs",
        "changelog",
        "news",
        "press",
        "customers",
        "careers",
        "jobs",
        "guides",
        "tutorials",
        "resources",
        "learn",
        "events",
    }
)
COLLECTION_INDEX_SCORE = 0.5
LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")

PENALTY_SUBSTRINGS = ("/legal", "/terms", "/privacy")
# Product-marketing path segments: a keyword hit here is almost never a real
# about/team page (vapi.ai `/custom-agents/sales-team-agent` classified as team).
PRODUCT_PATH_SEGMENTS = frozenset(
    {"custom-agents", "solutions", "use-cases", "integrations"}
)
PRODUCT_PATH_PENALTY = 4.0
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


class ConsideredLink(BaseModel):
    """Debug audit trail: every URL discovery looked at, not just the winners.
    See discovery-and-cleaning.md, "--debug candidate audit trail"."""

    url: str
    kind: Kind | Literal["other"] | None = None
    score: float | None = None
    source: Literal["sitemap", "anchor", "guess"]
    decision: Literal["selected", "dropped", "skipped"]
    reason: str | None = None


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


def _locale_adjusted_segments(path: str) -> list[str]:
    """Strip a leading locale segment (`/en/`, `/en-us/`) so `/en/blog/x` counts as a
    `blog` child, not an `en` anything."""
    segments = [seg for seg in path.split("/") if seg]
    if segments and LOCALE_SEGMENT_RE.match(segments[0].lower()):
        return segments[1:]
    return segments


def _collection_child_reason(path: str) -> str | None:
    """Non-None if `path` is a *child* of a collection section (dropped before scoring,
    from every source)."""
    segments = _locale_adjusted_segments(path)
    if len(segments) > 1 and segments[0].lower() in COLLECTION_SECTIONS:
        return f"collection-child:/{segments[0].lower()}"
    return None


def _collection_index_kind(path: str) -> Kind | Literal["other"] | None:
    """ "other" if `path` is exactly a collection-section root (`/blog`, `/en/docs`) —
    kept as low-score filler, never a drop."""
    segments = _locale_adjusted_segments(path)
    if len(segments) == 1 and segments[0].lower() in COLLECTION_SECTIONS:
        return "other"
    return None


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _keyword_score(
    keyword: str,
    weight: float,
    *,
    path_lower: str,
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
    last_seg = path_lower.rstrip("/").rsplit("/", 1)[-1]
    if "-" in keyword:
        return weight if keyword in last_seg else None

    candidates: list[float] = []
    if keyword in last_segment_tokens:
        candidates.append(weight / max(len(last_segment_tokens), 1))
    if keyword in anchor_tokens:
        candidates.append(weight / max(len(anchor_tokens), 1))
    return max(candidates) if candidates else None


def _classify(
    path: str, anchor_text: str
) -> tuple[Kind | Literal["other"], float] | None:
    path_lower = path.lower()
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
                anchor_tokens=anchor_tokens,
                last_segment_tokens=last_segment_tokens,
            )
            for kw in keywords
        )
        kind_score = max((s for s in scores if s is not None), default=None)
        if kind_score is not None and (best is None or kind_score > best[1]):
            best = (kind, kind_score)

    if best is None:
        # No real kind matched — fall back to the low-score collection-index filler
        # (e.g. bare "/blog") rather than dropping the URL outright.
        index_kind = _collection_index_kind(path)
        if index_kind is not None:
            return index_kind, COLLECTION_INDEX_SCORE
        return None

    kind, weight = best
    penalty = 0.0
    if any(sub in path.lower() for sub in PENALTY_SUBSTRINGS):
        penalty += 2.0
    if any(
        seg.lower() in PRODUCT_PATH_SEGMENTS for seg in _locale_adjusted_segments(path)
    ):
        penalty += PRODUCT_PATH_PENALTY
    if LANG_PREFIX_RE.match(path):
        penalty += 1.0
    if urlparse(path).query:
        penalty += 1.0
    score = weight - penalty
    if score <= 0:
        return None
    return kind, score


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


def _sitemap_collection_hit(sitemap_url: str) -> str | None:
    """Non-None (the matched section) if a child sitemap's URL is itself about a
    collection section (e.g. "/docs/sitemap.xml", "/sitemap-blog.xml") — walking it just
    to filter every URL back out afterward isn't worth the round-trip."""
    tokens = _tokenize(urlparse(sitemap_url).path)
    return next((section for section in COLLECTION_SECTIONS if section in tokens), None)


async def _sitemap_urls_from(
    client: httpx.AsyncClient,
    sitemap_url: str,
    *,
    depth: int,
    skipped: list[ConsideredLink],
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
            hit = _sitemap_collection_hit(child_url)
            if hit is not None:
                skipped.append(
                    ConsideredLink(
                        url=child_url,
                        source="sitemap",
                        decision="skipped",
                        reason=f"collection-section-sitemap:{hit}",
                    )
                )
                logger.debug("skipped child sitemap %s (section=%s)", child_url, hit)
                continue
            urls.extend(
                await _sitemap_urls_from(
                    client, child_url, depth=depth + 1, skipped=skipped
                )
            )
        return urls

    if root_tag == "urlset":
        for url_el in root:
            if _strip_ns(url_el.tag) != "url":
                continue
            for loc in url_el:
                if _strip_ns(loc.tag) == "loc" and loc.text:
                    urls.append(loc.text.strip())
    return urls


def _drop(
    url: str, *, source: Literal["sitemap", "anchor", "guess"], reason: str
) -> ConsideredLink:
    return ConsideredLink(url=url, source=source, decision="dropped", reason=reason)


async def _discover_sitemap_candidates(
    base_url: str, domain: str
) -> tuple[list[CandidateLink], list[ConsideredLink], ErrorRecord | None]:
    candidates: list[CandidateLink] = []
    considered: list[ConsideredLink] = []
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
                all_urls.extend(
                    await _sitemap_urls_from(
                        client, sitemap_url, depth=0, skipped=considered
                    )
                )

            for url in all_urls:
                # A real sitemap can list thousands of URLs — auditing every generic
                # drop (off-site / blocked-ext / too-deep / unclassified) here would
                # turn the --debug table into a sitemap dump, not a reviewable audit
                # trail. Only the collection-child drop (what this rule is actually
                # about) is worth recording per-URL at this volume; the classified
                # pool (selected/not-selected) is recorded once, after selection, in
                # discover_links.
                path = urlparse(url).path
                child_reason = _collection_child_reason(path)
                if child_reason is not None:
                    considered.append(_drop(url, source="sitemap", reason=child_reason))
                    continue
                if not _same_site(url, domain):
                    continue
                if _has_blocked_extension(path):
                    continue
                if _path_depth(path) > 2:
                    continue
                classified = _classify(path, "")
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
        return (
            candidates,
            considered,
            ErrorRecord(stage="discover_links", kind="sitemap_error", message=str(exc)),
        )
    return candidates, considered, None


# --- Anchor scoring (from the fetched homepage HTML) ---------------------------------


def _discover_anchor_candidates(
    html: str, base_url: str, domain: str
) -> tuple[list[CandidateLink], list[ConsideredLink]]:
    soup = BeautifulSoup(html, "lxml")
    candidates: list[CandidateLink] = []
    considered: list[ConsideredLink] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        anchor_text = anchor.get_text(strip=True)

        child_reason = _collection_child_reason(parsed.path)
        if child_reason is not None:
            considered.append(_drop(absolute, source="anchor", reason=child_reason))
            continue
        if not _same_site(absolute, domain):
            considered.append(_drop(absolute, source="anchor", reason="off-site"))
            continue
        if _has_blocked_extension(parsed.path):
            considered.append(
                _drop(absolute, source="anchor", reason="blocked-extension")
            )
            continue
        classified = _classify(parsed.path, anchor_text)
        if classified is None:
            considered.append(_drop(absolute, source="anchor", reason="unclassified"))
            continue
        kind, score = classified
        if score <= 0:
            considered.append(
                _drop(absolute, source="anchor", reason="non-positive-score")
            )
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
    return candidates, considered


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
                found[email.lower()] = FoundEmail(
                    email=email.lower(), source_url=source_url
                )

    for match in EMAIL_RE.finditer(soup.get_text(" ")):
        email = match.group(0)
        if _is_junk_email(email):
            continue
        email_domain = email.split("@", 1)[1]
        if not (
            _same_site(f"https://{email_domain}", domain) or email.lower() in found
        ):
            continue
        found.setdefault(
            email.lower(), FoundEmail(email=email.lower(), source_url=source_url)
        )

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
    """Node: rank candidate subpages and harvest emails/LinkedIn links from the homepage.

    Also returns `considered_links` (list[ConsideredLink]): every URL looked at, not
    just the winners, for the `--debug` audit trail. This key is diagnostic-only —
    intentionally not part of `state.py`'s documented schema — and is never written to
    `DomainResult`/`output.json`."""
    settings = get_settings()
    domain = state["domain"]
    home = state.get("home")
    errors: list[ErrorRecord] = []

    if home is None or not home.html:
        return {
            "candidates": [],
            "candidate_emails": [],
            "linkedin_links": [],
            "considered_links": [],
            "errors": errors,
        }

    base_url = state.get("base_url") or home.url

    (
        sitemap_candidates,
        sitemap_considered,
        sitemap_error,
    ) = await _discover_sitemap_candidates(base_url, domain)
    if sitemap_error:
        errors.append(sitemap_error)
    anchor_candidates, anchor_considered = _discover_anchor_candidates(
        home.html, base_url, domain
    )

    found_kinds = {c.kind for c in (*sitemap_candidates, *anchor_candidates)}
    guesses = _guess_candidates(base_url, found_kinds)

    classified_pool = [*sitemap_candidates, *anchor_candidates, *guesses]
    selected = _select_candidates(classified_pool, settings.max_pages_per_domain)
    selected_urls = {_normalize_path(c.url) for c in selected}

    considered: list[ConsideredLink] = [*sitemap_considered, *anchor_considered]
    for candidate in classified_pool:
        decision = (
            "selected" if _normalize_path(candidate.url) in selected_urls else "dropped"
        )
        considered.append(
            ConsideredLink(
                url=candidate.url,
                kind=candidate.kind,
                score=candidate.score,
                source=candidate.discovered_by,
                decision=decision,
                reason=None if decision == "selected" else "not-selected",
            )
        )

    emails = find_emails(home.html, home.url, domain)
    linkedin_links = find_linkedin_links(home.html, home.url)

    return {
        "candidates": selected,
        "candidate_emails": emails,
        "linkedin_links": linkedin_links,
        "considered_links": considered,
        "errors": errors,
    }
