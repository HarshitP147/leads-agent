"""Typer CLI: concurrency, writing outputs, summary table.

See docs/ARCHITECTURE.md (top-level shape) and docs/design-docs/resilience.md
(domain-level / run-level wrappers).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from enrich import fetcher, pipeline
from enrich.config import get_settings
from enrich.logging_setup import configure_logging
from enrich.models import ConfidenceBreakdown, DomainResult, ErrorRecord, Usage

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()
logger = logging.getLogger(__name__)


async def run_domain(
    domain: str,
    *,
    domain_timeout_s: int,
    debug: bool,
    debug_sink: dict[str, dict[str, list]] | None = None,
) -> DomainResult:
    """Domain-level timeout wrapper around `pipeline.run_pipeline`. The pipeline itself
    already turns stage exceptions into a `failed` DomainResult (see pipeline.py); this
    wrapper exists for the one thing the pipeline can't catch on its own — running out
    of time — and as the last line of defence from docs/design-docs/resilience.md."""
    settings = get_settings()
    try:
        return await asyncio.wait_for(
            pipeline.run_pipeline(domain, settings, debug=debug, debug_sink=debug_sink),
            timeout=domain_timeout_s,
        )
    except TimeoutError:
        logger.error("domain %s failed: timeout after %ss", domain, domain_timeout_s)
        return _failed_result(
            domain, kind="timeout", message=f"exceeded {domain_timeout_s}s"
        )
    except Exception as exc:
        if debug:
            logger.exception("domain %s: escaped pipeline.run_pipeline", domain)
        else:
            logger.error("domain %s failed: %s: %s", domain, type(exc).__name__, exc)
        return _failed_result(domain, kind="internal", message=str(exc))


def _failed_result(domain: str, *, kind: str, message: str) -> DomainResult:
    return DomainResult(
        domain=domain,
        status="failed",
        profile=None,
        confidence=ConfidenceBreakdown(score=0.0, components={}),
        pages=[],
        errors=[ErrorRecord(stage="cli", kind=kind, message=message)],
        usage=Usage(),
        duration_s=0.0,
        scraped_at=datetime.now(UTC).isoformat(),
    )


async def run_all(
    domains: list[str], *, out: Path, domain_timeout_s: int, debug: bool
) -> tuple[list[DomainResult], dict[str, dict[str, list]]]:
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.max_concurrent_domains)
    results: list[DomainResult] = []
    debug_sink: dict[str, dict[str, list]] = {}

    async def _one(domain: str) -> None:
        async with semaphore:
            result = await run_domain(
                domain,
                domain_timeout_s=domain_timeout_s,
                debug=debug,
                debug_sink=debug_sink if debug else None,
            )
            results.append(result)
            # Incremental write: a Ctrl-C mid-run still leaves useful output.
            out.write_text(json.dumps([r.model_dump() for r in results], indent=2))

    try:
        await asyncio.gather(*(_one(d) for d in domains), return_exceptions=True)
    finally:
        await fetcher.close_browser()
    return results, debug_sink


def _token_totals(result: DomainResult) -> tuple[int, int]:
    raw = sum(p.raw_tokens for p in result.pages if p.raw_tokens is not None)
    clean = sum(p.clean_tokens for p in result.pages if p.clean_tokens is not None)
    return raw, clean


def _reduction_pct(raw: int, clean: int) -> str:
    if raw <= 0:
        return "-"
    return f"{(1 - clean / raw) * 100:.0f}%"


def _print_summary(results: list[DomainResult], *, wall_time_s: float) -> None:
    """Per-domain rows, then a TOTAL row. Each domain's `duration` is that domain's own
    isolated wall-clock time (verified: an artificially slow 3s stub reports 3.0s, a 1s
    stub reports 1.0s, run concurrently) — it is not cumulative. The TOTAL row's time is
    the *run's* wall-clock time, not `sum(duration)`, precisely because domains overlap
    under `MAX_CONCURRENT_DOMAINS`; summing the column would double-count that overlap
    and look like the exact "cumulative" artifact this table is trying to avoid.

    `raw tok`/`clean tok`/`reduction %` are cleaner.py's token counts (`raw` on the
    original HTML, `clean` on the truncated markdown) — the evidence for the "no raw
    HTML to the LLM" requirement (AGENTS.md #4)."""
    table = Table(title="Enrichment summary")
    for col in (
        "domain",
        "status",
        "confidence",
        "pages",
        "raw tok",
        "clean tok",
        "reduction",
        "in tok",
        "out tok",
        "duration",
    ):
        table.add_column(col)
    table.add_column("est $", min_width=12, no_wrap=True)

    total_raw = total_clean = 0
    for i, r in enumerate(results):
        raw, clean = _token_totals(r)
        total_raw += raw
        total_clean += clean
        table.add_row(
            r.domain,
            r.status,
            f"{r.confidence.score:.2f}",
            str(len(r.pages)),
            str(raw),
            str(clean),
            _reduction_pct(raw, clean),
            str(r.usage.input_tokens),
            str(r.usage.output_tokens),
            f"{r.duration_s:.1f}s",
            f"${r.usage.est_cost_usd:.6f}",
            end_section=(i == len(results) - 1),
        )
    table.add_row(
        "TOTAL",
        "",
        "",
        str(sum(len(r.pages) for r in results)),
        str(total_raw),
        str(total_clean),
        _reduction_pct(total_raw, total_clean),
        str(sum(r.usage.input_tokens for r in results)),
        str(sum(r.usage.output_tokens for r in results)),
        f"{wall_time_s:.1f}s",
        f"${sum(r.usage.est_cost_usd for r in results):.6f}",
        style="bold",
    )
    console.print(table)
    console.print(
        "[dim]TOTAL duration is the run's wall-clock time, not the sum of the rows "
        "above — domains run concurrently (MAX_CONCURRENT_DOMAINS), so summing would "
        "double-count the overlap.[/dim]"
    )


