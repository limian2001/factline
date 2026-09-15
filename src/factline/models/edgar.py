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

# Taxonomies we accept. Anything else is quarantined rather than guessed at.
KNOWN_TAXONOMIES = frozenset({"us-gaap", "ifrs-full", "dei", "srt", "invest"})


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

    @field_validator("taxonomy")
    @classmethod
    def _known_taxonomy(cls, v: str) -> str:
        if v not in KNOWN_TAXONOMIES:
            raise ValueError(f"unknown taxonomy {v!r}")
        return v

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
    def _filed_after_period_end(self) -> FactRow:
        # A filing cannot predate the period it reports on. When this trips it is
        # real corrupt data, and treating it as valid would let future information
        # leak into a point-in-time query.
        if self.filed < self.end:
            raise ValueError(
                f"filed {self.filed} precedes period end {self.end} ({self.tag}, accn={self.accn})"
            )
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
    """Outcome of parsing one companyfacts document."""

    cik: int
    ticker: str
    entity_name: str
    rows: list[FactRow] = Field(default_factory=list)
    quarantined: list[QuarantinedRecord] = Field(default_factory=list)

    @property
    def accepted_count(self) -> int:
        return len(self.rows)

    @property
    def quarantine_rate(self) -> float:
        total = len(self.rows) + len(self.quarantined)
        return len(self.quarantined) / total if total else 0.0
