# Stack Reference

Python **>= 3.11** (browser-use requirement). Check with `python --version` inside `.venv`.
If the venv is 3.10, recreate it before anything else.

## Pinned versions (PyPI latest on 16 Sep 2026)

| Package | Version | Role |
|---|---|---|
| langgraph | 1.2.11 | Pipeline graph |
| langchain-core | 1.6.3 | Messages, structured output |
| langchain-anthropic / langchain-openai | 1.3.0 / 1.1.14 | Chat models (relaxed from 1.7.2/1.6.2 — see Gotchas) |
| langchain-deepseek | 1.1.0 | Chat model for extraction (16 Sep decision — see build-plan.md Decisions) |
| browser-use | 0.13.10 | Agentic navigation fallback |
| playwright | 1.63.0 | Headless Chromium |
| trafilatura | 2.2.0 | HTML → markdown |
| beautifulsoup4 + lxml | 4.15.0 / 6.1.3 | DOM stripping, link/email extraction |
| httpx | 0.28.1 | robots/sitemap fetch |
| tiktoken | 0.14.0 | token counts (cl100k_base — approximation, not DeepSeek's real tokenizer; see M2 note) |
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

### DeepSeek (16 Sep — LLM provider switch; re-checked 17 Sep against api-docs.deepseek.com + installed `langchain-deepseek==1.1.0` source)

- **`langchain-deepseek==1.1.0` installs clean against our existing pins** — no relaxing
  needed. It requires `langchain-core<2.0.0,>=1.4.0` (we have 1.6.3 ✓) and
  `langchain-openai<2.0.0,>=1.1.0` (we have 1.1.14 ✓, the version already forced down for
  browser-use). `pip install --dry-run` confirmed zero new conflicts before installing for real.
- **Model ids (re-checked 17 Sep 2026):** DeepSeek's live `/chat/completions` `model`
  enum and `GET /models` still list exactly **`deepseek-flash`** and
  **`deepseek-v4-pro`**. Pricing page (same day): Flash is DeepSeek-V4.1-Flash (native
  multimodal; legacy `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` names still
  route here); V4 Pro continues after 14 Sep 2026 with unchanged billing. Both models
  list **Tool Calls ✓** and **JSON Output ✓**. Default in `.env.example` is
  `EXTRACTION_MODEL=deepseek-flash` (cheaper/faster, still tool-calling capable);
  `deepseek-v4-pro` is the higher-quality alternative. Older aliases `deepseek-chat` /
  `deepseek-reasoner` are not in the current enum.
- **Function calling**: confirmed 17 Sep via the Tool Calls guide
  (`api-docs.deepseek.com/guides/tool_calls`) — `tools` param, `type: "function"`,
  OpenAI-shaped loop, examples use `model="deepseek-flash"`. This is what LangChain's
  `with_structured_output(..., method="function_calling")` (ChatDeepSeek's default) uses
  under the hood.
- **JSON mode**: `response_format: {"type": "json_object"}` is supported, but per DeepSeek's
  own docs, "you must also instruct the model to produce JSON yourself via a system or
  user message" — i.e. JSON mode guarantees syntactically valid JSON, **not** schema
  conformance. This is why our fallback path parses with
  `LLMExtraction.model_validate_json(...)` in a try/except rather than trusting a
  `parsed` field.
- **`ChatDeepSeek` (`langchain_deepseek.chat_models`)**, verified by reading the installed
  source directly:
  - Subclasses `langchain_openai.chat_models.base.BaseChatOpenAI` → inherits the same
    `with_structured_output`, `bind_tools`, and `usage_metadata` shapes already verified
    above for `AIMessage`. No provider-specific usage-accounting code needed in `cost.py`.
  - Constructor takes `model=` (aliased to internal `model_name`), `api_key=` (else reads
    `DEEPSEEK_API_KEY` env var), `base_url=` (else reads `DEEPSEEK_API_BASE`, default
    `https://api.deepseek.com/v1`).
  - `with_structured_output(schema, method="function_calling" | "json_mode", include_raw=True,
    strict=None)`. `method="json_schema"` is silently coerced to `"function_calling"`
    (DeepSeek has no separate strict-JSON-schema endpoint the way OpenAI does).
  - **`strict=True` switches to DeepSeek's beta endpoint** (`https://api.deepseek.com/beta`)
    for schema-enforced tool calls, but its docstring warns "DeepSeek's strict mode
    requires all object properties to be marked as required in the schema." Our
    `LLMExtraction` has several genuinely optional fields (`title`, `linkedin_url`,
    `missing_info_notes`, ...), so we deliberately do **not** use `strict=True` — see
    extraction-and-verification.md for the repair-retry + json_mode fallback we use instead.
  - `_generate`/`_stream` wrap the OpenAI-SDK call and re-raise `JSONDecodeError` with a
    DeepSeek-specific message when the API itself returns a malformed response — catch
    this as `kind="llm_error"`, separate from our own `parsing_error`/`kind="parse_error"`
    handling of a well-formed-JSON-but-wrong-shape response.

## Gotchas log

<!-- Append: date — package — what surprised you — what we did -->

- 2026-09-16 — Plain `pytest` failed with an `ImportError` from a completely
  unrelated real PyPI package: `enrich` (pycontribs/enrich, a console/logging helper —
  `Home-page: https://github.com/pycontribs/enrich`, `Author: Sorin Sbarnea`) had at
  some point been installed directly into `.venv/site-packages` (`pip show enrich`
  showed `Required-by:` empty — nothing in our own dependency tree pulls it in, so this
  was a one-off manual/accidental install, e.g. a stray `pip install enrich` or a
  non-editable `pip install .`/`uv pip install .` of *our own* project landing under
  that name). Because our top-level import package is also literally named `enrich`,
  whichever one import resolution finds first wins — a real, unavoidable name
  collision at the package-name level (not the PyPI distribution-name level: our
  `pyproject.toml`'s distribution is named `leads-agent`, specifically so it never gets
  confused with the real `enrich` package on PyPI, but the *import* name `enrich/` is
  fixed by every module's `from enrich.xxx import ...` and can't change without a
  repo-wide rename). Fixed by uninstalling it (`pip uninstall enrich`) and switching the
  whole project to `uv`-managed workflow (see AGENTS.md Commands): `uv sync` does a
  proper **editable** install of our own project from `pyproject.toml`, and
  `pythonpath = ["."]` in `[tool.pytest.ini_options]` makes plain `pytest` resolve the
  local `enrich/` directory correctly even without any install at all. **Never run a
  non-editable install of this project** (`pip install .` / `uv pip install .` without
  `-e`) — that's almost certainly how the real `enrich` got in the way in the first
  place.
- 2026-09-16 — `pydantic-settings` (imported directly in `config.py` since M0) was
  never added to `requirements.txt` — it only worked because `browser-use` pins it
  transitively (`pydantic-settings==2.15.0`). Added it to `pyproject.toml`'s
  dependencies explicitly now that we're tracking direct deps properly there.
- 2026-09-16 — `uv sync` resolves and installs cleanly against every pin already
  worked out for `pip` (langchain-anthropic/openai, python-dotenv, rich, tiktoken — see
  the entries below) with zero new conflicts; uv's resolver just had to verify the
  same exact pins, not find new ones. `uv export --no-hashes --no-emit-project -o
  requirements.txt` regenerates a fully-pinned (direct + transitive, ~316 packages)
  `requirements.txt` from `uv.lock` — verbose (`# via <package>` comments per line) but
  standard pip-compatible syntax, so `pip install -r requirements.txt` still works with
  no `uv` installed at all. Run that export command again after any dependency change;
  don't hand-edit `requirements.txt` anymore, it's now generated.

- 2026-09-16 — `tiktoken` was already installed transitively (via `langchain-openai`),
  and its `cl100k_base` encoding works fully offline once its BPE file is cached (first
  call needs network to download it — fine in this environment, but note it if running
  fully air-gapped). Added it to `requirements.txt` explicitly since `cleaner.py` now
  imports it directly rather than relying on a transitive dep. It's OpenAI's tokenizer,
  not DeepSeek's — DeepSeek doesn't publish a `tiktoken`-compatible encoding, so
  `raw_tokens`/`clean_tokens` are a consistent *approximation* for the before/after
  reduction-% metric, not an exact count of what DeepSeek's API will actually bill.
  Falls back to `len(text)//4` if `tiktoken` fails to import for any reason;
  `cleaner.TOKEN_COUNT_METHOD` records which was used.

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
- 2026-09-16 — `langchain-deepseek==1.1.0` needed for the DeepSeek provider switch (see
  build-plan.md Decisions) installed with **zero** conflicts against our existing pins —
  no relaxing required this time. Full findings (model ids, function-calling/json_mode
  support, `strict=True`'s beta-endpoint + all-fields-required requirement) in the
  dedicated DeepSeek section above.
- 2026-09-16 — Freshly created `.venv` had no `pip` executable (only a broken
  interpreter) and pip 25.3's `ensurepip` doesn't drop a `pip` symlink either — only
  `pip3`/`pip3.13`. Use `.venv/bin/python -m pip ...` rather than assuming
  `.venv/bin/pip` exists.
