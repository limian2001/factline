"""Coverage checks: did we get the data we expected, not just well-formed data?

This module exists because validation passed and the data was still wrong. XOM
came back with 269 facts and two years of history, against a peer median of
~27,000 facts and twenty years -- and its error rate was 1.8%, healthier than
Alphabet's. Every individual record was valid. The company was the wrong one.

Validation asks "is this record well formed?". Coverage asks "is this the body
of data I expected?". A financial system loses trust on the second question,
because failing it produces no error at all -- just quietly wrong answers.

Comparisons use the median, not the mean: with fifteen names a single degenerate
ticker drags a mean far enough to hide itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median

# A ticker with less than this share of the peer median is almost certainly the
# wrong entity rather than merely a smaller filer. XOM's 269 against a median of
# ~27,000 is 1%; the smallest legitimate filer in a large-cap universe sits well
# above 10%.
MIN_SHARE_OF_MEDIAN_FACTS = 0.10
MIN_SHARE_OF_MEDIAN_HISTORY = 0.25

# How far behind its peers a ticker's most recent FILING may fall before it is
# suspicious. Deliberately measured on the filing date, not the period end: a
# slow filer has an old period but a recent filing, whereas an entity that has
# stopped filing has neither -- and only the second is a coverage problem.
#
# Quarterly filers file roughly every 90 days, so being more than ~75 days
# behind the peer median means a whole reporting period is missing. That is what
# happens to a predecessor CIK after a holding-company reorganisation: the
# history stays complete and abundant, it simply stops. Volume and depth checks
# are both blind to it.
MAX_FILING_LAG_DAYS_VS_MEDIAN = 75


@dataclass(frozen=True)
class CoverageFinding:
    ticker: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.ticker}: {self.detail}"


@dataclass(frozen=True)
class TickerCoverage:
    ticker: str
    rows: int
    tags: int
    earliest: date
    # Latest period the data covers -- used for history depth.
    latest: date
    # Latest date anything was actually filed -- used for staleness. These are
    # different questions and conflating them hides a dead entity behind a
    # merely late one.
    latest_filing: date

    @property
    def history_days(self) -> int:
        return (self.latest - self.earliest).days


def check_coverage(coverage: list[TickerCoverage]) -> list[CoverageFinding]:
    """Flag tickers whose coverage is a severe outlier against their peers."""
    if len(coverage) < 3:
        # Too few peers for a median to mean anything. Say so rather than
        # returning an empty list, which would read as "all clear".
        return [
            CoverageFinding(
                ticker="-",
                kind="insufficient_peers",
                detail=f"only {len(coverage)} tickers; need 3+ to compare",
            )
        ]

    findings: list[CoverageFinding] = []
    median_rows = median(c.rows for c in coverage)
    median_history = median(c.history_days for c in coverage)
    median_filing = median(c.latest_filing.toordinal() for c in coverage)

    for c in coverage:
        if c.rows < median_rows * MIN_SHARE_OF_MEDIAN_FACTS:
            findings.append(
                CoverageFinding(
                    ticker=c.ticker,
                    kind="too_few_facts",
                    detail=(
                        f"{c.rows:,} facts vs peer median {median_rows:,.0f} "
                        f"({c.rows / median_rows:.1%}) — likely the wrong entity, "
                        "e.g. a reorganisation shell rather than the operating company"
                    ),
                )
            )

        lag = median_filing - c.latest_filing.toordinal()
        if lag > MAX_FILING_LAG_DAYS_VS_MEDIAN:
            findings.append(
                CoverageFinding(
                    ticker=c.ticker,
                    kind="stale_filings",
                    detail=(
                        f"last filed {c.latest_filing}, {lag} days behind the peer "
                        "median — a reporting period is missing, so this entity may "
                        "have stopped filing (e.g. a predecessor CIK after a "
                        "reorganisation)"
                    ),
                )
            )

        if c.history_days < median_history * MIN_SHARE_OF_MEDIAN_HISTORY:
            findings.append(
                CoverageFinding(
                    ticker=c.ticker,
                    kind="too_little_history",
                    detail=(
                        f"history starts {c.earliest} "
                        f"({c.history_days / 365:.1f}y vs peer median "
                        f"{median_history / 365:.1f}y)"
                    ),
                )
            )

    return findings
