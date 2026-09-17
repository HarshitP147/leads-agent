"""HTML -> cleaned markdown, with fallback and token counts.

See docs/design-docs/discovery-and-cleaning.md (Cleaning).
"""

from __future__ import annotations

import copy
import logging
import re
from typing import TYPE_CHECKING, Literal

import trafilatura
from bs4 import BeautifulSoup
from pydantic import BaseModel

from enrich.config import get_settings
from enrich.models import ErrorRecord

if TYPE_CHECKING:
    from enrich.state import DomainState

logger = logging.getLogger(__name__)

try:
    import tiktoken

    _ENCODING = tiktoken.get_encoding("cl100k_base")
    TOKEN_COUNT_METHOD = "tiktoken(cl100k_base)"
except Exception:  # noqa: BLE001 -- token counting must never block the pipeline
    _ENCODING = None
    TOKEN_COUNT_METHOD = "chars/4"

REMOVE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "svg",
    "canvas",
    "iframe",
    "form",
    "header",
    "nav",
    "footer",
)
COOKIE_BANNER_MARKERS = ("cookie", "consent", "gdpr")
_HIDDEN_STYLE_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE
)
MIN_TRAFILATURA_CHARS = 200
TEAM_CARD_KINDS = ("team", "leadership")
_BLANK_LINES_RE = re.compile(r"\n{3,}")

# "Prioritise pages by kind" (discovery-and-cleaning.md): leadership/team/about first,
# both for cross-page line dedup (first occurrence wins) and for the order extractor.py
# will see pages in.
KIND_PRIORITY: dict[str, int] = {
    "leadership": 0,
    "team": 1,
    "about": 2,
    "home": 3,
    "company": 4,
    "contact": 5,
    "pricing": 6,
    "other": 7,
}


class CleanPage(BaseModel):
    url: str
    kind: Literal[
        "home", "about", "team", "company", "contact", "pricing", "leadership", "other"
    ]
    markdown: str
    raw_tokens: int
    clean_tokens: int


class TeamCard(BaseModel):
    """A structured (name, role, linkedin) triple from a team/leadership page's card
    grid, pulled *before* the DOM gets stripped for markdown conversion — cheap,
    high-signal, and passed to the LLM extractor as its own block in M3 rather than
    buried in prose."""

    name: str
    role: str | None = None
    linkedin_url: str | None = None
    source_url: str