def _print_debug_pages(results: list[DomainResult]) -> None:
    """Debug view: the pages each domain's fetch+discover actually picked."""
    for r in results:
        table = Table(title=f"{r.domain} — pages")
        for col in ("url", "kind", "discovered_by", "status", "http_status"):
            table.add_column(col)
        for p in r.pages:
            table.add_row(p.url, p.kind, p.discovered_by, p.status, str(p.http_status))
        console.print(table)


_AGGREGATE_REASON_PREFIXES = ("collection-child:", "collection-section-sitemap:")


def _print_debug_candidates(debug_sink: dict[str, dict[str, list]]) -> None:
    """Debug view: every URL discovery considered, not just the winners — selected,
    dropped, or skipped, each with a reason. See discovery-and-cleaning.md, "--debug
    candidate audit trail" (16 Sep, M1 review).

    Collection-child / collection-section-sitemap drops are aggregated by reason
    (one row, with a count) rather than listed individually — a real site's sitemap can
    have thousands of blog/docs/changelog children, and a wall of near-identical rows
    would defeat the point of an *audit* view. Everything else is itemized."""
    for domain, payload in debug_sink.items():
        considered = payload.get("considered_links", [])
        aggregated: dict[str, int] = {}
        individual = []
        for c in considered:
            if c.reason and c.reason.startswith(_AGGREGATE_REASON_PREFIXES):
                aggregated[c.reason] = aggregated.get(c.reason, 0) + 1
            else:
                individual.append(c)

        table = Table(title=f"{domain} — candidates considered")
        for col in ("url", "kind", "score", "source", "decision", "reason"):
            table.add_column(col)
        for reason, count in aggregated.items():
            decision = "skipped" if "sitemap" in reason else "dropped"
            table.add_row(
                f"({count} URLs)", "-", "-", "sitemap", decision, reason, style="dim"
            )
        for c in individual:
            row_style = "green" if c.decision == "selected" else "dim"
            table.add_row(
                c.url,
                c.kind or "-",
                f"{c.score:.2f}" if c.score is not None else "-",
                c.source,
                c.decision,
                c.reason or "",
                style=row_style,
            )
        console.print(table)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url).strip("/")
    slug = _SLUG_RE.sub("-", path.lower()).strip("-")
    return slug or "home"


def _write_debug_cleaned_pages(debug_sink: dict[str, dict[str, list]]) -> None:
    """Writes `debug/<domain>/<kind>-<slug>.md` with each page's cleaned markdown."""
    for domain, payload in debug_sink.items():
        cleaned = payload.get("cleaned", [])
        if not cleaned:
            continue
        domain_dir = Path("debug") / domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        for page in cleaned:
            path = domain_dir / f"{page.kind}-{_slugify(page.url)}.md"
            path.write_text(
                f"<!-- {page.url} — raw_tokens={page.raw_tokens} "
                f"clean_tokens={page.clean_tokens} -->\n\n{page.markdown}"
            )
        console.print(
            f"[dim]wrote {len(cleaned)} cleaned page(s) to {domain_dir}/[/dim]"
        )


@app.command()
def main(
    domains: list[str] = typer.Argument(  # noqa: B008
        ..., help="Company domains to enrich, e.g. postman.com"
    ),
    out: Path = typer.Option(  # noqa: B008
        Path("output.json"), "--out", help="Where to write the JSON result."
    ),
    timeout: int | None = typer.Option(
        None,
        "--timeout",
        help="Per-domain timeout in seconds (default: DOMAIN_TIMEOUT_S).",
    ),
    max_pages: int | None = typer.Option(
        None,
        "--max-pages",
        help="Override MAX_PAGES_PER_DOMAIN for this run.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Full tracebacks + a pages-picked table + a candidates-considered table.",
    ),
) -> None:
    """Enrich a list of company domains into a structured, verified profile per domain."""
    configure_logging(debug=debug)
    if max_pages is not None:
        os.environ["MAX_PAGES_PER_DOMAIN"] = str(max_pages)
    settings = get_settings()
    domain_timeout_s = timeout or settings.domain_timeout_s
    wall_start = time.monotonic()
    results, debug_sink = asyncio.run(
        run_all(domains, out=out, domain_timeout_s=domain_timeout_s, debug=debug)
    )
    wall_time_s = time.monotonic() - wall_start
    if debug:
        _print_debug_pages(results)
        _print_debug_candidates(debug_sink)
        _write_debug_cleaned_pages(debug_sink)
    _print_summary(results, wall_time_s=wall_time_s)


if __name__ == "__main__":
    app()
