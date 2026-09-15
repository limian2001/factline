"""The ingest pipeline: EDGAR -> raw/ -> validate -> clean/ + quarantine/.

Re-runnable end to end. `raw/` is written once and never edited, so the clean
layer can always be deleted and rebuilt without touching the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

from factline.clients.edgar import EdgarClient
from factline.config import Settings
from factline.store.lake import FactLake

log = structlog.get_logger(__name__)


@dataclass
class IngestOutcome:
    ticker: str
    cik: int
    accepted: int
    quarantined: int
    ok: bool
    error: str | None = None

    @property
    def quarantine_rate(self) -> float:
        total = self.accepted + self.quarantined
        return self.quarantined / total if total else 0.0


def _write_raw(raw_dir: Path, ticker: str, payload: dict) -> Path:
    """Land the untouched response. Dated, so history is auditable."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = raw_dir / "companyfacts" / f"date={stamp}"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{ticker.upper()}.json"
    with path.open("w") as fh:
        json.dump(payload, fh)
    return path


def ingest_ticker(
    ticker: str, settings: Settings, client: EdgarClient, lake: FactLake
) -> IngestOutcome:
    try:
        cik = client.ticker_to_cik(ticker)
        payload = client.fetch_company_facts(cik)
        _write_raw(settings.raw_dir, ticker, payload)

        parsed = EdgarClient.parse_company_facts(payload, ticker)
        if parsed.rows:
            lake.write_facts(ticker, parsed.rows)
        lake.write_quarantine(ticker, parsed.quarantined)

        return IngestOutcome(
            ticker=ticker.upper(),
            cik=cik,
            accepted=parsed.accepted_count,
            quarantined=len(parsed.quarantined),
            ok=bool(parsed.rows),
        )
    except Exception as exc:  # noqa: BLE001 - one bad ticker must not kill the run
        log.error("ingest.failed", ticker=ticker, error=str(exc))
        return IngestOutcome(
            ticker=ticker.upper(),
            cik=-1,
            accepted=0,
            quarantined=0,
            ok=False,
            error=str(exc),
        )


def ingest_universe(tickers: list[str], settings: Settings) -> list[IngestOutcome]:
    for d in (settings.raw_dir, settings.clean_dir, settings.quarantine_dir):
        d.mkdir(parents=True, exist_ok=True)

    client = EdgarClient(settings)
    lake = FactLake(settings.clean_dir, settings.quarantine_dir)
    try:
        return [ingest_ticker(t, settings, client, lake) for t in tickers]
    finally:
        client.close()
