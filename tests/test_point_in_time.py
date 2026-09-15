"""Point-in-time correctness.

These are the tests that make the rest of the project trustworthy. If a metric
as-of some date can see a number that was not yet public, every downstream
conclusion is contaminated.
"""

from __future__ import annotations

from datetime import date

from factline.clients.edgar import EdgarClient
from factline.store.lake import FactLake


def build_lake(settings, companyfacts) -> FactLake:
    lake = FactLake(settings.clean_dir, settings.quarantine_dir)
    parsed = EdgarClient.parse_company_facts(companyfacts, "AAPL")
    lake.write_facts("AAPL", parsed.rows)
    return lake


def test_future_filings_are_invisible(settings, companyfacts):
    """The FY2024 10-K was filed 2024-11-01, so on 2024-10-01 it did not exist."""
    lake = build_lake(settings, companyfacts)

    before = lake.facts_as_of("AAPL", date(2024, 10, 1), tag="Assets")
    assert before == [], "a filing from the future leaked into an as-of query"

    after = lake.facts_as_of("AAPL", date(2024, 11, 1), tag="Assets")
    assert len(after) == 1
    assert after[0][4] == 364980000000


def test_original_value_used_before_restatement(settings, companyfacts):
    """Q1 FY2025 revenue was first filed at 124.30bn, then restated to 124.40bn.

    Between the two filing dates the ORIGINAL number is the correct one: it is
    what the market actually knew. Most naive pipelines return the restated value
    for every historical date, which is the classic lookahead bug.
    """
    lake = build_lake(settings, companyfacts)
    period_end = date(2024, 12, 28)

    on_first_filing = lake.facts_as_of("AAPL", date(2025, 2, 15), tag="Revenues")
    q1 = [r for r in on_first_filing if r[3] == period_end]
    assert len(q1) == 1, "restatement should not duplicate the period"
    assert q1[0][4] == 124300000000, "should see the ORIGINAL figure, not the restatement"
    assert q1[0][7] == "10-Q"

    on_after_restatement = lake.facts_as_of("AAPL", date(2025, 6, 1), tag="Revenues")
    q1_after = [r for r in on_after_restatement if r[3] == period_end]
    assert len(q1_after) == 1
    assert q1_after[0][4] == 124400000000, "after the amendment, the restated figure wins"
    assert q1_after[0][7] == "10-Q/A"


def test_as_of_is_monotonic_in_information(settings, companyfacts):
    """Later dates can never know less than earlier dates."""
    lake = build_lake(settings, companyfacts)
    counts = [
        len(lake.facts_as_of("AAPL", d))
        for d in (date(2024, 1, 1), date(2024, 6, 1), date(2025, 1, 1), date(2026, 1, 1))
    ]
    assert counts == sorted(counts), f"information went backwards: {counts}"
