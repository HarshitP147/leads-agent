"""Playwright page fetch: shared browser, per-domain context, retries, bot-wall detection.

See docs/design-docs/discovery-and-cleaning.md (Fetching) and
docs/design-docs/resilience.md (Request level) for the full contract.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import TYPE_CHECKING, Literal

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    Response as PlaywrightResponse,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)
from pydantic import BaseModel
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from enrich.config import get_settings
from enrich.discovery import FoundEmail, FoundLink, find_emails, find_linkedin_links
from enrich.models import ErrorRecord

if TYPE_CHECKING:
    from enrich.state import DomainState

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}
BOT_WALL_MARKERS = (
    "cf-challenge",
    "challenge-platform",
    "just a moment",
    "attention required",
    "captcha",
    "px-captcha",
    "access denied",
)
_TAG_RE = re.compile(r"<[^>]+>")


class FetchedPage(BaseModel):
    """Result of fetching one URL. `html` is kept in memory only — never persisted
    into `DomainResult` (see AGENTS.md rule: no raw HTML to the LLM, ever)."""

    url: str  # final URL after redirects
    requested_url: str
    kind: Literal[
        "home", "about", "team", "company", "contact", "pricing", "leadership", "other"
    ]
    discovered_by: Literal["seed", "sitemap", "anchor", "guess", "agent"]
    status: Literal["ok", "not_found", "blocked", "timeout", "error"]
    http_status: int | None = None
    html: str | None = None
    title: str | None = None


# --- Shared browser / per-domain context lifecycle --------------------------------

_playwright = None
_browser: Browser | None = None
_contexts: dict[str, BrowserContext] = {}
_browser_lock = asyncio.Lock()


async def _get_browser() -> Browser:
    global _playwright, _browser
    async with _browser_lock:
        if _browser is None:
            settings = get_settings()
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(headless=settings.headless)
        return _browser


async def _block_heavy_resources(route) -> None:
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()


async def _get_context(domain: str) -> BrowserContext:
    """One BrowserContext per domain, reused across fetch_home/fetch_subpages calls
    for that domain (isolated cookies, closed once in `close_browser`)."""
    if domain in _contexts:
        return _contexts[domain]
    browser = await _get_browser()
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 900},
        locale="en-US",
    )
    await context.route("**/*", _block_heavy_resources)
    _contexts[domain] = context
    return context


async def close_browser() -> None:
    """Close every context + the shared browser. Call once at the end of the run
    (see docs/ARCHITECTURE.md, Run level: "Browser closed in finally")."""
    global _playwright, _browser
    for context in list(_contexts.values()):
        await context.close()
    _contexts.clear()
    if _browser is not None:
        await _browser.close()
        _browser = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None


# --- Bot-wall detection -------------------------------------------------------------


def _looks_like_bot_wall(http_status: int | None, html: str, title: str) -> bool:
    """See docs/design-docs/discovery-and-cleaning.md, Bot-wall detection."""
    haystack = f"{title}\n{html}".lower()
    if http_status in (403, 503) and any(
        marker in haystack for marker in BOT_WALL_MARKERS
    ):
        return True
    if any(marker in title.lower() for marker in BOT_WALL_MARKERS):
        visible_len = len(_TAG_RE.sub(" ", html))
        if visible_len < 300:
            return True
    return False


# --- Navigation with retries ---------------------------------------------------------


async def _goto_with_retries(
    page: Page, url: str, *, timeout_s: int
) -> PlaywrightResponse | None:
    """2 retries, exponential backoff + jitter, only on network errors / navigation
    timeouts / 429 / 5xx. Never retry 404 (handled by the caller, not raised here)."""
    response: PlaywrightResponse | None = None

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        retry=retry_if_exception_type((PlaywrightTimeoutError, PlaywrightError)),
        reraise=True,
    ):
        with attempt:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=timeout_s * 1000
            )
            if response is not None and response.status == 429:
                retry_after = response.headers.get("retry-after")
                delay = min(float(retry_after), 10.0) if retry_after else 2.0
                await asyncio.sleep(delay)
                raise PlaywrightError(f"429 rate limited on {url}")
            if response is not None and response.status >= 500:
                raise PlaywrightError(f"{response.status} server error on {url}")

    return response


async def _settle_page(page: Page, *, timeout_s: int) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        pass  # SPAs often never go idle; not a failure
    try:
        await page.mouse.wheel(0, 2000)  # trigger lazy-loaded content
    except PlaywrightError:
        pass


def _classify_navigation_error(exc: Exception) -> str:
    message = str(exc)
    if "ERR_NAME_NOT_RESOLVED" in message or "ERR_NAME_RESOLUTION_FAILED" in message:
        return "dns_error"
    if isinstance(exc, PlaywrightTimeoutError):
        return "timeout"
    return "error"


def _candidate_home_urls(domain: str) -> list[str]:
    """Normalise input: strip scheme/paths, try https://{domain} then https://www.{domain}."""
    cleaned = domain.strip().lower()
    for prefix in ("https://", "http://"):
        cleaned = cleaned.removeprefix(prefix)
    cleaned = cleaned.split("/")[0]
    urls = [f"https://{cleaned}"]
    if not cleaned.startswith("www."):
        urls.append(f"https://www.{cleaned}")
    return urls


