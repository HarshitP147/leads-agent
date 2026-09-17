"""Playwright page fetch: shared browser, per-domain context, retries, bot-wall detection.

See docs/design-docs/discovery-and-cleaning.md (Fetching) and
docs/design-docs/resilience.md (Request level) for the full contract.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
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
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from enrich.config import get_settings
from enrich.discovery import (
    CandidateLink,
    FoundEmail,
    FoundLink,
    find_emails,
    find_linkedin_links,
)
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
_DNS_ERROR_MARKERS = ("ERR_NAME_NOT_RESOLVED", "ERR_NAME_RESOLUTION_FAILED")
_EMPTY_OR_CONN_MARKERS = (
    "ERR_EMPTY_RESPONSE",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_CONNECTION_ABORTED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_CONNECTION_FAILED",
)
_ERROR_MESSAGE_LIMIT = 200

# Playwright ops with no native `timeout=` kwarg (new_context, new_page, close,
# content, title, ...) get bounded here instead. This matters more than it looks:
# `asyncio.wait_for` does not hard-kill a task on timeout — it cancels it, then
# *awaits* the cancellation to finish. If a cancelled task's `finally: await
# page.close()` hangs on an unresponsive CDP round-trip, the domain-level
# `asyncio.wait_for` in cli.py never actually returns, no matter what
# DOMAIN_TIMEOUT_S says. Bounding every such op is what makes that timeout real.
_PLAYWRIGHT_OP_TIMEOUT_S = 10
_BROWSER_LAUNCH_TIMEOUT_S = 30

# Page-settle tuning (16 Sep, profiling follow-up): most marketing sites never truly
# go network-idle (analytics/chat-widget polling), so the old 5s networkidle wait was
# ~63-65% of total per-domain time, almost always paid in full. Race the native
# `networkidle` event against a cheap stable-text poll and take whichever fires first,
# capped at NETWORKIDLE_CAP_S either way. See discovery-and-cleaning.md, "Cleaning".
NETWORKIDLE_CAP_S = 2.0
STABLE_TEXT_POLL_S = 0.25
STABLE_TEXT_MIN_CHARS = 1000


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


async def _bounded(coro, *, op: str, timeout_s: float = _PLAYWRIGHT_OP_TIMEOUT_S):
    """Await `coro` with a hard ceiling. Raises `PlaywrightError` (not `TimeoutError`)
    on our own timeout so callers can handle it exactly like any other navigation
    failure; a genuine external cancellation (`asyncio.CancelledError`) is a
    `BaseException`, not caught here, and still propagates normally."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except TimeoutError as exc:
        raise PlaywrightError(f"{op} exceeded {timeout_s}s") from exc


async def _get_browser() -> Browser:
    global _playwright, _browser
    async with _browser_lock:
        if _browser is None:
            settings = get_settings()
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                headless=settings.headless,
                timeout=_BROWSER_LAUNCH_TIMEOUT_S * 1000,
            )
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
    context = await _bounded(
        browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        ),
        op="new_context",
    )
    await context.route("**/*", _block_heavy_resources)
    _contexts[domain] = context
    return context


async def close_browser() -> None:
    """Close every context + the shared browser. Call once at the end of the run
    (see docs/ARCHITECTURE.md, Run level: "Browser closed in finally").

    Each close is individually bounded and independently try/excepted: one stuck or
    already-broken context must not prevent closing the rest, the browser, or the
    Playwright driver itself."""
    global _playwright, _browser
    for context in list(_contexts.values()):
        try:
            await _bounded(context.close(), op="context.close")
        except Exception:
            logger.debug("context.close() failed or timed out", exc_info=True)
    _contexts.clear()
    if _browser is not None:
        try:
            await _bounded(_browser.close(), op="browser.close")
        except Exception:
            logger.debug("browser.close() failed or timed out", exc_info=True)
        _browser = None
    if _playwright is not None:
        try:
            await _bounded(_playwright.stop(), op="playwright.stop")
        except Exception:
            logger.debug("playwright.stop() failed or timed out", exc_info=True)
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


def _is_retryable_navigation_error(exc: BaseException) -> bool:
    """Retry network errors / navigation timeouts / 429 / 5xx — but not a DNS
    resolution failure. The domain doesn't exist; retrying with the same timeout 2
    more times (plus backoff) can't change that, and it turns "the domain is bad" into
    a multi-minute wait for no benefit. Never retry 404 either (handled by the
    caller, not raised here)."""
    if not isinstance(exc, (PlaywrightTimeoutError, PlaywrightError)):
        return False
    message = str(exc)
    return not any(
        marker in message for marker in (*_DNS_ERROR_MARKERS, *_EMPTY_OR_CONN_MARKERS)
    )


