# Stack Reference

Python **>= 3.11** (browser-use requirement). Check with `python --version` inside `.venv`.
If the venv is 3.10, recreate it before anything else.

## Pinned versions (PyPI latest on 16 Sep 2026)

| Package | Version | Role |
|---|---|---|
| langgraph | 1.2.11 | Pipeline graph |
| langchain-core | 1.6.3 | Messages, structured output |
| langchain-anthropic / langchain-openai | 1.3.0 / 1.1.14 | Chat models (relaxed from 1.7.2/1.6.2 — see Gotchas) |
| browser-use | 0.13.10 | Agentic navigation fallback |
| playwright | 1.63.0 | Headless Chromium |
| trafilatura | 2.2.0 | HTML → markdown |
| beautifulsoup4 + lxml | 4.15.0 / 6.1.3 | DOM stripping, link/email extraction |
| httpx | 0.28.1 | robots/sitemap fetch |
| tavily-python | 0.8.3 | LinkedIn search |
| pydantic | 2.13.5 | Schemas |
| tenacity | 9.1.4 | Retries |
| typer + rich | 0.27.2 / 14.3.3 | CLI + tables (rich relaxed from 15.0.0 — see Gotchas) |

## Verify-before-use checklist

Confirm against the **installed** source, then record findings below:

- [x] LangGraph: `StateGraph`, `add_conditional_edges`, `START/END` import paths; async `ainvoke`.
- [x] `with_structured_output(..., include_raw=True)` return shape (`raw`, `parsed`, `parsing_error`).
- [x] `AIMessage.usage_metadata` keys for the chosen provider.
- [x] Browser Use: `Agent` constructor args, which LLM class it expects, `max_steps` location,
      structured output option, how to read token usage from the run history.
- [x] Tavily client: async support, `search()` params (`include_domains`, `max_results`).
- [x] trafilatura markdown output flag name in 2.x.

### Findings

- **LangGraph** (`langgraph==1.2.11`): `from langgraph.graph import StateGraph, START, END`.
  `START`/`END` are just the sentinel strings `"__start__"`/`"__end__"`.
  `StateGraph(SomeTypedDict)` builds a graph; `.add_conditional_edges(source, path_fn, path_map=None)`.
  `.compile()` returns a `CompiledStateGraph`, and **that** object has
  `async def ainvoke(input, config=None, *, context=None, stream_mode='values', ...)`
  — `ainvoke` is on the compiled graph, not on `StateGraph` itself. Confirmed end-to-end with
  a toy graph.
- **`with_structured_output(schema, include_raw=True)`** (`langchain-core==1.6.3`, on
  `BaseChatModel`): with `include_raw=True` the runnable always returns a `dict` with keys
  `'raw'` (the original `BaseMessage`), `'parsed'` (the Pydantic instance, or `None` on a
  parse failure), and `'parsing_error'` (caught exception or `None`). Matches schemas.md
  assumption exactly — safe to build the extractor's repair-retry around `parsing_error`.
- **`AIMessage.usage_metadata`**: typed as `UsageMetadata | None` with keys
  `input_tokens`, `output_tokens`, `total_tokens` (ints) plus optional
  `input_token_details` / `output_token_details`. Same field names regardless of provider
  (anthropic vs openai chat models both populate this normalized shape) — `cost.py` can read
  one set of keys regardless of `LLM_PROVIDER`.
- **Browser Use `Agent`** (`browser-use==0.13.10`):
  - Constructor: `Agent(task, llm=None, ..., output_model_schema=None, extraction_schema=None,
    calculate_cost=False, ...)`. **No `max_steps` here.**
  - `max_steps` is a parameter of `.run(max_steps=500, on_step_start=None, on_step_end=None)`,
    which is `async` (despite the name) and returns an `AgentHistoryList`. Pass
    `BROWSER_USE_MAX_STEPS` to `agent.run(max_steps=...)`, not the constructor.
  - LLM class: no top-level `browser_use.ChatXxx` export. Provider chat models live under
    `browser_use.llm.<provider>`, e.g. `from browser_use.llm.anthropic.chat import ChatAnthropic`
    (also `browser_use.llm.openai`). These are Browser Use's own lightweight chat wrappers,
    **not** `langchain_anthropic.ChatAnthropic` — do not pass a LangChain chat model into
    `Agent(llm=...)`.
  - Token usage: only populated if `Agent(..., calculate_cost=True)`. Then
    `history = await agent.run(...)`; `history.usage` is a `UsageSummary`
    (`browser_use.tokens.views`) with `total_prompt_tokens`, `total_completion_tokens`,
    `total_tokens`, `total_cost`, `entry_count`, and `by_model: dict[str, ModelUsageStats]`.
    Per-call usage during the run is `ChatInvokeUsage` (`prompt_tokens`, `completion_tokens`,
    `total_tokens`, plus cache-token fields) — `cost.py`'s navigation `UsageEvent`s should read
    from `history.usage`, aggregated once per `navigator.py` call, not per internal step.
- **Tavily**: `from tavily import AsyncTavilyClient`; `await client.search(query, ...)` has the
  same kwargs as the sync client, including `include_domains: Sequence[str]`,
  `max_results: int`, `search_depth`, `include_raw_content`. Use `AsyncTavilyClient` in
  `search.py` to keep the graph fully async.
- **trafilatura markdown**: `trafilatura.extract(html, output_format='markdown', ...)` — it's a
  string enum value (`'txt' | 'markdown' | ...`), not a boolean flag. Confirmed it runs without
  error on a sample fragment; combine with `include_formatting=True`, `include_links=True` for
  richer markdown when cleaning pages in `cleaner.py`.

## Gotchas log

<!-- Append: date — package — what surprised you — what we did -->

- 2026-09-16 — `browser-use==0.13.10` pins its LLM-provider deps to **exact** versions
  (`anthropic==0.76.0`, `openai==2.26.0`, `python-dotenv==1.2.2`, `rich==14.3.3`), which
  conflicted with our originally-pinned `langchain-anthropic==1.7.2` (needs
  `anthropic>=0.120.0`), `langchain-openai==1.6.2` (needs `openai>=2.45.0`),
  `python-dotenv==1.2.3`, `rich==15.0.0`. Resolved by relaxing to the newest versions of
  each that still satisfy browser-use's exact pins:
  `langchain-anthropic==1.3.0` (`anthropic>=0.75.0,<1.0.0` ✅ 0.76.0),
  `langchain-openai==1.1.14` (`openai>=2.26.0,<3.0.0` ✅ 2.26.0; this is also the exact
  version browser-use itself pins for its own `[all]`/`[examples]` extras — good signal
  it's the intended compatible version), `python-dotenv==1.2.2`, `rich==14.3.3`. Verified
  both langchain-anthropic 1.3.0 and langchain-openai 1.1.14 still declare
  `langchain-core>=1.2.31,<2.0.0` (compat) and langgraph 1.2.11 wants
  `langchain-core>=1.4.7,<2` — all satisfied by our pinned `langchain-core==1.6.3`.
  `requirements.txt` updated to the resolved set; full install then succeeded.
- 2026-09-16 — Freshly created `.venv` had no `pip` executable (only a broken
  interpreter) and pip 25.3's `ensurepip` doesn't drop a `pip` symlink either — only
  `pip3`/`pip3.13`. Use `.venv/bin/python -m pip ...` rather than assuming
  `.venv/bin/pip` exists.
