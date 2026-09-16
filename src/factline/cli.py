"""Command line entry point."""

from __future__ import annotations

import logging
from datetime import date

import structlog
import typer
from rich.console import Console
from rich.table import Table

from factline.config import load_settings
from factline.quality import TickerCoverage, check_coverage
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
    table.add_column("Forward", justify="right")
    table.add_column("Out of scope", justify="right")
    table.add_column("Errors", justify="right")
    table.add_column("Status")
    for o in outcomes:
        # Errors are highlighted only above 1%: below that it is normal EDGAR
        # noise, and colouring it red trains you to ignore the column.
        err = f"{o.quarantined:,}" + (f" ({o.quarantine_rate:.2%})" if o.quarantined else "")
        if o.quarantine_rate > 0.01:
            err = f"[yellow]{err}[/]"
        table.add_row(
            o.ticker,
            str(o.cik) if o.cik > 0 else "—",
            f"{o.accepted:,}",
            f"{o.forward_looking:,}" if o.forward_looking else "—",
            f"[dim]{o.out_of_scope:,}[/]" if o.out_of_scope else "—",
            err,
            "[green]ok[/]" if o.ok else f"[red]{o.error or 'failed'}[/]",
        )
    console.print(table)
    console.print(
        "[dim]Out of scope = real SEC taxonomies that are not fundamentals "
        "(ffd filing fees, ecd executive pay). Skipped, not errors.[/]"
    )

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
    rows = lake.coverage()
    for row in rows:
        table.add_row(row[0], f"{row[1]:,}", f"{row[2]:,}", str(row[3]), str(row[4]), str(row[5]))
    console.print(table)

    # The coverage check runs here rather than in a separate command nobody
    # remembers to run: a check you have to invoke deliberately is a check that
    # is not protecting you.
    findings = check_coverage(
        [
            TickerCoverage(
                ticker=r[0],
                rows=r[1],
                tags=r[2],
                earliest=r[3],
                latest=r[4],
                latest_filing=r[5],
            )
            for r in rows
        ]
    )
    if findings:
        console.print(
            "\n[yellow]Coverage warnings[/] — every record was valid, "
            "but these tickers do not look like their peers:"
        )
        for f in findings:
            console.print(f"  [yellow]•[/] {f}")
    else:
        console.print("\n[green]Coverage check passed[/] — no outliers against peers.")


@app.command()
def resolve(
    ticker: list[str] = typer.Argument(..., help="One or more tickers, e.g. TSLA AVGO"),
) -> None:
    """Look up what to put in universe.py for a ticker, and warn if it looks wrong.

    Adding a name means pasting one line into UNIVERSE, and that line needs a
    CIK. This does the lookup -- and checks the entity's filing history while it
    is there, because the cheapest moment to catch "this ticker now points at a
    reorganisation shell" is before ingesting 20,000 facts from the wrong
    company, not after.
    """
    from factline.clients.edgar import EdgarClient

    settings = load_settings()
    client = EdgarClient(settings)
    try:
        for raw in ticker:
            t = raw.upper()
            console.print()
            try:
                cik = client.ticker_to_cik(t)
            except KeyError:
                console.print(f"  [red]{t}[/] not in the SEC ticker map")
                continue

            sub = client.fetch_submissions(cik)
            recent = sub.get("filings", {}).get("recent", {})
            dates = sorted(recent.get("filingDate") or [])
            fye_raw = str(sub.get("fiscalYearEnd") or "")
            fye = f"{fye_raw[:2]}-{fye_raw[2:]}" if len(fye_raw) == 4 else "??-??"

            console.print(f"  [bold]{t}[/]  {sub.get('name', '?')}")
            console.print(f"  {'CIK':<17}{cik}")
            console.print(f"  {'Fiscal year end':<17}{fye}")
            console.print(f"  {'SIC':<17}{sub.get('sic', '?')} {sub.get('sicDescription', '')}")
            if dates:
                span = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
                console.print(
                    f"  {'Filings':<17}{len(dates):,} "
                    f"(earliest {dates[0]}, latest {dates[-1]}, {span / 365:.1f}y)"
                )

                # The XOM check, applied at the boundary instead of after the fact.
                if span < 365 * 5:
                    console.print(
                        f"\n  [yellow]This entity has only {span / 365:.1f}y of filing "
                        "history.[/] It may be a post-reorganisation holding company, "
                        "with the operating history under a predecessor CIK. Check "
                        f"[dim]https://www.sec.gov/cgi-bin/browse-edgar?company="
                        f"{sub.get('name', '').split()[0]}&action=getcompany[/] "
                        "before adding it."
                    )
            else:
                console.print(f"  [yellow]{'Filings':<17}none found[/]")

            console.print(
                f'\n  [dim]Holding("{t}", {cik}, "{sub.get("name", "")}", '
                f'"TODO-sector", "{fye}"),[/]'
            )
        console.print()
    finally:
        client.close()


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
