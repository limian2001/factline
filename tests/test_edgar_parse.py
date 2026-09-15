"""Parsing must accept good rows and explain the bad ones, never drop silently."""

from __future__ import annotations

from datetime import date

from factline.clients.edgar import EdgarClient
from factline.models.edgar import FactRow


def test_parses_flow_and_instant_facts(companyfacts):
    result = EdgarClient.parse_company_facts(companyfacts, "AAPL")

    assert result.cik == 320193
    assert result.entity_name == "Apple Inc."
    assert result.accepted_count == 5  # 3 Revenues + 1 Assets + 1 dei

    revenues = [r for r in result.rows if r.tag == "Revenues"]
    assert len(revenues) == 3
    assert all(not r.is_instant for r in revenues)

    assets = next(r for r in result.rows if r.tag == "Assets")
    assert assets.is_instant, "balance-sheet facts have no start date"
    assert assets.end == date(2024, 9, 28)


def test_quarantines_bad_rows_with_reasons(companyfacts):
    result = EdgarClient.parse_company_facts(companyfacts, "AAPL")

    reasons = {q.tag: q.reason for q in result.quarantined}
    assert "BrokenFiledBeforePeriodEnd" in reasons
    assert "precedes period end" in reasons["BrokenFiledBeforePeriodEnd"]
    assert "SomeConcept" in reasons
    assert "unknown taxonomy" in reasons["SomeConcept"]

    # Quarantined records keep their payload so they can be diagnosed later.
    assert all(q.payload for q in result.quarantined)
    assert 0 < result.quarantine_rate < 0.5


def test_citation_is_human_readable(companyfacts):
    result = EdgarClient.parse_company_facts(companyfacts, "AAPL")
    assets = next(r for r in result.rows if r.tag == "Assets")
    assert assets.citation == "10-K 2024-11-01 (0000320193-24-000123)"


def test_cik_padding():
    assert EdgarClient.pad_cik(320193) == "CIK0000320193"
    assert EdgarClient.pad_cik(1018724) == "CIK0001018724"


def test_fact_row_rejects_future_dated_filing():
    """A filing cannot predate its own reporting period."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="precedes period end"):
        FactRow(
            cik=1,
            ticker="X",
            taxonomy="us-gaap",
            tag="Revenues",
            unit="USD",
            val=1.0,
            start=date(2024, 1, 1),
            end=date(2024, 12, 31),
            filed=date(2024, 6, 30),
            accn="a",
            form="10-K",
        )
