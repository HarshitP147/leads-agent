# Fetching, Discovery, Cleaning

## Fetching (`fetcher.py`)

- Async Playwright, Chromium, `headless` from settings. One browser, one context per domain.
- Context: realistic desktop UA, `viewport 1366x900`, `locale en-US`, block heavy resources
  via `context.route` (images, media, fonts). Keep stylesheets off too; we don't render visually.
- `page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_S*1000)`, then settle
  (below), then a short scroll to trigger lazy content.
- Capture: final URL (after redirects), HTTP status from the response, `page.content()`, title.
- Retries: `tenacity`, 2 retries, exponential backoff with jitter, only on network errors /
  navigation timeouts / 429 / 5xx. Never retry 404, and never retry a DNS resolution
  failure (permanent — the domain doesn't exist, retrying can't fix that).
- For a 429, respect `Retry-After` if present (cap at 10 s).
- Normalise input: strip scheme/paths, try `https://{domain}` then `https://www.{domain}`.
  (CLI-level normalisation — full URL -> bare host, IP/localhost rejection — happens
  earlier, in `cli.normalize_domain`; see resilience.md, "Input hygiene".)

### Cross-domain redirects (e.g. `twitter.com` -> `x.com`)

If the homepage navigation lands on a different registrable domain than requested,
`fetch_home._redirect_update` adopts the final domain as `state["domain"]` and logs
`fetch_home:redirected <old>-><new>` to `route_log`. This matters because
`discover_links`'s same-site filter and `fetch_subpages`'s guessed URLs both read
`state["domain"]` — without this, a legitimately redirected site's own homepage anchors
would be filtered out as "off-site" against the stale, pre-redirect domain. A plain
`www.` redirect is not treated as a domain change (registrable host is compared after
stripping `www.`). Known trade-off: `fetch_subpages` still keys its browser context
cache by the (now-stale) original domain, so a redirect costs one extra `BrowserContext`
for the rest of that domain's fetches — harmless, just not maximally efficient (see
tech-debt.md).

### Settling the page (16 Sep, profiling follow-up)

