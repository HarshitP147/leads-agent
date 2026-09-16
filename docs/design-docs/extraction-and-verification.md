# Extraction and Verification

## Extraction (`extractor.py`)

- Chat model from `LLM_PROVIDER` + `EXTRACTION_MODEL` (`langchain-anthropic` or
  `langchain-openai`), `temperature=0`, timeout ~60 s, `max_retries=2`.
- `structured = llm.with_structured_output(LLMExtraction, include_raw=True)`.
  `include_raw=True` gives us the `AIMessage` so we can read `usage_metadata` and
  detect parse failures (`parsing_error`) without exceptions.
- On parse failure: one repair retry with the error message appended. If it fails again,
  record `ErrorRecord(kind="parse_error")` and leave `extraction=None`.
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
- Unverified leaders are **dropped** from output (logged as
  `ErrorRecord(kind="unverified_person")` so the reviewer sees we caught it).
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

Triggered by `route_after_verify`.

- For each verified leader without a URL (max 5): Tavily query
  `"{name}" "{company_name}" site:linkedin.com/in`, `max_results=5`.
- If zero leaders: one query `"{company_name}" founder CEO site:linkedin.com/in`, then
  accept a result only if its title contains a founder/CEO/co-founder term **and** the
  company name. New people found this way get `verified=True`,
  `source_url=<search result url>`, `linkedin_source="search"`.
- Accept a URL only if it matches the LinkedIn profile regex and the result title
  contains the person's last name (case-insensitive). First acceptable result wins.
- Each call emits `UsageEvent(component="search", search_calls=1)`.
- No Tavily key → skip silently with a `route_log` entry, not an error.
- Never fetch linkedin.com pages.
