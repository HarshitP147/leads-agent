# Extraction and Verification

## Extraction (`extractor.py`)

- LLM provider is **DeepSeek** (`LLM_PROVIDER=deepseek`), via `langchain-deepseek`'s
  `ChatDeepSeek(model=EXTRACTION_MODEL, api_key=DEEPSEEK_API_KEY, temperature=0,
  extra_body={"thinking": {"type": "disabled"}})`. Thinking is off because DeepSeek
  rejects named/`required` `tool_choice` in thinking mode (HTTP 400), and
  `with_structured_output(method="function_calling")` sends a named tool_choice.
  `ChatDeepSeek` subclasses `langchain_openai.BaseChatOpenAI`, so it gets the same
  `with_structured_output`/`bind_tools`/`usage_metadata` shapes verified in stack.md —
  `LLM_PROVIDER` staying a `Literal["deepseek", "anthropic", "openai"]` in `config.py`
  is a same-interface swap, not a special case. `config.Settings` needs a
  `deepseek_api_key` field alongside the Anthropic/OpenAI ones (done in M3, not M0).
  Timeout ~60 s, `max_retries=2`.
- **Structured output — function calling first, JSON-mode fallback:**
  `structured = llm.with_structured_output(LLMExtraction, method="function_calling",
  include_raw=True)`. `include_raw=True` gives us the `AIMessage` so we can read
  `usage_metadata` and detect parse failures (`parsing_error`) without exceptions.
  Do **not** pass `strict=True` — DeepSeek routes that to its beta endpoint and requires
  every schema property to be marked `required`, which `LLMExtraction` deliberately isn't
  (several fields are legitimately optional, e.g. `title`, `linkedin_url`,
  `missing_info_notes`); reshaping the schema to satisfy strict mode isn't worth losing
  those semantics.
- On `parsing_error`: one repair retry (`function_calling` again, error message appended
  to the prompt). If that also fails, fall back once to
  `llm.with_structured_output(LLMExtraction, method="json_mode", include_raw=True)` —
  DeepSeek's JSON mode *only* guarantees syntactically valid JSON, not schema conformance
  (per DeepSeek's docs: you still have to instruct the schema in the prompt yourself), so
  parse the raw content with `LLMExtraction.model_validate_json(raw.content)` inside a
  try/except rather than trusting `parsed`. If that also fails, record
  `ErrorRecord(kind="parse_error")` and leave `extraction=None`.
- `langchain-deepseek` also translates a malformed-JSON response from the DeepSeek API
  itself into `JSONDecodeError` at the HTTP layer (seen in its source, not just docs) —
  that's a `kind="llm_error"`, not a `parse_error`; catch it separately.
- One LLM call per domain (all pages in one prompt). Do not call per page.

### Prompt layout

System:
```
You extract factual company information for B2B lead enrichment.
Use ONLY the provided page content. If something is not stated, leave it empty.
Never guess people's names, titles, emails, or LinkedIn URLs.
Emails must be chosen from CANDIDATE EMAILS. LinkedIn URLs must appear in the content
or in LINKEDIN LINKS.
```

User (ordered by value, total capped):
```
DOMAIN: supabase.com

CANDIDATE EMAILS:
- hello@supabase.io (from https://supabase.com/contact)
...

LINKEDIN LINKS:
- https://www.linkedin.com/in/... | anchor: "Paul Copplestone" | page: https://...

TEAM CARDS:
- name: ..., role: ..., linkedin: ..., page: ...

=== PAGE: https://supabase.com/company (kind: company) ===
<cleaned markdown>

=== PAGE: https://supabase.com (kind: home) ===
<cleaned markdown>
```

## Verification (`verify.py`)

Runs deterministically after extraction.

**Leaders**
- Normalise (casefold, strip accents/punctuation, collapse spaces).
- `verified=True` if the normalised full name occurs in the cleaned text of `source_url`
  or any fetched page, or in TEAM CARDS / LINKEDIN LINKS anchor text.
  Allow match on first+last token when a middle name is present.
- Drop if the title names a company other than the target (e.g. `CPO, Kavak`).
- Drop if the evidence/name sits in a testimonial/quote attribution block.
- Prefer about/team/company/leadership pages. A name found **only** on the homepage
  is kept if it has any professional title with no foreign company, or if the page
  presents them as the site's owner/author/subject (personal sites: name in the
  domain, matching `company_name`, "I'm …" / "about me"). Testimonials/quotes are
  still dropped.
- Fetched page titles count as deterministic name evidence. This covers visible hero
  names that Trafilatura omits from cleaned markdown without exposing raw HTML to the
  LLM or weakening the name-match requirement.
- Unverified / dropped leaders are logged as
  `ErrorRecord(kind="unverified_person")` so the reviewer sees we caught it.
- LinkedIn URL kept only if it appears in `linkedin_links` or page text, and matches
  `^https?://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9\-_%]+/?$`. Otherwise set `None`
  (search may fill it later). `linkedin_source="website"`.
- Dedupe leaders by normalised name; cap at 10.

**Emails**
- Keep only emails present in `candidate_emails`; attach that candidate's `source_url`.
- Any regex-found email the LLM ignored is still added with `purpose="other"`.

**Overview**
- If not two sentences, trim/merge to two with a simple sentence splitter. No second LLM call.

## LinkedIn search (`search.py`, bonus)

Runs after deterministic website verification and uses Tavily basic search. Responses
are parsed through a Pydantic model before the evidence rules run.

- For each website-verified leader without a URL (max 5), run a company/role-scoped
  LinkedIn query. Keep only a direct `/in/` result whose title or URL slug identifies
  that person, and require separate result evidence that names their leadership role
  at the target company. This prevents a snippet mentioning several people from
  assigning one person's profile to another.
- If the website yields no leaders, run one broad founder/CEO/CTO query and up to three
  focused follow-ups for named profiles whose role is initially missing. A discovered
  leader needs the same direct-profile, identity, company, and role corroboration.
- In parallel, run two target-domain-filtered searches for public contact and sensitive
  inboxes. Accept only literal, non-junk addresses found in result/raw text from a URL
  on the target domain; a same-brand legacy email domain (for example `supabase.io`) is
  allowed. Never construct or infer an address. Merge with website-harvested emails.
- Each attempted call emits `UsageEvent(component="search", search_calls=1)`, including
  a provider error, so the usage and failure are visible rather than lost.
- No Tavily key → skip silently with a `route_log` entry, not an error.
- Never fetch linkedin.com pages.
