"""test_bot_wall.py per QUALITY.md: Cloudflare-style fixture HTML is detected.
No network."""

from __future__ import annotations

from enrich.fetcher import _looks_like_bot_wall

CF_HTML = """
<html><head><title>Just a moment...</title></head>
<body>
<div id="challenge-platform" class="cf-challenge">
Checking your browser before accessing the site.
</div>
</body></html>
"""


def test_cloudflare_challenge_with_403_is_bot_wall() -> None:
    assert _looks_like_bot_wall(403, CF_HTML, "Just a moment...") is True


def test_attention_required_short_body_is_bot_wall() -> None:
    html = "<html><body>Attention Required. Please enable cookies.</body></html>"
    assert _looks_like_bot_wall(200, html, "Attention Required") is True


def test_normal_page_is_not_bot_wall() -> None:
    html = "<html><body>" + ("We build developer tools. " * 40) + "</body></html>"
    assert _looks_like_bot_wall(200, html, "Acme — Developer tools") is False
