# Lead Enrichment Agent

Autonomous Lead Enrichment Agent (take-home for SoftwareBrio). Full write-up — what/why,
architecture diagram, sample output, design decisions, limitations — lands at M7 (Ship).
This section only covers setup; see `AGENTS.md` for the full command reference.

## Setup

Requires Python **>= 3.11** (`browser-use` requirement).

### With `uv` (preferred)

```bash
uv sync                        # creates/updates .venv, editable-installs this project
uv run playwright install chromium
cp .env.example .env           # then fill in your API keys
```

### Without `uv` (plain `pip` fallback)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env           # then fill in your API keys
```

Either way, **never** run a non-editable install of this project (`pip install .` /
`uv pip install .` without `-e`) — see `docs/references/stack.md`'s Gotchas for why
that's actively harmful here (it collides with an unrelated real PyPI package that
happens to share our top-level import name).

## Run

```bash
uv run python -m enrich postman.com supabase.com vapi.ai --out output.json
# or, without uv:
python -m enrich postman.com supabase.com vapi.ai --out output.json
```

## Checks

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```
