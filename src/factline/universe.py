"""The covered universe.

Deliberately small. 15 names across sectors and fiscal-year conventions is enough
to surface every structural problem in EDGAR data (non-calendar fiscal years,
restatements, tag drift) without the long tail of a 3000-name universe.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str
    sector: str
    # Fiscal year end as MM-DD. Non-calendar years are the most common source of
    # silently wrong year-over-year comparisons, so we record it explicitly.
    fiscal_year_end: str


UNIVERSE: tuple[Holding, ...] = (
    Holding("AAPL", "Apple Inc.", "Technology", "09-30"),
    Holding("MSFT", "Microsoft Corporation", "Technology", "06-30"),
    Holding("NVDA", "NVIDIA Corporation", "Semiconductors", "01-31"),
    Holding("GOOGL", "Alphabet Inc.", "Technology", "12-31"),
    Holding("AMZN", "Amazon.com Inc.", "Consumer Discretionary", "12-31"),
    Holding("META", "Meta Platforms Inc.", "Technology", "12-31"),
    Holding("JPM", "JPMorgan Chase & Co.", "Financials", "12-31"),
    Holding("JNJ", "Johnson & Johnson", "Healthcare", "12-31"),
    Holding("XOM", "Exxon Mobil Corporation", "Energy", "12-31"),
    Holding("PG", "Procter & Gamble Company", "Consumer Staples", "06-30"),
    Holding("KO", "Coca-Cola Company", "Consumer Staples", "12-31"),
    Holding("WMT", "Walmart Inc.", "Consumer Staples", "01-31"),
    Holding("CAT", "Caterpillar Inc.", "Industrials", "12-31"),
    Holding("UNH", "UnitedHealth Group Inc.", "Healthcare", "12-31"),
    Holding("COST", "Costco Wholesale Corporation", "Consumer Staples", "08-31"),
)

BY_TICKER = {h.ticker: h for h in UNIVERSE}
TICKERS = tuple(h.ticker for h in UNIVERSE)

# Four of fifteen names do not close in December. Any year-over-year metric that
# ignores this is wrong, which is what test_universe_has_offset_fiscal_years guards.
OFFSET_FISCAL_YEAR_TICKERS = tuple(h.ticker for h in UNIVERSE if h.fiscal_year_end != "12-31")
