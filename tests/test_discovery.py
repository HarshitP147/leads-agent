"""Discovery unit tests — no network. See docs/design-docs/discovery-and-cleaning.md
("Collection sections") for the rules under test."""

from __future__ import annotations

from enrich.discovery import (
    _classify,
    _collection_child_reason,
    _discover_anchor_candidates,
    _select_candidates,
    _sitemap_collection_hit,
)

DOMAIN = "example.com"
BASE_URL = "https://example.com"


def _html(*links: tuple[str, str]) -> str:
    anchors = "".join(f'<a href="{href}">{text}</a>' for href, text in links)
    return f"<html><body>{anchors}</body></html>"


def test_blog_child_dropped_before_scoring() -> None:
    html = _html(("/blog/some-title", "Some Title"))
    candidates, considered = _discover_anchor_candidates(html, BASE_URL, DOMAIN)

    assert candidates == []
    assert len(considered) == 1
    assert considered[0].decision == "dropped"
    assert considered[0].reason == "collection-child:/blog"


def test_blog_index_kept_as_low_score_filler() -> None:
    html = _html(("/blog", "Blog"))
    candidates, considered = _discover_anchor_candidates(html, BASE_URL, DOMAIN)

    assert len(candidates) == 1
    assert candidates[0].kind == "other"
    assert candidates[0].score == 0.5
    assert considered == []  # it was classified, not dropped


def test_locale_prefixed_blog_child_also_dropped() -> None:
    assert _collection_child_reason("/en/blog/some-title") == "collection-child:/blog"
    assert (
        _collection_child_reason("/en-us/blog/some-title") == "collection-child:/blog"
    )


def test_docs_child_sitemap_flagged_for_skip() -> None:
    assert _sitemap_collection_hit("https://example.com/docs/sitemap.xml") == "docs"
    assert _sitemap_collection_hit("https://example.com/sitemap-blog.xml") == "blog"
    assert _sitemap_collection_hit("https://example.com/sitemap.xml") is None


def test_about_still_wins_over_blog_noise() -> None:
    html = _html(
        ("/blog/some-title", "Some Title"),
        ("/blog", "Blog"),
        ("/about", "About"),
    )
    candidates, _ = _discover_anchor_candidates(html, BASE_URL, DOMAIN)
    selected = _select_candidates(candidates, max_pages=6)

    about = [c for c in selected if c.kind == "about"]
    assert len(about) == 1
    assert about[0].url.endswith("/about")
    assert not any(c.url.endswith("/blog/some-title") for c in selected)


def test_classify_unaffected_for_ordinary_pages() -> None:
    assert _classify("/about", "")[0] == "about"
    assert _classify("/pricing", "")[0] == "pricing"
