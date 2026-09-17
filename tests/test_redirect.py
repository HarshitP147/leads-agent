"""Cross-domain redirect handling: fetch_home must adopt the final domain (e.g.
twitter.com -> x.com) as the site's identity for the rest of the run, and record the
switch in route_log. No network — `_fetch_one` is stubbed."""

from __future__ import annotations

import pytest

from enrich import fetcher
from enrich.fetcher import FetchedPage, fetch_home


def _ok_page(url: str) -> FetchedPage:
    return FetchedPage(
        url=url,
        requested_url="https://twitter.com",
        kind="home",
        discovered_by="seed",
        status="ok",
        http_status=200,
        html="<html><body>hi</body></html>",
        title="X",
    )


@pytest.mark.asyncio
async def test_cross_domain_redirect_updates_domain_and_logs_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched = _ok_page("https://x.com/")

    async def fake_fetch_one(domain, url, *, kind, discovered_by, timeout_s):
        return fetched, None

    monkeypatch.setattr(fetcher, "_fetch_one", fake_fetch_one)

    update = await fetch_home({"domain": "twitter.com"})

    assert update["domain"] == "x.com"
    assert update["base_url"] == "https://x.com/"
    assert any(
        "redirected twitter.com->x.com" in entry
        for entry in update.get("route_log", [])
    )


@pytest.mark.asyncio
async def test_same_domain_fetch_does_not_touch_domain_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched = _ok_page("https://supabase.com/")

    async def fake_fetch_one(domain, url, *, kind, discovered_by, timeout_s):
        return fetched, None

    monkeypatch.setattr(fetcher, "_fetch_one", fake_fetch_one)

    update = await fetch_home({"domain": "supabase.com"})

    assert "domain" not in update
    assert "route_log" not in update


@pytest.mark.asyncio
async def test_www_redirect_is_not_treated_as_cross_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain www. redirect (the common case) must not look like a site change."""
    fetched = _ok_page("https://www.supabase.com/")

    async def fake_fetch_one(domain, url, *, kind, discovered_by, timeout_s):
        return fetched, None

    monkeypatch.setattr(fetcher, "_fetch_one", fake_fetch_one)

    update = await fetch_home({"domain": "supabase.com"})

    assert "domain" not in update
