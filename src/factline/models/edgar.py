"""Validated shapes for SEC EDGAR payloads.

The whole point of this layer is that nothing reaches the data lake unvalidated.
A record that fails validation goes to quarantine with a reason attached; it is
never silently dropped, because "we lost 4% of the rows and nobody noticed" is
the failure mode that makes a research system untrustworthy.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Taxonomies that carry fundamentals. These are what the fact table is for.
FUNDAMENTAL_TAXONOMIES = frozenset({"us-gaap", "ifrs-full", "dei", "srt", "invest"})

# Real SEC taxonomies that are simply not fundamentals. They are skipped, and
# counted separately from errors, because calling them "unknown taxonomy"
# failures made JPMorgan look like it had a 6.8% data quality problem when in
# fact it just issues a lot of structured notes.
#
#   ffd  Filing Fee Disclosure -- fee tables in 424B2 prospectus supplements
#   ecd  Executive Compensation Disclosure -- pay-versus-performance in DEF 14A
#   rr / oef / cef / vip        fund and insurance product disclosures
#   country / currency / exch / stpr / naics / sic   SEC reference dimensions
#
# A noisy quality metric is a metric nobody reads, so out-of-scope data must not
# be counted as bad data.
OUT_OF_SCOPE_TAXONOMIES = frozenset(
    {
        "ffd",
        "ecd",
        "rr",
        "oef",
        "cef",
        "vip",
        "country",
        "currency",
        "exch",
        "stpr",
        "naics",
        "sic",
    }
)


class FactRow(BaseModel):
    """One observation of one concept, flattened for columnar storage.

    `accn` and `filed` are the two fields that make this project work:
      - accn  -> the citation. Every number in a report points at a real filing.
      - filed -> the point-in-time key. A metric as-of date D may only use rows
                 whose `filed` <= D, because that is when the number became public.
    """

    model_config = ConfigDict(frozen=True)

    cik: int
    ticker: str
    taxonomy: str
    tag: str
    unit: str
    val: float
    # `start` is absent for instantaneous facts (balance-sheet items).
    start: date | None = None
    end: date
    filed: date
    accn: str
    form: str
    fy: int | None = None
    fp: str | None = None
    frame: str | None = None

    # True when the fact describes a period that had not ended when it was filed:
    # next year's debt maturities, forecast amortisation, guidance. These are
    # legitimate disclosures, not corrupt rows -- but treating a forecast as an
    # actual is a serious analytical error, so the distinction is carried in the
    # schema rather than left for a downstream query to remember.
    is_forward_looking: bool = False

    @field_validator("taxonomy")
    @classmethod
    def _is_fundamental_taxonomy(cls, v: str) -> str:
        if v not in FUNDAMENTAL_TAXONOMIES:
            raise ValueError(f"non-fundamental taxonomy {v!r}")
        return v

    @model_validator(mode="before")
    @classmethod
    def _derive_forward_looking(cls, data: Any) -> Any:
        """Flag facts filed before their period ended, instead of rejecting them."""
        if isinstance(data, dict) and "is_forward_looking" not in data:
            filed, end = data.get("filed"), data.get("end")
            if filed is not None and end is not None:
                data = {**data, "is_forward_looking": str(filed) < str(end)}
        return data

    @field_validator("val")
    @classmethod
    def _finite(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError("val must be finite")
        return v

    @field_validator("unit", "tag", "accn", "form")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def _period_is_coherent(self) -> FactRow:
        # A period that ends before it starts is genuinely corrupt. A filing that
        # predates its period end is NOT -- that is a forecast (next year's debt
        # maturities, guidance), and it is flagged rather than rejected.
        #
        # Point-in-time correctness is unaffected either way: `filed <= as_of`
        # remains the right test, because a forecast *was* public on the day it
        # was filed. What matters is that downstream can tell the two apart,
        # which is what `is_forward_looking` is for.
        if self.start is not None and self.start > self.end:
            raise ValueError(f"start {self.start} after end {self.end}")
        return self

    @property
    def is_instant(self) -> bool:
        """Balance-sheet style fact (a stock, not a flow)."""
        return self.start is None

    @property
    def citation(self) -> str:
        """Human-readable provenance, e.g. '10-K 2024-11-01 (0000320193-24-000123)'."""
        return f"{self.form} {self.filed.isoformat()} ({self.accn})"


class QuarantinedRecord(BaseModel):
    """A record we refused, kept with enough context to diagnose it later."""

    cik: int
    ticker: str
    taxonomy: str
    tag: str
    unit: str
    reason: str
    payload: dict[str, Any]


class CompanyFactsParseResult(BaseModel):
    """Outcome of parsing one companyfacts document.

    Three buckets, not two. Lumping "we do not analyse executive pay tables" in
    with "this row is corrupt" produced a 6.8% quality alarm for JPMorgan that
    was entirely noise -- and a quality metric that cries wolf is one nobody
    reads, which is worse than having none.
    """

    cik: int
    ticker: str
    entity_name: str
    rows: list[FactRow] = Field(default_factory=list)
    quarantined: list[QuarantinedRecord] = Field(default_factory=list)
    # taxonomy -> count of facts skipped as out of scope. Counted, never stored.
    out_of_scope: dict[str, int] = Field(default_factory=dict)

    @property
    def accepted_count(self) -> int:
        return len(self.rows)

    @property
    def out_of_scope_count(self) -> int:
        return sum(self.out_of_scope.values())

    @property
    def forward_looking_count(self) -> int:
        return sum(1 for r in self.rows if r.is_forward_looking)

    @property
    def quarantine_rate(self) -> float:
        """Errors as a share of in-scope facts. Out-of-scope data is excluded:
        it is not an error, and including it would swamp the signal."""
        total = len(self.rows) + len(self.quarantined)
        return len(self.quarantined) / total if total else 0.0