The old design waited on `page.wait_for_load_state("networkidle", timeout=5000)` alone.
Profiling `baseten.co`/`vapi.ai` (`--debug`'s per-page `PROFILE` log) showed this was
**63-65% of total per-domain fetch time**, because modern marketing sites never truly go
network-idle — analytics beacons and chat widgets keep polling — so almost every page
paid close to the full 5 s, not the rare fast case.

Now `fetcher._settle_page` races two things, capped at `NETWORKIDLE_CAP_S = 2.0`:
- the native `networkidle` event, and
- a stable-text poll (`_wait_for_stable_text`): every `STABLE_TEXT_POLL_S = 0.25` s,
  read `document.body.innerText.length`; done once it's `> STABLE_TEXT_MIN_CHARS = 1000`
  and unchanged across two consecutive polls.

Whichever finishes first wins; the loser is cancelled. `--debug` logs which one fired
(`exit=idle` / `exit=stable_text` / `exit=cap`) per page, alongside `goto=`/`scroll=`
timings — see the measured before/after in build-plan.md's Progress Log (16 Sep):
`vapi.ai` 37.8s → 14.5s, `baseten.co` 43.1s → 17.3s (both ~60% faster). In practice
almost every page now exits via `stable_text` in under ~1.1s; only pages whose text
kept changing throughout (still-animating content) hit the 2 s cap, which is still
better than the old flat 5 s regardless.
- Politeness jitter between subpages: `random.uniform(0.2, 0.6)` (was `0.5-1.5`) — still
  staggered, no longer the second-largest cost in the profile (was 15-17% of total time).

### Bot-wall detection

Mark `status="blocked"` when any holds:
- HTTP 403/503 **and** body contains challenge markers
  (`cf-challenge`, `challenge-platform`, `Just a moment`, `Attention Required`,
  `captcha`, `px-captcha`, `Access Denied`);
- title matches those markers and visible text < 300 chars.

On blocked: wait 3–5 s once and reload (JS challenges sometimes auto-resolve). If still
blocked, record `ErrorRecord(kind="bot_wall")` and continue with whatever else we have.
**We do not attempt CAPTCHA solving or stealth plugins.** State this in the README.

The 300-char "visible text" check strips `<script>`/`<style>` *bodies*, not just tags
(`fetcher._visible_text_len`) — a Cloudflare-style interstitial that returns HTTP 200
(a JS-redirect challenge, not a 403/503) is mostly obfuscated inline JS, which survived
tag-only stripping and could push the visible-length count well past 300 even though a
human would see almost nothing. Only stripping tags undercounted the challenge as a
normal page in that case.

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

Kind keywords match the **last path segment** primarily (plus anchor text). An earlier
segment containing `team` is not enough: `/custom-agents/sales-team-agent` is not
`kind="team"`; `/about/team` is.

Penalise (score discount; score ≤ 0 drops the kind): `/legal`, `/terms`, `/privacy`,
query strings, and product-ish path segments `/custom-agents/`, `/solutions/`,
`/use-cases/`, `/integrations/`. Pick best-scoring URL per kind, then fill remaining
slots by score. Dedupe by normalised path.

### Collection sections — drop children, keep the index as filler (16 Sep, M1 review)

`COLLECTION_SECTIONS` (a constant in `discovery.py`): `blog`, `docs`, `changelog`,
`news`, `press`, `customers`, `careers`, `jobs`, `guides`, `tutorials`, `resources`,
`learn`, `events`. These are index sections whose *children* are content items, not
company-info pages — a blog post or a customer case study is not a "company" or "about"
page even if a stray keyword collides (this generalises the M1 smoke-test fix where
`"story"` matched supabase.com's "Read the story →" customer-card CTAs).

Rule, applied **before scoring**, to every source (sitemap, anchors, later the Browser
Use agent too):
- Take the URL's path segments; if the first segment is a 2-letter (or 2-letter-2-letter)
  locale code (`/en/...`, `/en-us/...`), skip it and use the *next* segment instead —
  `/en/blog/x` counts as a `blog` child, not an `en` anything.
- If that (locale-adjusted) first segment is in `COLLECTION_SECTIONS` **and** the path
  has more than one segment after it (i.e. it's a child, not the section root) → **drop
  the URL entirely**, before it ever reaches keyword scoring. Not a penalty, not a low
  score — it never becomes a candidate.
- If the path is *exactly* the section root (`/blog`, `/en/docs`) → keep it as a
  candidate, but only as `kind="other"` at a fixed low score (0.5) — filler for when
  nothing better exists, never able to outrank a real about/team/pricing/contact hit
  (the lowest real kind score is `pricing`/`contact` at weight 2.0, and even a
  worst-case-discounted single-word match rarely drops below 0.5, so this only wins an
  otherwise-empty slot).

Sitemap indexes get the same treatment one level up: a **child sitemap** (e.g.
`/docs/sitemap.xml`, `/sitemap-blog.xml`) whose URL contains a collection-section word is
never fetched at all — Postman's and Supabase's sitemaps are dominated by
docs/blog/customer-story children, and walking those sitemaps just to filter every URL
back out afterward wastes the one HTTP round-trip per child that the depth-1 cap
(`max 3 child sitemaps`) is trying to keep cheap. `--debug` logs each skipped child
sitemap with its reason.

### `--debug` candidate audit trail

Every URL discovery looks at — not just the ones that made the final cut — is worth
seeing when tuning the scorer. `discover_links` returns a full list of considered links
(url, kind, score, source, decision: `selected` / `dropped` / `skipped`, and a reason for
anything not selected). `cli.py` prints this as a table per domain under `--debug`, right
alongside the pages-picked table.

`discovery.py` deliberately does **not** record a per-URL reason for every generic drop
(off-site, blocked-extension, too-deep, unclassified) coming out of a sitemap — a real
sitemap can list thousands of URLs, and auditing all of them would turn `--debug` into a
sitemap dump. Only the sitemap loop's collection-child drops are recorded (that's what
this rule is about), and `cli.py` aggregates *those* by reason into one row with a count
(e.g. `collection-child:/blog (1203 URLs)`) rather than listing each one — vercel.com's
real sitemap has 4000+ such children. Anchor-derived candidates (bounded to one
homepage's links) are still itemized individually; that volume is small enough to read.

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
