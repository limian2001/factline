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
from factline.universe import BY_TICKER, check_cik_drift

log = structlog.get_logger(__name__)


@dataclass
class IngestOutcome:
    ticker: str
    cik: int
    accepted: int
    quarantined: int
    ok: bool
    forward_looking: int = 0
    out_of_scope: int = 0
    error: str | None = None

    @property
    def quarantine_rate(self) -> float:
        """Errors over in-scope facts. Out-of-scope volume is excluded so the
        number stays a usable signal rather than a proxy for how many structured
        notes the company happens to issue."""
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
        # The PINNED cik, not a runtime lookup: see universe.py on why resolving
        # tickers at ingest time silently analyses the wrong company.
        holding = BY_TICKER.get(ticker.upper())
        cik = holding.cik if holding else client.ticker_to_cik(ticker)
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
            forward_looking=parsed.forward_looking_count,
            out_of_scope=parsed.out_of_scope_count,
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
        # Surface any ticker that has changed legal entity since the pins were
        # set. Not fatal -- the pin is deliberate -- but never silent.
        try:
            resolved = {t: client.ticker_to_cik(t) for t in tickers}
            for warning in check_cik_drift(resolved):
                log.warning("universe.cik_drift", detail=warning)
        except Exception as exc:  # noqa: BLE001 - drift check must not block ingest
            log.warning("universe.cik_drift_check_failed", error=str(exc))

        return [ingest_ticker(t, settings, client, lake) for t in tickers]
    finally:
        client.close()