async def _fetch_one(
    domain: str, url: str, *, kind: str, discovered_by: str, timeout_s: int
) -> tuple[FetchedPage, ErrorRecord | None]:
    context = await _get_context(domain)
    page = await context.new_page()
    try:
        response = await _goto_with_retries(page, url, timeout_s=timeout_s)
        await _settle_page(page, timeout_s=timeout_s)
        html = await page.content()
        title = await page.title()
        http_status = response.status if response is not None else None
        final_url = page.url

        if _looks_like_bot_wall(http_status, html, title):
            await asyncio.sleep(4)
            try:
                await page.reload(
                    wait_until="domcontentloaded", timeout=timeout_s * 1000
                )
                html = await page.content()
                title = await page.title()
            except PlaywrightError:
                pass
            if _looks_like_bot_wall(http_status, html, title):
                fetched = FetchedPage(
                    url=final_url,
                    requested_url=url,
                    kind=kind,
                    discovered_by=discovered_by,
                    status="blocked",
                    http_status=http_status,
                    html=html,
                    title=title,
                )
                error = ErrorRecord(
                    stage="fetcher",
                    kind="bot_wall",
                    message="bot wall detected",
                    url=url,
                )
                return fetched, error

        if http_status == 404:
            fetched = FetchedPage(
                url=final_url,
                requested_url=url,
                kind=kind,
                discovered_by=discovered_by,
                status="not_found",
                http_status=http_status,
                html=None,
                title=title,
            )
            return fetched, None

        status: Literal["ok", "error"] = (
            "ok" if (http_status and http_status < 400) else "error"
        )
        fetched = FetchedPage(
            url=final_url,
            requested_url=url,
            kind=kind,
            discovered_by=discovered_by,
            status=status,
            http_status=http_status,
            html=html,
            title=title,
        )
        error = None
        if status == "error":
            error = ErrorRecord(
                stage="fetcher",
                kind=f"http_{http_status}xx" if http_status else "error",
                message=f"unexpected status {http_status}",
                url=url,
            )
        return fetched, error
    finally:
        await page.close()


# --- Nodes --------------------------------------------------------------------------


async def fetch_home(state: DomainState) -> dict:
    """Node: fetch the domain's homepage. Returns a partial DomainState update."""
    domain = state["domain"]
    settings = get_settings()
    errors: list[ErrorRecord] = []
    candidates = _candidate_home_urls(domain)

    for i, url in enumerate(candidates):
        try:
            fetched, error = await _fetch_one(
                domain,
                url,
                kind="home",
                discovered_by="seed",
                timeout_s=settings.page_timeout_s,
            )
            if error is not None:
                errors.append(error)
            if fetched.status == "not_found" and i < len(candidates) - 1:
                continue  # try the next candidate (e.g. www.)
            return {
                "home": fetched,
                "pages": [fetched],
                "base_url": fetched.url,
                "errors": errors,
            }
        except Exception as exc:  # noqa: BLE001 -- stage boundary: never crash the run (AGENTS.md #3)
            kind = _classify_navigation_error(exc)
            errors.append(
                ErrorRecord(stage="fetch_home", kind=kind, message=str(exc), url=url)
            )
            if i == len(candidates) - 1:
                fetched = FetchedPage(
                    url=url,
                    requested_url=url,
                    kind="home",
                    discovered_by="seed",
                    status="timeout" if kind == "timeout" else "error",
                    http_status=None,
                    html=None,
                    title=None,
                )
                return {"home": fetched, "pages": [fetched], "errors": errors}

    # Unreachable in practice (loop always returns), but keeps the type checker honest.
    fetched = FetchedPage(
        url=domain,
        requested_url=domain,
        kind="home",
        discovered_by="seed",
        status="error",
    )
    return {"home": fetched, "pages": [fetched], "errors": errors}


def _dedupe_emails(emails: list[FoundEmail]) -> list[FoundEmail]:
    seen: dict[str, FoundEmail] = {}
    for email in emails:
        seen.setdefault(email.email.lower(), email)
    return list(seen.values())


def _dedupe_links(links: list[FoundLink]) -> list[FoundLink]:
    seen: dict[str, FoundLink] = {}
    for link in links:
        seen.setdefault(link.url.lower(), link)
    return list(seen.values())


async def fetch_subpages(state: DomainState) -> dict:
    """Node: fetch the discovered candidate subpages sequentially, with jitter."""
    settings = get_settings()
    domain = state["domain"]
    candidates = state.get("candidates", [])[: settings.max_pages_per_domain]
    existing_pages = list(state.get("pages", []))
    candidate_emails = list(state.get("candidate_emails", []))
    linkedin_links = list(state.get("linkedin_links", []))
    errors: list[ErrorRecord] = []

    for candidate in candidates:
        await asyncio.sleep(random.uniform(0.5, 1.5))
        try:
            fetched, error = await _fetch_one(
                domain,
                candidate.url,
                kind=candidate.kind,
                discovered_by=candidate.discovered_by,
                timeout_s=settings.page_timeout_s,
            )
            if error is not None:
                errors.append(error)
            existing_pages.append(fetched)
            if fetched.html:
                candidate_emails = _dedupe_emails(
                    candidate_emails + find_emails(fetched.html, fetched.url, domain)
                )
                linkedin_links = _dedupe_links(
                    linkedin_links + find_linkedin_links(fetched.html, fetched.url)
                )
        except Exception as exc:  # noqa: BLE001 -- stage boundary: never crash the run (AGENTS.md #3)
            kind = _classify_navigation_error(exc)
            errors.append(
                ErrorRecord(
                    stage="fetch_subpages",
                    kind=kind,
                    message=str(exc),
                    url=candidate.url,
                )
            )

    return {
        "pages": existing_pages,
        "candidate_emails": candidate_emails,
        "linkedin_links": linkedin_links,
        "errors": errors,
    }
