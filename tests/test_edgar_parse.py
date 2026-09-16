"""Parsing must sort facts into three buckets, not two.

The three-way split exists because of a real misclassification: JPMorgan came
back with a 6.8% "data quality problem" that was entirely filing-fee tables from
424B2 prospectus supplements -- real SEC data, just not fundamentals. A quality
metric that cries wolf is one nobody reads.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from factline.clients.edgar import EdgarClient
from factline.models.edgar import FactRow


@pytest.fixture
def parsed(companyfacts):
    return EdgarClient.parse_company_facts(companyfacts, "AAPL")


def test_accepts_in_scope_facts(parsed):
    assert parsed.cik == 320193
    assert parsed.entity_name == "Apple Inc."
    # 3 Revenues + 1 Assets + 1 forward-looking debt maturity + 1 dei
    assert parsed.accepted_count == 6


def test_out_of_scope_taxonomies_are_counted_not_quarantined(parsed):
    """ffd and ecd are real taxonomies we simply do not analyse."""
    assert parsed.out_of_scope == {"ffd": 2, "ecd": 1}
    assert parsed.out_of_scope_count == 3

    quarantined_taxonomies = {q.taxonomy for q in parsed.quarantined}
    assert "ffd" not in quarantined_taxonomies
    assert "ecd" not in quarantined_taxonomies


def test_quarantine_rate_ignores_out_of_scope(parsed):
    """Out-of-scope volume must not inflate the error rate -- that was the bug."""
    # 2 errors out of (6 accepted + 2 errors); the 3 out-of-scope are excluded.
    assert parsed.quarantine_rate == pytest.approx(2 / 8)


def test_genuinely_broken_rows_are_still_quarantined(parsed):
    reasons = {q.tag: q.reason for q in parsed.quarantined}

    assert "CorruptPeriodStartsAfterItEnds" in reasons
    assert "after end" in reasons["CorruptPeriodStartsAfterItEnds"]

    # An unfamiliar taxonomy is quarantined, not skipped: we want to find out
    # about it rather than quietly discard whatever it was.
    assert "SomeConcept" in reasons
    assert "non-fundamental taxonomy" in reasons["SomeConcept"]

    assert all(q.payload for q in parsed.quarantined)


def test_forward_looking_facts_are_kept_and_flagged(parsed):
    """Next year's debt maturities are a real disclosure, not corrupt data."""
    fwd = [r for r in parsed.rows if r.is_forward_looking]
    assert len(fwd) == 1
    assert fwd[0].tag == "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"
    assert fwd[0].filed < fwd[0].end
    assert parsed.forward_looking_count == 1


def test_historical_facts_are_not_flagged_forward_looking(parsed):
    actuals = [r for r in parsed.rows if r.tag in {"Revenues", "Assets"}]
    assert actuals, "sanity: fixture should contain actuals"
    assert not any(r.is_forward_looking for r in actuals)


def test_flag_is_derived_not_trusted_from_input():
    """The flag is computed from the dates, so it cannot disagree with them."""
    row = FactRow(
        cik=1,
        ticker="X",
        taxonomy="us-gaap",
        tag="T",
        unit="USD",
        val=1.0,
        end=date(2030, 1, 1),
        filed=date(2024, 1, 1),
        accn="a",
        form="10-K",
    )
    assert row.is_forward_looking is True


def test_period_ending_before_it_starts_is_rejected():
    with pytest.raises(ValidationError, match="after end"):
        FactRow(
            cik=1,
            ticker="X",
            taxonomy="us-gaap",
            tag="T",
            unit="USD",
            val=1.0,
            start=date(2024, 12, 31),
            end=date(2024, 1, 1),
            filed=date(2025, 1, 1),
            accn="a",
            form="10-K",
        )


def test_instant_and_flow_facts_are_distinguished(parsed):
    assets = next(r for r in parsed.rows if r.tag == "Assets")
    assert assets.is_instant
    assert assets.end == date(2024, 9, 28)

    revenues = [r for r in parsed.rows if r.tag == "Revenues"]
    assert len(revenues) == 3
    assert not any(r.is_instant for r in revenues)


def test_citation_is_human_readable(parsed):
    assets = next(r for r in parsed.rows if r.tag == "Assets")
    assert assets.citation == "10-K 2024-11-01 (0000320193-24-000123)"


def test_cik_padding():
    assert EdgarClient.pad_cik(320193) == "CIK0000320193"
    assert EdgarClient.pad_cik(1018724) == "CIK0001018724"
