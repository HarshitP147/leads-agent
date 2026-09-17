"""Build prompt from cleaned pages; structured LLM call; usage capture.

See docs/design-docs/extraction-and-verification.md. Uses
`with_structured_output(LLMExtraction, include_raw=True)`, which returns a dict with
`raw`/`parsed`/`parsing_error` (verified in docs/references/stack.md).
"""

from __future__ import annotations

import logging
import time
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek

from enrich.cleaner import KIND_PRIORITY
from enrich.config import get_settings
from enrich.cost import UsageEvent
from enrich.models import ErrorRecord, LLMExtraction

if TYPE_CHECKING:
    from enrich.state import DomainState

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract factual company information for B2B lead enrichment.
Use ONLY the provided page content. If something is not stated, leave it empty.
Never guess people's names, titles, emails, or LinkedIn URLs.
Emails must be chosen from CANDIDATE EMAILS. LinkedIn URLs must appear in the content
or in LINKEDIN LINKS.
"""
JSON_MODE_INSTRUCTION = (
    "Respond with a single JSON object matching the extraction schema. "
    "Do not wrap it in markdown fences."
)
DEFAULT_MODEL = "deepseek-flash"
LLM_TIMEOUT_S = 60.0
StructuredMethod = Literal["function_calling", "json_mode"]


def _llm() -> ChatDeepSeek:
    settings = get_settings()
    api_key = (settings.deepseek_api_key or "").strip()
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY is not set")
    kwargs: dict[str, Any] = {
        "model": settings.extraction_model or DEFAULT_MODEL,
        "api_key": api_key,
        "temperature": 0,
        "timeout": LLM_TIMEOUT_S,
        "max_retries": 2,
    }
    base_url = (settings.deepseek_base_url or "").strip()
    if base_url:
        kwargs["base_url"] = base_url
    return ChatDeepSeek(**kwargs)


def _usage_event(raw: BaseMessage | None, model: str) -> UsageEvent:
    meta = getattr(raw, "usage_metadata", None) or {}
    return UsageEvent(
        component="extraction",
        model=model,
        input_tokens=int(meta.get("input_tokens") or 0),
        output_tokens=int(meta.get("output_tokens") or 0),
    )


def _format_emails(state: DomainState) -> str:
    lines = [
        f"- {e.email} (from {e.source_url})" for e in state.get("candidate_emails", [])
    ]
    return "\n".join(lines) if lines else "- (none)"


def _format_linkedin(state: DomainState) -> str:
    lines = []
    for link in state.get("linkedin_links", []):
        anchor = link.anchor_text or ""
        lines.append(f'- {link.url} | anchor: "{anchor}" | page: {link.source_url}')
    return "\n".join(lines) if lines else "- (none)"


def _format_team_cards(state: DomainState) -> str:
    lines = []
    for card in state.get("team_cards", []):
        lines.append(
            f"- name: {card.name}, role: {card.role or ''}, "
            f"linkedin: {card.linkedin_url or ''}, page: {card.source_url}"
        )
    return "\n".join(lines) if lines else "- (none)"


def _format_pages(state: DomainState) -> str:
    pages = sorted(
        state.get("cleaned", []), key=lambda p: KIND_PRIORITY.get(p.kind, 99)
    )
    return "\n\n".join(
        f"=== PAGE: {page.url} (kind: {page.kind}) ===\n{page.markdown}"
        for page in pages
    )


def _messages(
    state: DomainState, *, extra: str = "", json_mode: bool = False
) -> list[SystemMessage | HumanMessage]:
    system = SYSTEM_PROMPT
    if json_mode:
        system = f"{system}\n{JSON_MODE_INSTRUCTION}"
    user = (
        f"DOMAIN: {state.get('domain', '')}\n\n"
        f"CANDIDATE EMAILS:\n{_format_emails(state)}\n\n"
        f"LINKEDIN LINKS:\n{_format_linkedin(state)}\n\n"
        f"TEAM CARDS:\n{_format_team_cards(state)}\n\n"
        f"{_format_pages(state)}"
    )
    if extra:
        user = f"{user}\n\n{extra}"
    return [SystemMessage(content=system), HumanMessage(content=user)]


def _as_extraction(parsed: Any) -> LLMExtraction | None:
    if isinstance(parsed, LLMExtraction):
        return parsed
    if isinstance(parsed, dict):
        return LLMExtraction.model_validate(parsed)
    return None


def _strip_fence(content: str) -> str:
    text = content.strip()
    if not text.startswith("```"):
        return text
    text = text.strip("`").strip()
    if text.lower().startswith("json"):
        text = text[4:].strip()
    return text


def _extraction_from_raw(raw: BaseMessage | None) -> LLMExtraction:
    content = raw.content if raw is not None and isinstance(raw.content, str) else ""
    return LLMExtraction.model_validate_json(_strip_fence(content))


async def _invoke(
    llm: ChatDeepSeek, messages: list, *, method: StructuredMethod
) -> dict[str, Any]:
    structured = llm.with_structured_output(
        LLMExtraction, method=method, include_raw=True
    )
    result = await structured.ainvoke(messages)
    if not isinstance(result, dict):
        return {"raw": None, "parsed": result, "parsing_error": None}
    return result


def _fail(kind: str, message: str, events: list[UsageEvent] | None = None) -> dict:
    return {
        "extraction": None,
        "errors": [ErrorRecord(stage="extract", kind=kind, message=message)],
        "usage_events": events or [],
        "route_log": [f"extract:{kind}"],
    }


def _ok(parsed: LLMExtraction, events: list[UsageEvent], route: str) -> dict:
    return {
        "extraction": parsed,
        "usage_events": events,
        "route_log": [f"extract:{route}"],
    }


async def _extract_with_fallback(state: DomainState) -> dict:
    llm = _llm()
    model = llm.model_name
    events: list[UsageEvent] = []

    first = await _invoke(llm, _messages(state), method="function_calling")
    events.append(_usage_event(first.get("raw"), model))
    parsed = _as_extraction(first.get("parsed"))
    if parsed is not None and first.get("parsing_error") is None:
        return _ok(parsed, events, "function_calling")

    err = first.get("parsing_error")
    repair = await _invoke(
        llm,
        _messages(state, extra=f"Previous output failed validation: {err}. Fix it."),
        method="function_calling",
    )
    events.append(_usage_event(repair.get("raw"), model))
    parsed = _as_extraction(repair.get("parsed"))
    if parsed is not None and repair.get("parsing_error") is None:
        return _ok(parsed, events, "function_calling_repair")

    return await _json_mode_fallback(llm, state, model, events)


async def _json_mode_fallback(
    llm: ChatDeepSeek,
    state: DomainState,
    model: str,
    events: list[UsageEvent],
) -> dict:
    result = await _invoke(llm, _messages(state, json_mode=True), method="json_mode")
    events.append(_usage_event(result.get("raw"), model))
    try:
        parsed = _extraction_from_raw(result.get("raw"))
    except Exception as exc:  # noqa: BLE001 — schema miss is a parse_error, not a crash
        return _fail("parse_error", str(exc), events)
    return _ok(parsed, events, "json_mode")


async def extract(state: DomainState) -> dict:
    """Node: run the structured extraction call, with one repair retry on parse failure."""
    started = time.monotonic()
    domain = state.get("domain", "")
    if not state.get("cleaned"):
        logger.info("extract skipped domain=%s (no cleaned pages)", domain)
        return {"extraction": None, "route_log": ["extract:skipped_no_pages"]}
    try:
        update = await _extract_with_fallback(state)
    except JSONDecodeError as exc:
        logger.error("domain %s: deepseek malformed JSON: %s", domain, exc)
        update = _fail("llm_error", str(exc))
    except Exception as exc:  # noqa: BLE001 — stage must never raise
        logger.error(
            "domain %s: extract failed: %s: %s", domain, type(exc).__name__, exc
        )
        update = _fail("llm_error", str(exc))
    logger.info(
        "extract done domain=%s duration=%.2fs route=%s",
        domain,
        time.monotonic() - started,
        update.get("route_log"),
    )
    return update
