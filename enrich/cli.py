"""Typer CLI: concurrency, writing outputs, summary table.

See docs/ARCHITECTURE.md (top-level shape) and docs/design-docs/resilience.md
(domain-level / run-level wrappers).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from enrich.config import get_settings
from enrich.graph import build_graph
from enrich.logging_setup import configure_logging
from enrich.models import ConfidenceBreakdown, DomainResult, ErrorRecord, Usage

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()
logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_domain(
    domain: str, *, domain_timeout_s: int, debug: bool
) -> DomainResult:
    """Run the compiled graph for one domain; never raises. Any exception escaping the
    graph (nodes should already have handled their own) becomes a `failed` DomainResult —
    this is the last line of defence described in docs/design-docs/resilience.md."""
    started = time.monotonic()
    graph = build_graph()
    initial_state = {
        "domain": domain,
        "base_url": f"https://{domain}",
        "started_at": started,
    }
    try:
        await asyncio.wait_for(graph.ainvoke(initial_state), timeout=domain_timeout_s)
        raise NotImplementedError(
            "graph nodes are still stubs — see build-plan.md M1-M5"
        )
    except Exception as exc:
        logger.exception("domain %s failed", domain)
        return DomainResult(
            domain=domain,
            status="failed",
            profile=None,
            confidence=ConfidenceBreakdown(score=0.0, components={}),
            pages=[],
            errors=[ErrorRecord(stage="cli", kind="internal", message=str(exc))],
            usage=Usage(),
            duration_s=time.monotonic() - started,
            scraped_at=_utcnow_iso(),
        )


async def run_all(
    domains: list[str], *, out: Path, domain_timeout_s: int, debug: bool
) -> list[DomainResult]:
    settings = get_settings()
    semaphore = asyncio.Semaphore(settings.max_concurrent_domains)
    results: list[DomainResult] = []

    async def _one(domain: str) -> None:
        async with semaphore:
            result = await run_domain(
                domain, domain_timeout_s=domain_timeout_s, debug=debug
            )
            results.append(result)
            # Incremental write: a Ctrl-C mid-run still leaves useful output.
            out.write_text(json.dumps([r.model_dump() for r in results], indent=2))

    await asyncio.gather(*(_one(d) for d in domains), return_exceptions=True)
    return results


def _print_summary(results: list[DomainResult]) -> None:
    table = Table(title="Enrichment summary")
    for col in (
        "domain",
        "status",
        "confidence",
        "pages",
        "in tok",
        "out tok",
        "est $",
        "duration",
    ):
        table.add_column(col)
    for r in results:
        table.add_row(
            r.domain,
            r.status,
            f"{r.confidence.score:.2f}",
            str(len(r.pages)),
            str(r.usage.input_tokens),
            str(r.usage.output_tokens),
            f"${r.usage.est_cost_usd:.4f}",
            f"{r.duration_s:.1f}s",
        )
    console.print(table)


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
    debug: bool = typer.Option(
        False, "--debug", help="Write cleaned markdown + trace to debug/<domain>/."
    ),
) -> None:
    """Enrich a list of company domains into a structured, verified profile per domain."""
    configure_logging(debug=debug)
    settings = get_settings()
    domain_timeout_s = timeout or settings.domain_timeout_s
    results = asyncio.run(
        run_all(domains, out=out, domain_timeout_s=domain_timeout_s, debug=debug)
    )
    _print_summary(results)


if __name__ == "__main__":
    app()
