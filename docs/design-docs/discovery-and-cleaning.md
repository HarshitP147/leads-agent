# Fetching, Discovery, Cleaning

## Fetching (`fetcher.py`)

- Async Playwright, Chromium, `headless` from settings. One browser, one context per domain.
- Context: realistic desktop UA, `viewport 1366x900`, `locale en-US`, block heavy resources
  via `context.route` (images, media, fonts). Keep stylesheets off too; we don't render visually.
- `page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_S*1000)`, then
  `page.wait_for_load_state("networkidle", timeout=~5s)` inside try/except (SPAs often never go
  idle; a timeout here is **not** a failure). Then a short scroll to trigger lazy content.
- Capture: final URL (after redirects), HTTP status from the response, `page.content()`, title.
- Retries: `tenacity`, 2 retries, exponential backoff with jitter, only on network errors /
  navigation timeouts / 429 / 5xx. Never retry 404.
- For a 429, respect `Retry-After` if present (cap at 10 s).
- Normalise input: strip scheme/paths, try `https://{domain}` then `https://www.{domain}`.

### Bot-wall detection

Mark `status="blocked"` when any holds:
- HTTP 403/503 **and** body contains challenge markers
  (`cf-challenge`, `challenge-platform`, `Just a moment`, `Attention Required`,
  `captcha`, `px-captcha`, `Access Denied`);
- title matches those markers and visible text < 300 chars.

On blocked: wait 3–5 s once and reload (JS challenges sometimes auto-resolve). If still
blocked, record `ErrorRecord(kind="bot_wall")` and continue with whatever else we have.
**We do not attempt CAPTCHA solving or stealth plugins.** State this in the README.

## Discovery (`discovery.py`)

Goal: up to `MAX_PAGES_PER_DOMAIN` (default 6) high-value same-site URLs, one per kind
where possible.

Sources, in order:
1. **Seeds:** the homepage.
2. **Sitemap:** read `robots.txt` for `Sitemap:` lines, else try `/sitemap.xml`. Fetch with
   `httpx` (fast, no browser), handle sitemap indexes (max depth 1, max 3 child sitemaps).
   Sitemaps can be huge (Postman's docs/blog): only keep URLs with path depth ≤ 2.
3. **Anchors:** all `<a href>` from the rendered homepage, resolved to absolute, same
   registrable domain (allow `www.` and subdomains like `about.x.com`), fragments stripped.
4. **Guesses:** `/about`, `/company`, `/team`, `/contact`, `/pricing` — only if nothing of
   that kind was found; a 404 here is recorded as `not_found`, not an error.

Scoring (kind, weight), match against path and anchor text, lowercase:

| kind | keywords |
|---|---|
| leadership | leadership, founders, executives, management-team, leadership-team, our-leadership, exec-team |
| team | team, people, our-team |
| about | about, about-us, story, mission |
| company | company, careers? (low weight) |
| contact | contact, support, help, sales |
| pricing | pricing, plans |

Bare `management` was tried and dropped during M1 (16 Sep): it false-positives hard on
product-marketing sites that sell "X management" features (vapi.ai's own
`/custom-agents/*-management-agent` pages outscored every real leadership candidate in
the M1 smoke test — all 6 picked slots were junk). Only compound, unambiguous forms
(`management-team`, `leadership-team`, ...) are kept.

Penalise: `/blog/`, `/docs/`, `/changelog`, `/legal`, `/terms`, `/privacy`, language
prefixes (`/de/`, `/ja/`), query strings, file extensions (`.pdf`, `.png`).
Pick best-scoring URL per kind, then fill remaining slots by score. Dedupe by normalised path.

Also collect from **every** fetched page (not just home):
- `mailto:` hrefs and regex emails → `candidate_emails` (with `source_url`);
- `linkedin.com/in/` and `linkedin.com/company/` hrefs with anchor text / nearest heading →
  `linkedin_links`.

Email regex post-filter: drop matches ending in image/asset extensions (`@2x.png`),
`example.com`, `sentry`, `wixpress`, hashes, and emails whose domain is unrelated to the
target unless found in a `mailto:`.

## Cleaning (`cleaner.py`)

1. BeautifulSoup (`lxml`) remove: `script, style, noscript, svg, canvas, iframe, form,
   header nav, nav, footer, [aria-hidden=true]`, cookie banners (class/id contains
   `cookie`, `consent`, `gdpr`).
   Exception: keep footer text **only** for extracting emails/links (done before removal).
2. Convert with `trafilatura.extract(html, output_format="markdown", include_links=True,
   favor_recall=True)`. If it returns < 200 chars (common on SPA team pages built from
   cards), fall back to `soup.get_text("\n")` with whitespace collapsed.
3. Collapse repeated blank lines, dedupe identical lines across pages (menus that survived).
4. Truncate to `MAX_CHARS_PER_PAGE`; prioritise pages by kind (leadership/team/about first).
5. Record `raw_tokens` (on raw HTML) and `clean_tokens`. Use a cheap approximation
   (`len(text)/4`) or `tiktoken` if installed; state which in README. Log total reduction %.

Team pages with card grids: before cleaning, also extract structured candidates
(elements with an `h3/h4` name + nearby short role text + optional linkedin href). Pass
them to the LLM as a small `TEAM CARDS` block; it's high-signal and cheap.
