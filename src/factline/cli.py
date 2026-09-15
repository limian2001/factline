"""Command line entry point."""

from __future__ import annotations

import logging
from datetime import date

import structlog
import typer
from rich.console import Console
from rich.table import Table

from factline.config import load_settings
from factline.store.lake import FactLake
from factline.universe import TICKERS

app = typer.Typer(add_completion=False, help="factline — filing-grounded equity research")
console = Console()


def _setup_logging(verbose: bool) -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if verbose else logging.INFO
        ),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


@app.command()
def ingest(
    ticker: list[str] = typer.Option(None, "--ticker", "-t", help="Defaults to the full universe."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Pull companyfacts from EDGAR, validate, and land it in the lake."""
    from factline.ingest import ingest_universe

    _setup_logging(verbose)
    settings = load_settings()
    targets = [t.upper() for t in (ticker or TICKERS)]

    console.print(
        f"[bold]Ingesting {len(targets)} tickers[/] at {settings.sec_rate_limit_rps} req/s\n"
    )
    outcomes = ingest_universe(targets, settings)

    table = Table(title="Ingest result", header_style="bold")
    table.add_column("Ticker")
    table.add_column("CIK", justify="right")
    table.add_column("Facts", justify="right")
    table.add_column("Quarantined", justify="right")
    table.add_column("Status")
    for o in outcomes:
        table.add_row(
            o.ticker,
            str(o.cik) if o.cik > 0 else "—",
            f"{o.accepted:,}",
            f"{o.quarantined:,}" + (f" ({o.quarantine_rate:.2%})" if o.quarantined else ""),
            "[green]ok[/]" if o.ok else f"[red]{o.error or 'failed'}[/]",
        )
    console.print(table)

    failed = [o for o in outcomes if not o.ok]
    if failed:
        console.print(f"\n[red]{len(failed)} ticker(s) failed.[/]")
        raise typer.Exit(1)


@app.command()
def coverage() -> None:
    """Show what is actually in the lake — the basis of the data quality report."""
    settings = load_settings()
    lake = FactLake(settings.clean_dir, settings.quarantine_dir)

    table = Table(title="Lake coverage", header_style="bold")
    for col in ("Ticker", "Rows", "Tags", "Earliest", "Latest period", "Latest filing"):
        table.add_column(col, justify="right" if col != "Ticker" else "left")
    for row in lake.coverage():
        table.add_row(row[0], f"{row[1]:,}", f"{row[2]:,}", str(row[3]), str(row[4]), str(row[5]))
    console.print(table)


@app.command("as-of")
def as_of(
    ticker: str = typer.Argument(..., help="e.g. AAPL"),
    on: str = typer.Option(..., "--on", help="Point-in-time date, YYYY-MM-DD"),
    tag: str = typer.Option("Revenues", "--tag", help="XBRL concept, e.g. NetIncomeLoss"),
) -> None:
    """What was knowable about TICKER on a given date. Proves point-in-time correctness."""
    settings = load_settings()
    lake = FactLake(settings.clean_dir, settings.quarantine_dir)
    rows = lake.facts_as_of(ticker, date.fromisoformat(on), tag=tag)

    if not rows:
        console.print(f"[yellow]No {tag} facts for {ticker.upper()} public on {on}.[/]")
        return

    table = Table(title=f"{ticker.upper()} · {tag} · as public on {on}", header_style="bold")
    for col in ("Period start", "Period end", "Value", "Unit", "Filed", "Form", "Citation"):
        table.add_column(col)
    for _tag, unit, start, end, val, filed, accn, form in rows[:20]:
        table.add_row(str(start or "—"), str(end), f"{val:,.0f}", unit, str(filed), form, accn)
    console.print(table)
    console.print(
        "\n[dim]Every row above was already filed on the as-of date. "
        "Restated periods resolve to the latest revision filed by then.[/]"
    )


if __name__ == "__main__":
    app()
