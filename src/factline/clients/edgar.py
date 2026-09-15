"""SEC EDGAR client.

EDGAR is the backbone of this project and deliberately the only fundamentals
source. A vendor's pre-normalised fundamentals would be easier to consume and
would also destroy the two properties the whole design rests on: the accession
number that lets a report cite its source, and the filing date that makes
point-in-time correctness possible.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import ValidationError

from factline.clients.base import DiskCache, JSONAPIClient
from factline.config import Settings
from factline.models.edgar import (
    CompanyFactsParseResult,
    FactRow,
    QuarantinedRecord,
)

log = structlog.get_logger(__name__)

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
DATA_BASE = "https://data.sec.gov"


class EdgarClient:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self.settings = settings
        self.http = JSONAPIClient(
            base_url=DATA_BASE,
            headers={
                # The SEC requires a contact header and returns 403 without it.
                "User-Agent": settings.sec_user_agent,
                "Accept-Encoding": "gzip, deflate",
                # No Host header here on purpose. This client talks to two hosts:
                # data.sec.gov for the XBRL APIs and www.sec.gov for the ticker
                # map. Pinning Host to either one makes requests to the other
                # return 404, because the server resolves the path against the
                # Host it was given rather than the host we connected to.
                # httpx derives Host from each URL, which is what we want.
            },
            rate_limit_rps=settings.sec_rate_limit_rps,
            cache=DiskCache(settings.cache_dir, settings.cache_ttl_hours),
            client=client,
        )
        self._ticker_to_cik: dict[str, int] | None = None

    def close(self) -> None:
        self.http.close()

    # ------------------------------------------------------------------ CIK

    def ticker_to_cik(self, ticker: str) -> int:
        """Resolve a ticker to its CIK.

        EDGAR is keyed by CIK, not ticker, and the mapping file is the only
        authoritative source. It is cached for 24h like everything else.
        """
        if self._ticker_to_cik is None:
            payload = self.http.get_json(TICKER_MAP_URL)
            # Shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
            self._ticker_to_cik = {
                str(entry["ticker"]).upper(): int(entry["cik_str"]) for entry in payload.values()
            }
            log.info("edgar.ticker_map_loaded", count=len(self._ticker_to_cik))

        key = ticker.upper()
        if key not in self._ticker_to_cik:
            raise KeyError(f"ticker {ticker!r} not found in SEC ticker map")
        return self._ticker_to_cik[key]

    @staticmethod
    def pad_cik(cik: int) -> str:
        """EDGAR paths want a zero-padded 10-digit CIK."""
        return f"CIK{cik:010d}"

    # ------------------------------------------------------------ companyfacts

    def fetch_company_facts(self, cik: int) -> dict[str, Any]:
        return self.http.get_json(f"/api/xbrl/companyfacts/{self.pad_cik(cik)}.json")

    def fetch_submissions(self, cik: int) -> dict[str, Any]:
        """Filing index for a company — used later to pick up 8-K material events."""
        return self.http.get_json(f"/submissions/{self.pad_cik(cik)}.json")

    # ----------------------------------------------------------------- parse

    @staticmethod
    def parse_company_facts(payload: dict[str, Any], ticker: str) -> CompanyFactsParseResult:
        """Flatten companyfacts into validated rows plus a quarantine list.

        The nesting is: facts -> taxonomy -> tag -> units -> unit -> [observations].
        Every observation becomes one FactRow. Anything that fails validation is
        recorded with its reason rather than dropped.
        """
        cik = int(payload["cik"])
        result = CompanyFactsParseResult(
            cik=cik,
            ticker=ticker.upper(),
            entity_name=str(payload.get("entityName", "")).strip(),
        )

        for taxonomy, tags in (payload.get("facts") or {}).items():
            for tag, tag_body in (tags or {}).items():
                for unit, observations in ((tag_body or {}).get("units") or {}).items():
                    for obs in observations or []:
                        try:
                            result.rows.append(
                                FactRow(
                                    cik=cik,
                                    ticker=ticker.upper(),
                                    taxonomy=taxonomy,
                                    tag=tag,
                                    unit=unit,
                                    val=obs["val"],
                                    start=obs.get("start"),
                                    end=obs["end"],
                                    filed=obs["filed"],
                                    accn=obs["accn"],
                                    form=obs["form"],
                                    fy=obs.get("fy"),
                                    fp=obs.get("fp"),
                                    frame=obs.get("frame"),
                                )
                            )
                        except (ValidationError, KeyError, TypeError) as exc:
                            result.quarantined.append(
                                QuarantinedRecord(
                                    cik=cik,
                                    ticker=ticker.upper(),
                                    taxonomy=taxonomy,
                                    tag=tag,
                                    unit=unit,
                                    reason=str(exc).replace("\n", " ")[:500],
                                    payload=obs if isinstance(obs, dict) else {"raw": obs},
                                )
                            )

        log.info(
            "edgar.parsed",
            ticker=ticker,
            accepted=result.accepted_count,
            quarantined=len(result.quarantined),
            quarantine_rate=round(result.quarantine_rate, 5),
        )
        return result
