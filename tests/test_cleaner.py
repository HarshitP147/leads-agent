"""test_cleaner.py per QUALITY.md: scripts/svg/nav removed; fallback path when
trafilatura returns little; token counts shrink. No network."""

from __future__ import annotations

import pytest

from enrich.cleaner import _clean_markdown, _extract_team_cards, clean_pages
from enrich.fetcher import FetchedPage

DOMAIN = "example.com"


def _page(html: str, *, kind: str = "about") -> FetchedPage:
    return FetchedPage(
        url=f"https://example.com/{kind}",
        requested_url=f"https://example.com/{kind}",
        kind=kind,
        discovered_by="anchor",
        status="ok",
        http_status=200,
        html=html,
        title="Example",
    )


def test_scripts_svg_nav_removed() -> None:
    html = """
    <html><body>
      <nav>Home About Contact</nav>
      <script>trackEvent('pageview');</script>
      <svg><path d="M0 0"/></svg>
      <main><p>This company builds developer tools for building backend applications.</p></main>
      <footer>Copyright 2026</footer>
    </body></html>
    """
    markdown, _soup = _clean_markdown(html)
    assert "trackEvent" not in markdown
    assert "pageview" not in markdown
    assert "M0 0" not in markdown
    assert "developer tools" in markdown


def test_fallback_path_when_trafilatura_returns_little() -> None:
    # A card-grid page with no article/paragraph structure — trafilatura's own
    # boilerplate heuristics tend to return little or nothing for this shape (common on
    # SPA team pages built from cards, per discovery-and-cleaning.md).
    html = """
    <html><body>
      <div class="card"><div>Alice Example</div><div>CEO</div></div>
      <div class="card"><div>Bob Example</div><div>CTO</div></div>
    </body></html>
    """
    markdown, _soup = _clean_markdown(html)
    assert "Alice Example" in markdown
    assert "Bob Example" in markdown


@pytest.mark.asyncio
async def test_token_counts_shrink() -> None:
    noisy_html = (
        """
    <html><body>
      <script>"""
        + ("var x = 1; " * 500)
        + """</script>
      <nav>Home About Contact Pricing Blog Docs</nav>
      <main><p>This company builds developer tools for building backend applications.</p></main>
      <footer>Copyright 2026 Example Inc. All rights reserved.</footer>
    </body></html>
    """
    )
    state = {"pages": [_page(noisy_html)]}
    result = await clean_pages(state)

    assert result["errors"] == []
    assert len(result["cleaned"]) == 1
    page = result["cleaned"][0]
    assert page.raw_tokens > page.clean_tokens
    assert page.clean_tokens > 0


@pytest.mark.asyncio
async def test_skips_pages_without_html_or_not_ok() -> None:
    broken = FetchedPage(
        url="https://example.com/blocked",
        requested_url="https://example.com/blocked",
        kind="other",
        discovered_by="guess",
        status="blocked",
        http_status=403,
        html=None,
        title=None,
    )
    state = {"pages": [broken]}
    result = await clean_pages(state)
    assert result["cleaned"] == []
    assert result["team_cards"] == []


def test_team_card_extraction() -> None:
    html = """
    <html><body>
      <div class="card">
        <h3>Jane Doe</h3>
        <p>Co-founder &amp; CEO</p>
        <a href="https://www.linkedin.com/in/janedoe">LinkedIn</a>
      </div>
    </body></html>
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    cards = _extract_team_cards(soup, "https://example.com/team")
    assert len(cards) == 1
    assert cards[0].name == "Jane Doe"
    assert cards[0].role == "Co-founder & CEO"
    assert cards[0].linkedin_url == "https://www.linkedin.com/in/janedoe"


@pytest.mark.asyncio
async def test_team_cards_only_extracted_for_team_and_leadership_kinds() -> None:
    html = """
    <html><body>
      <h3>Jane Doe</h3>
      <p>CEO</p>
    </body></html>
    """
    state = {"pages": [_page(html, kind="pricing")]}
    result = await clean_pages(state)
    assert result["team_cards"] == []

    state = {"pages": [_page(html, kind="team")]}
    result = await clean_pages(state)
    assert len(result["team_cards"]) == 1