def _count_tokens(text: str) -> int:
    if _ENCODING is not None:
        return len(_ENCODING.encode(text, disallowed_special=()))
    return max(1, len(text) // 4)


def _is_cookie_banner(tag) -> bool:
    class_attr = tag.get("class") or []
    if isinstance(class_attr, str):
        class_attr = [class_attr]
    haystack = " ".join([*class_attr, tag.get("id") or ""]).lower()
    return any(marker in haystack for marker in COOKIE_BANNER_MARKERS)


def _is_hidden(tag) -> bool:
    """True for content invisible to a real visitor: the `hidden` attribute,
    `aria-hidden="true"`, or an inline `display:none`/`visibility:hidden` style. A
    hidden element is exactly where a prompt-injection payload would be planted (never
    seen by a human, only by whatever scrapes the raw HTML) — see
    tests/test_prompt_injection.py."""
    if tag.has_attr("hidden") or tag.get("aria-hidden") == "true":
        return True
    return bool(_HIDDEN_STYLE_RE.search(tag.get("style") or ""))


def _strip_hidden(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(_is_hidden):
        tag.decompose()


def _strip_boilerplate(soup: BeautifulSoup) -> None:
    for selector in REMOVE_SELECTORS:
        for tag in soup.find_all(selector):
            tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.parent is not None and _is_cookie_banner(tag):
            tag.decompose()


def _extract_team_cards(soup: BeautifulSoup, url: str) -> list[TeamCard]:
    """Elements with an h3/h4 name + nearby short role text + optional linkedin href."""
    cards: list[TeamCard] = []
    for heading in soup.find_all(("h3", "h4")):
        name = heading.get_text(strip=True)
        if not name or len(name) > 60:
            continue
        role: str | None = None
        linkedin_url: str | None = None
        sibling = heading.find_next_sibling()
        hops = 0
        while sibling is not None and hops < 3:
            if hasattr(sibling, "get_text"):
                text = sibling.get_text(strip=True)
                if text and role is None and len(text) <= 80:
                    role = text
                for anchor in sibling.find_all("a", href=True):
                    if "linkedin.com/in/" in anchor["href"]:
                        linkedin_url = anchor["href"]
                        break
            sibling = sibling.find_next_sibling()
            hops += 1
        if linkedin_url is None and heading.parent is not None:
            for anchor in heading.parent.find_all("a", href=True):
                if "linkedin.com/in/" in anchor["href"]:
                    linkedin_url = anchor["href"]
                    break
        cards.append(
            TeamCard(name=name, role=role, linkedin_url=linkedin_url, source_url=url)
        )
    return cards


def _collapse_blank_lines(text: str) -> str:
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _dedupe_lines_across_pages(markdowns: list[str]) -> list[str]:
    """`markdowns` must already be in priority order. Drops any non-trivial line
    that already appeared verbatim in an earlier (higher-priority) page — menus/nav
    remnants that survived `_strip_boilerplate`."""
    seen: set[str] = set()
    results: list[str] = []
    for markdown in markdowns:
        kept: list[str] = []
        for line in markdown.split("\n"):
            stripped = line.strip()
            if len(stripped) > 2:
                if stripped in seen:
                    continue
                seen.add(stripped)
            kept.append(line)
        results.append("\n".join(kept))
    return results


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n\n[...truncated...]"


def _clean_markdown(html: str) -> tuple[str, BeautifulSoup]:
    """Returns (markdown, soup-before-boilerplate-stripping) — the caller needs this
    soup for team-card extraction. Hidden content is removed from it here too (not
    only from `stripped`): team-card extraction must not treat an invisible fake h3 as
    a real team member any more than the markdown extraction should quote one."""
    soup = BeautifulSoup(html, "lxml")
    _strip_hidden(soup)
    stripped = copy.deepcopy(soup)
    _strip_boilerplate(stripped)
    markdown = (
        trafilatura.extract(
            str(stripped),
            output_format="markdown",
            include_links=True,
            favor_recall=True,
        )
        or ""
    )
    if len(markdown) < MIN_TRAFILATURA_CHARS:
        markdown = stripped.get_text("\n")
    return _collapse_blank_lines(markdown), soup


async def clean_pages(state: DomainState) -> dict:
    """Node: strip boilerplate, convert to markdown, truncate to MAX_CHARS_PER_PAGE.

    Email/LinkedIn harvesting already happened on the *raw* HTML in fetcher.py/
    discovery.py, well before this node ever runs — the "keep footer text only for
    extracting emails/links" exception from discovery-and-cleaning.md is satisfied by
    that ordering, not by anything in this module."""
    settings = get_settings()
    pages = state.get("pages", [])
    errors: list[ErrorRecord] = []
    team_cards: list[TeamCard] = []

    raw_items: list[tuple[str, str, int, str]] = []  # (url, kind, raw_tokens, markdown)
    for page in pages:
        if page.status != "ok" or not page.html:
            continue
        try:
            raw_tokens = _count_tokens(page.html)
            markdown, soup = _clean_markdown(page.html)
            if page.kind in TEAM_CARD_KINDS:
                team_cards.extend(_extract_team_cards(soup, page.url))
            raw_items.append((page.url, page.kind, raw_tokens, markdown))
        except Exception as exc:  # noqa: BLE001 -- stage boundary (AGENTS.md #3)
            errors.append(
                ErrorRecord(
                    stage="clean_pages",
                    kind="parse_error",
                    message=str(exc),
                    url=page.url,
                )
            )

    raw_items.sort(key=lambda item: KIND_PRIORITY.get(item[1], len(KIND_PRIORITY)))
    deduped = _dedupe_lines_across_pages([item[3] for item in raw_items])

    cleaned: list[CleanPage] = []
    for (url, kind, raw_tokens, _markdown), deduped_markdown in zip(
        raw_items, deduped, strict=True
    ):
        truncated = _truncate(deduped_markdown, settings.max_chars_per_page)
        cleaned.append(
            CleanPage(
                url=url,
                kind=kind,
                markdown=truncated,
                raw_tokens=raw_tokens,
                clean_tokens=_count_tokens(truncated),
            )
        )

    return {"cleaned": cleaned, "team_cards": team_cards, "errors": errors}
