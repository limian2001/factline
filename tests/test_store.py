from __future__ import annotations

import pytest

from factline.clients.edgar import EdgarClient
from factline.store.lake import FactLake


def test_roundtrip_and_idempotence(settings, companyfacts):
    lake = FactLake(settings.clean_dir, settings.quarantine_dir)
    parsed = EdgarClient.parse_company_facts(companyfacts, "AAPL")

    lake.write_facts("AAPL", parsed.rows)
    first = lake.sql("SELECT COUNT(*) FROM facts")[0][0]

    # Re-running the pipeline must not append duplicates.
    lake.write_facts("AAPL", parsed.rows)
    second = lake.sql("SELECT COUNT(*) FROM facts")[0][0]

    assert first == second == parsed.accepted_count


def test_refuses_to_write_empty_partition(settings):
    lake = FactLake(settings.clean_dir, settings.quarantine_dir)
    with pytest.raises(ValueError, match="empty fact partition"):
        lake.write_facts("AAPL", [])


def test_quarantine_is_persisted(settings, companyfacts):
    lake = FactLake(settings.clean_dir, settings.quarantine_dir)
    parsed = EdgarClient.parse_company_facts(companyfacts, "AAPL")
    path = lake.write_quarantine("AAPL", parsed.quarantined)

    assert path is not None and path.exists()
    lines = path.read_text().strip().splitlines()
    assert len(lines) == len(parsed.quarantined)


def test_coverage_report(settings, companyfacts):
    lake = FactLake(settings.clean_dir, settings.quarantine_dir)
    parsed = EdgarClient.parse_company_facts(companyfacts, "AAPL")
    lake.write_facts("AAPL", parsed.rows)

    rows = lake.coverage()
    assert len(rows) == 1
    ticker, count, tags, earliest, latest, latest_filing = rows[0]
    assert ticker == "AAPL"
    assert count == parsed.accepted_count
    # Revenues, Assets, LongTermDebtMaturities...NextTwelveMonths, and the dei tag.
    assert tags == 4