async def _goto_with_retries(
    page: Page, url: str, *, timeout_s: int
) -> PlaywrightResponse | None:
    """Up to 2 retries, exponential backoff + jitter. See
    `_is_retryable_navigation_error` for what actually gets retried."""
    response: PlaywrightResponse | None = None

    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        retry=retry_if_exception(_is_retryable_navigation_error),
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


async def _wait_for_stable_text(page: Page) -> None:
    """Poll `document.body.innerText.length`; return once it's > STABLE_TEXT_MIN_CHARS
    and unchanged across two consecutive polls. Runs until cancelled by the caller if
    the text never stabilizes — the NETWORKIDLE_CAP_S race always bounds it."""
    previous_length = -1
    while True:
        await asyncio.sleep(STABLE_TEXT_POLL_S)
        try:
            length = await page.evaluate(
                "document.body ? document.body.innerText.length : 0"
            )
        except PlaywrightError:
            return  # page navigated away or closed; nothing more to poll
        if length > STABLE_TEXT_MIN_CHARS and length == previous_length:
            return
        previous_length = length


async def _settle_page(page: Page, *, timeout_s: int) -> tuple[float, str, float]:
    """Wait for whichever fires first: the native `networkidle` event, or our own
    stable-text poll — capped at NETWORKIDLE_CAP_S either way. Returns
    (settle_s, exit_reason, scroll_s) where exit_reason is "idle" / "stable_text" /
    "cap", logged per page under --debug (see discovery-and-cleaning.md, "Cleaning")."""
    t0 = time.monotonic()
    idle_task = asyncio.ensure_future(page.wait_for_load_state("networkidle"))
    stable_task = asyncio.ensure_future(_wait_for_stable_text(page))

    exit_reason = "cap"
    try:
        done, _pending = await asyncio.wait(
            {idle_task, stable_task},
            timeout=NETWORKIDLE_CAP_S,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if idle_task in done and idle_task.exception() is None:
            exit_reason = "idle"
        elif stable_task in done and stable_task.exception() is None:
            exit_reason = "stable_text"
    finally:
        for task in (idle_task, stable_task):
            if not task.done():
                task.cancel()
        try:
            await _bounded(
                asyncio.gather(idle_task, stable_task, return_exceptions=True),
                op="settle_cleanup",
                timeout_s=5,
            )
        except Exception:
            logger.debug("settle_page cleanup failed or timed out", exc_info=True)
    settle_s = time.monotonic() - t0

    t1 = time.monotonic()
    try:
        await page.mouse.wheel(0, 2000)  # trigger lazy-loaded content
    except PlaywrightError:
        pass
    scroll_s = time.monotonic() - t1

    return settle_s, exit_reason, scroll_s


def _one_line(message: str, limit: int = _ERROR_MESSAGE_LIMIT) -> str:
    line = message.splitlines()[0].strip()
    return line[:limit]


def _http_error_kind(http_status: int | None) -> str:
    if http_status == 404:
        return "http_404"
    if http_status == 429:
        return "rate_limited"
    if http_status is not None and 400 <= http_status < 500:
        return "http_4xx"
    if http_status is not None and http_status >= 500:
        return "http_5xx"
    return "internal"


def _classify_navigation_error(exc: Exception) -> str:
    message = str(exc)
    if any(marker in message for marker in _DNS_ERROR_MARKERS):
        return "dns_error"
    if any(marker in message for marker in _EMPTY_OR_CONN_MARKERS):
        return "empty_response"
    if isinstance(exc, PlaywrightTimeoutError):
        return "timeout"
    if "429" in message:
        return "rate_limited"
    return "internal"


def _is_empty_or_connection_kind(kind: str) -> bool:
    return kind == "empty_response"


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
    page = await _bounded(context.new_page(), op="new_page")
    try:
        goto_t0 = time.monotonic()
        response = await _goto_with_retries(page, url, timeout_s=timeout_s)
        goto_s = time.monotonic() - goto_t0
        settle_s, exit_reason, scroll_s = await _settle_page(page, timeout_s=timeout_s)
        logger.debug(
            "PROFILE %s goto=%.2fs settle=%.2fs(exit=%s) scroll=%.2fs",
            url,
            goto_s,
            settle_s,
            exit_reason,
            scroll_s,
        )
        html = await _bounded(page.content(), op="content")
        title = await _bounded(page.title(), op="title")
        http_status = response.status if response is not None else None
        final_url = page.url

        if _looks_like_bot_wall(http_status, html, title):
            await asyncio.sleep(4)
            try:
                await page.reload(
                    wait_until="domcontentloaded", timeout=timeout_s * 1000
                )
                html = await _bounded(page.content(), op="content")
                title = await _bounded(page.title(), op="title")
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
                kind=_http_error_kind(http_status),
                message=_one_line(f"unexpected status {http_status}"),
                url=url,
            )
        return fetched, error
    finally:
        # Bounded and swallowed on purpose: this runs during cancellation too (the
        # domain-level asyncio.wait_for in cli.py cancels-then-awaits), and an
        # unbounded or raising close() here would block that cancellation from ever
        # completing, or mask whatever exception is already propagating.
        try:
            await _bounded(page.close(), op="page.close")
        except Exception:
            logger.debug("page.close() failed or timed out for %s", url, exc_info=True)


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
        except Exception as exc:
            kind = _classify_navigation_error(exc)
            logger.debug("fetch_home failed %s", url, exc_info=True)
            errors.append(
                ErrorRecord(
                    stage="fetch_home",
                    kind=kind,
                    message=_one_line(str(exc)),
                    url=url,
                )
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


def _remaining_guesses(candidates: list[CandidateLink], current: CandidateLink) -> int:
    seen_current = False
    remaining = 0
    for later in candidates:
        if later is current:
            seen_current = True
            continue
        if seen_current and later.discovered_by == "guess":
            remaining += 1
    return remaining


def _harvest_from(
    fetched: FetchedPage, domain: str, emails: list, links: list
) -> tuple:
    if not fetched.html:
        return emails, links
    emails = _dedupe_emails(emails + find_emails(fetched.html, fetched.url, domain))
    links = _dedupe_links(links + find_linkedin_links(fetched.html, fetched.url))
    return emails, links


async def fetch_subpages(state: DomainState) -> dict:
    """Node: fetch the discovered candidate subpages sequentially, with jitter."""
    settings = get_settings()
    domain = state["domain"]
    candidates = state.get("candidates", [])[: settings.max_pages_per_domain]
    pages = list(state.get("pages", []))
    emails = list(state.get("candidate_emails", []))
    links = list(state.get("linkedin_links", []))
    errors: list[ErrorRecord] = []
    consecutive_empty = 0
    skip_guesses = False

    for candidate in candidates:
        if candidate.discovered_by == "guess" and skip_guesses:
            continue
        await asyncio.sleep(random.uniform(0.2, 0.6))
        logger.debug("PROFILE jitter before %s", candidate.url)
        empty, fetched_err = await _fetch_candidate(
            domain, candidate, settings.page_timeout_s, pages, emails, links, errors
        )
        if fetched_err is None and empty is False:
            consecutive_empty = 0
        elif empty:
            consecutive_empty += 1
        else:
            consecutive_empty = 0
        if consecutive_empty >= 2 and not skip_guesses:
            skip_guesses = True
            remaining = _remaining_guesses(candidates, candidate)
            if remaining:
                errors.append(
                    ErrorRecord(
                        stage="fetch_subpages",
                        kind="empty_response",
                        message=_one_line(
                            f"skipped {remaining} guessed URL(s) after 2 consecutive "
                            "empty/connection failures"
                        ),
                        url=candidate.url,
                    )
                )

    return {
        "pages": pages,
        "candidate_emails": emails,
        "linkedin_links": links,
        "errors": errors,
    }


async def _fetch_candidate(
    domain: str,
    candidate: CandidateLink,
    timeout_s: int,
    pages: list[FetchedPage],
    emails: list[FoundEmail],
    links: list[FoundLink],
    errors: list[ErrorRecord],
) -> tuple[bool, ErrorRecord | None]:
    """Fetch one candidate. Mutates pages/emails/links/errors. Returns (is_empty, error)."""
    try:
        fetched, error = await _fetch_one(
            domain,
            candidate.url,
            kind=candidate.kind,
            discovered_by=candidate.discovered_by,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        kind = _classify_navigation_error(exc)
        logger.debug("fetch_subpages failed %s", candidate.url, exc_info=True)
        error = ErrorRecord(
            stage="fetch_subpages",
            kind=kind,
            message=_one_line(str(exc)),
            url=candidate.url,
        )
        errors.append(error)
        return _is_empty_or_connection_kind(kind), error
    if error is not None:
        errors.append(error)
    pages.append(fetched)
    harvested = _harvest_from(fetched, domain, emails, links)
    emails[:] = harvested[0]
    links[:] = harvested[1]
    empty = error is not None and _is_empty_or_connection_kind(error.kind)
    return empty, error
