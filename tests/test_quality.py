"""Tests for the coverage checks.

Built around the real case they were written for: a ticker that passed every
validation rule while describing the wrong company.
"""

from __future__ import annotations

from datetime import date

from factline.quality import CoverageFinding, TickerCoverage, check_coverage


def cov(ticker, rows, earliest=date(2006, 12, 31), latest=date(2026, 6, 30), tags=500):
    return TickerCoverage(ticker=ticker, rows=rows, tags=tags, earliest=earliest, latest=latest)


def healthy_peers():
    return [
        cov("AAPL", 25_135),
        cov("MSFT", 32_671),
        cov("JPM", 50_034),
        cov("KO", 33_232),
        cov("CAT", 39_378),
    ]


def test_healthy_universe_is_clean():
    assert check_coverage(healthy_peers()) == []


def test_catches_the_xom_case():
    """269 facts and two years of history, against a peer median of ~33k / 20y."""
    peers = healthy_peers()
    peers.append(cov("XOM", 269, earliest=date(2024, 12, 31), tags=95))

    findings = check_coverage(peers)
    kinds = {(f.ticker, f.kind) for f in findings}

    assert ("XOM", "too_few_facts") in kinds
    assert ("XOM", "too_little_history") in kinds
    assert all(f.ticker == "XOM" for f in findings), "must not flag healthy peers"


def test_uses_median_so_one_outlier_cannot_hide_itself():
    """A mean would be dragged down far enough to make the outlier look normal."""
    peers = healthy_peers()
    peers.append(cov("BAD", 100))
    assert any(f.ticker == "BAD" for f in check_coverage(peers))


def test_two_outliers_are_both_caught():
    peers = healthy_peers()
    peers.extend([cov("BAD1", 120), cov("BAD2", 340)])
    flagged = {f.ticker for f in check_coverage(peers)}
    assert {"BAD1", "BAD2"} <= flagged


def test_a_merely_smaller_filer_is_not_flagged():
    """The check must tolerate real variation or it becomes noise."""
    peers = healthy_peers()
    peers.append(cov("SMALL", 9_000))  # ~27% of median: small, but plausible
    assert check_coverage(peers) == []


def test_short_history_alone_is_enough_to_flag():
    """A recent IPO with plenty of facts but no history is still a coverage gap."""
    peers = healthy_peers()
    peers.append(cov("IPO", 30_000, earliest=date(2025, 1, 1)))
    kinds = {(f.ticker, f.kind) for f in check_coverage(peers)}
    assert ("IPO", "too_little_history") in kinds
    assert ("IPO", "too_few_facts") not in kinds


def test_too_few_peers_reports_rather_than_returning_all_clear():
    """An empty finding list must always mean "checked and clean"."""
    findings = check_coverage([cov("AAPL", 25_000), cov("MSFT", 30_000)])
    assert len(findings) == 1
    assert findings[0].kind == "insufficient_peers"


def test_finding_renders_readably():
    f = CoverageFinding(ticker="XOM", kind="too_few_facts", detail="269 facts vs 33,232")
    assert str(f).startswith("XOM: ")
