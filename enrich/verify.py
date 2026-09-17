"""Name/email/LinkedIn grounding checks: drop unverified people (AGENTS.md rule 5).

See docs/design-docs/extraction-and-verification.md.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from enrich.models import ContactEmail, ErrorRecord, Leader, LLMExtraction, LLMLeader

if TYPE_CHECKING:
    from enrich.cleaner import CleanPage
    from enrich.state import DomainState

logger = logging.getLogger(__name__)

LINKEDIN_RE = re.compile(
    r"^https?://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?$",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_TITLE_SPLIT_RE = re.compile(
    r"\s*(?:,|(?:\bat\b)|[|@]|(?:\s[-–—]\s))\s*", re.IGNORECASE
)
_STRONG_TITLE_RE = re.compile(
    r"\b(founder|co-?founder|ceo|cto|coo|cfo|president|vp|"
    r"vice[ -]?president|head of)\b",
    re.IGNORECASE,
)
PREFERRED_LEADER_KINDS = frozenset({"about", "team", "company", "leadership"})
QUOTE_CHARS = frozenset('"“”«»')
TESTIMONIAL_MARKERS = (
    "testimonial",
    "customer stor",
    "read the story",
    "trusted by",
    "what our customers",
    "success stor",
    "case study",
)
ROLE_OR_DEPT = frozenset(
    {
        "founder",
        "cofounder",
        "co",
        "ceo",
        "cto",
        "coo",
        "cfo",
        "cpo",
        "cro",
        "president",
        "vp",
        "vice",
        "head",
        "director",
        "officer",
        "chief",
        "engineer",
        "engineering",
        "manager",
        "lead",
        "product",
        "ai",
        "software",
        "development",
        "developer",
        "sales",
        "marketing",
        "design",
        "operations",
        "people",
        "finance",
        "legal",
        "research",
        "customer",
        "success",
        "support",
        "growth",
        "talent",
        "communications",
        "data",
        "staff",
        "principal",
        "senior",
        "junior",
        "intern",
        "and",
        "the",
        "of",
        "for",
    }
)
MAX_LEADERS = 10


def _normalise_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    no_marks = "".join(c for c in nfkd if not unicodedata.combining(c))
    no_punct = re.sub(r"[^\w\s]", " ", no_marks, flags=re.UNICODE)
    return re.sub(r"\s+", " ", no_punct).strip().casefold()


def _name_variants(name: str) -> list[str]:
    norm = _normalise_name(name)
    if not norm:
        return []
    variants = [norm]
    tokens = norm.split()
    if len(tokens) >= 3:
        variants.append(f"{tokens[0]} {tokens[-1]}")
    return variants


def _haystacks(state: DomainState) -> list[str]:
    texts = [_normalise_name(page.markdown) for page in state.get("cleaned", [])]
    for card in state.get("team_cards", []):
        texts.append(_normalise_name(f"{card.name} {card.role or ''}"))
    for link in state.get("linkedin_links", []):
        texts.append(_normalise_name(link.anchor_text or ""))
    return texts


def _name_grounded(name: str, haystacks: list[str]) -> bool:
    return any(variant in hay for variant in _name_variants(name) for hay in haystacks)


def two_sentences(text: str) -> str:
    """Trim/merge to two sentences with a simple splitter. No second LLM call."""
    collapsed = " ".join(text.split())
    if not collapsed:
        return collapsed
    parts = [p.strip() for p in _SENTENCE_RE.split(collapsed) if p.strip()]
    if len(parts) <= 1:
        return collapsed
    first, rest = parts[0], " ".join(parts[1:])
    if not first.endswith((".", "!", "?")):
        first += "."
    if rest and not rest.endswith((".", "!", "?")):
        rest += "."
    return f"{first} {rest}"


def _linkedin_kept(url: str | None, state: DomainState) -> str | None:
    if not url or not LINKEDIN_RE.match(url):
        return None
    needle = url.rstrip("/")
    known = {link.url.rstrip("/") for link in state.get("linkedin_links", [])}
    if needle in known:
        return url
    for page in state.get("cleaned", []):
        if needle in page.markdown or url in page.markdown:
            return url
    return None


def _target_tokens(company_name: str, domain: str) -> set[str]:
    tokens = set(_normalise_name(company_name).split())
    host = urlparse(f"https://{domain}").hostname or domain
    stem = host.lower().removeprefix("www.").split(".")[0]
    if stem:
        tokens.add(stem)
    return {tok for tok in tokens if tok}


def _foreign_company_in_title(title: str | None, allowed: set[str]) -> bool:
    """True if the title names a company other than the target (e.g. 'CPO, Kavak')."""
    if not title:
        return False
    parts = [p.strip() for p in _TITLE_SPLIT_RE.split(title) if p.strip()]
    if len(parts) < 2:
        return False
    for part in parts[1:]:
        tokens = _normalise_name(part).split()
        if not tokens:
            continue
        if any(tok in allowed for tok in tokens):
            continue
        if all(tok in ROLE_OR_DEPT for tok in tokens):
            continue
        return True
    return False


def _page_for_url(url: str, pages: list[CleanPage]) -> CleanPage | None:
    needle = url.rstrip("/")
    for page in pages:
        if page.url.rstrip("/") == needle:
            return page
    return None


def _name_on_preferred_pages(name: str, state: DomainState) -> bool:
    for page in state.get("cleaned", []):
        if page.kind not in PREFERRED_LEADER_KINDS:
            continue
        if _name_grounded(name, [_normalise_name(page.markdown)]):
            return True
    for card in state.get("team_cards", []):
        if _name_grounded(name, [_normalise_name(card.name)]):
            return True
    return False


def _in_quote_context(name: str, evidence: str, markdown: str) -> bool:
    """True if the name sits in a testimonial/quote attribution block."""
    ev = (evidence or "").strip()
    if ev[:1] in QUOTE_CHARS or ev[-1:] in QUOTE_CHARS:
        return True
    low = markdown.casefold()
    idx = low.find(name.casefold())
    if idx < 0 and ev:
        idx = low.find(ev[:40].casefold())
    if idx < 0:
        return False
    window = markdown[max(0, idx - 400) : idx + 80]
    if any(marker in window.casefold() for marker in TESTIMONIAL_MARKERS):
        return True
    before = markdown[max(0, idx - 250) : idx]
    if not any(char in before for char in QUOTE_CHARS):
        return False
    line_start = markdown.rfind("\n", 0, idx) + 1
    line_end = markdown.find("\n", idx)
    line = markdown[line_start : line_end if line_end != -1 else None].strip()
    return len(line) < 120


def _drop(item: LLMLeader, reason: str) -> tuple[None, ErrorRecord]:
    return None, ErrorRecord(
        stage="verify",
        kind="unverified_person",
        message=f"dropped {reason}: {item.name}",
        url=item.source_url,
    )


def _one_leader(
    item: LLMLeader,
    haystacks: list[str],
    seen: set[str],
    state: DomainState,
    allowed: set[str],
) -> tuple[Leader | None, ErrorRecord | None]:
    key = _normalise_name(item.name)
    if not key or key in seen:
        return None, None
    if not _name_grounded(item.name, haystacks):
        return _drop(item, "unverified leader")
    if _foreign_company_in_title(item.title, allowed):
        return _drop(item, "foreign-company title")
    source_page = _page_for_url(item.source_url, state.get("cleaned", []))
    markdown = source_page.markdown if source_page is not None else ""
    if markdown and _in_quote_context(item.name, item.evidence, markdown):
        return _drop(item, "testimonial/quote")
    homepage_only = not _name_on_preferred_pages(item.name, state)
    if homepage_only and not _STRONG_TITLE_RE.search(item.title or ""):
        return _drop(item, "homepage-only without exec title")
    seen.add(key)
    linkedin = _linkedin_kept(item.linkedin_url, state)
    return (
        Leader(
            name=item.name,
            title=item.title,
            linkedin_url=linkedin,
            linkedin_source="website" if linkedin else None,
            source_url=item.source_url,
            verified=True,
        ),
        None,
    )


def _verify_leaders(
    extraction: LLMExtraction, state: DomainState
) -> tuple[list[Leader], list[ErrorRecord]]:
    haystacks = _haystacks(state)
    allowed = _target_tokens(extraction.company_name, state.get("domain", ""))
    leaders: list[Leader] = []
    errors: list[ErrorRecord] = []
    seen: set[str] = set()
    for item in extraction.leaders:
        kept, error = _one_leader(item, haystacks, seen, state, allowed)
        if error is not None:
            errors.append(error)
        if kept is not None:
            leaders.append(kept)
        if len(leaders) >= MAX_LEADERS:
            break
    return leaders, errors


def _verify_emails(extraction: LLMExtraction, state: DomainState) -> list[ContactEmail]:
    candidates = {e.email.lower(): e for e in state.get("candidate_emails", [])}
    out: list[ContactEmail] = []
    seen: set[str] = set()
    for item in extraction.contact_emails:
        found = candidates.get(item.email.lower())
        if found is None or found.email.lower() in seen:
            continue
        seen.add(found.email.lower())
        out.append(
            ContactEmail(
                email=found.email.lower(),
                purpose=item.purpose,
                source_url=found.source_url,
            )
        )
    for key, found in candidates.items():
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ContactEmail(
                email=found.email.lower(), purpose="other", source_url=found.source_url
            )
        )
    return out


async def verify(state: DomainState) -> dict:
    """Node: keep only leaders/emails grounded in fetched page text."""
    started = time.monotonic()
    domain = state.get("domain", "")
    extraction = state.get("extraction")
    if extraction is None:
        logger.info("verify skipped domain=%s (no extraction)", domain)
        return {
            "leaders": [],
            "contact_emails": [],
            "route_log": ["verify:skipped_no_extraction"],
        }
    try:
        leaders, errors = _verify_leaders(extraction, state)
        emails = _verify_emails(extraction, state)
        updated = extraction.model_copy(
            update={"overview": two_sentences(extraction.overview)}
        )
        update: dict = {
            "extraction": updated,
            "leaders": leaders,
            "contact_emails": emails,
            "errors": errors,
            "route_log": [f"verify:leaders={len(leaders)} dropped={len(errors)}"],
        }
    except Exception as exc:  # noqa: BLE001 — stage must never raise
        logger.error(
            "domain %s: verify failed: %s: %s", domain, type(exc).__name__, exc
        )
        update = {
            "leaders": [],
            "contact_emails": [],
            "errors": [ErrorRecord(stage="verify", kind="internal", message=str(exc))],
            "route_log": ["verify:internal"],
        }
    logger.info(
        "verify done domain=%s duration=%.2fs", domain, time.monotonic() - started
    )
    return update
