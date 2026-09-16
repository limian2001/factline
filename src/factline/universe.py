"""The covered universe.

Deliberately small. 15 names across sectors and fiscal-year conventions is enough
to surface every structural problem in EDGAR data (non-calendar fiscal years,
restatements, tag drift, corporate reorganisations) without the long tail of a
3000-name universe.

CIKs are pinned, not resolved at runtime. Tickers move between legal entities --
reorganisations, spin-offs, mergers -- and the SEC ticker map always points at
the *current* registrant, which after a holding-company reorganisation is a new
CIK with almost no filing history. That is not a hypothetical: XOM resolves to
ExxonMobil Holdings Corporation (CIK 2115436, incorporated 2024, 269 facts)
while twenty years of operating history sits under Exxon Mobil Corporation
(CIK 34088). Nothing was corrupt, nothing failed validation -- the pipeline just
quietly analysed the wrong company. Pinning makes the choice explicit and
reviewable, and `check_cik_drift` turns a future remapping into a loud warning
instead of a silent substitution.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Holding:
    ticker: str
    cik: int
    name: str
    sector: str
    # Fiscal year end as MM-DD. Non-calendar years are the most common source of
    # silently wrong year-over-year comparisons, so we record it explicitly.
    fiscal_year_end: str
    # Set when the pinned CIK deliberately differs from what the SEC ticker map
    # returns, with the reason. Read by check_cik_drift.
    pin_note: str | None = None


UNIVERSE: tuple[Holding, ...] = (
    Holding("AAPL", 320193, "Apple Inc.", "Technology", "09-30"),
    Holding("MSFT", 789019, "Microsoft Corporation", "Technology", "06-30"),
    Holding("NVDA", 1045810, "NVIDIA Corporation", "Semiconductors", "01-31"),
    Holding("GOOGL", 1652044, "Alphabet Inc.", "Technology", "12-31"),
    Holding("AMZN", 1018724, "Amazon.com Inc.", "Consumer Discretionary", "12-31"),
    Holding("META", 1326801, "Meta Platforms Inc.", "Technology", "12-31"),
    Holding("JPM", 19617, "JPMorgan Chase & Co.", "Financials", "12-31"),
    Holding("JNJ", 200406, "Johnson & Johnson", "Healthcare", "12-31"),
    Holding(
        "XOM",
        34088,
        "Exxon Mobil Corporation",
        "Energy",
        "12-31",
        pin_note=(
            "The ticker map resolves XOM to ExxonMobil Holdings Corporation "
            "(CIK 2115436), a 2024 reorganisation entity with ~2 years of "
            "filings. The operating history is under CIK 34088."
        ),
    ),
    Holding("PG", 80424, "Procter & Gamble Company", "Consumer Staples", "06-30"),
    Holding("KO", 21344, "Coca-Cola Company", "Consumer Staples", "12-31"),
    Holding("WMT", 104169, "Walmart Inc.", "Consumer Staples", "01-31"),
    Holding("CAT", 18230, "Caterpillar Inc.", "Industrials", "12-31"),
    Holding("UNH", 731766, "UnitedHealth Group Inc.", "Healthcare", "12-31"),
    Holding("COST", 909832, "Costco Wholesale Corporation", "Consumer Staples", "08-31"),
)

BY_TICKER = {h.ticker: h for h in UNIVERSE}
TICKERS = tuple(h.ticker for h in UNIVERSE)

# Four of fifteen names do not close in December. Any year-over-year metric that
# ignores this is wrong, which is what test_universe_covers_offset_fiscal_years
# guards.
OFFSET_FISCAL_YEAR_TICKERS = tuple(h.ticker for h in UNIVERSE if h.fiscal_year_end != "12-31")


def check_cik_drift(resolved: dict[str, int]) -> list[str]:
    """Compare pinned CIKs against what the SEC ticker map currently returns.

    Returns one human-readable warning per disagreement. A disagreement is not an
    error -- the pin is deliberate -- but it must never pass unnoticed, because it
    means the ticker now belongs to a different legal entity than the one we
    analyse, and one day that will be a change we want to follow.
    """
    warnings: list[str] = []
    for holding in UNIVERSE:
        current = resolved.get(holding.ticker)
        if current is None or current == holding.cik:
            continue
        note = f" ({holding.pin_note})" if holding.pin_note else ""
        warnings.append(
            f"{holding.ticker}: pinned CIK {holding.cik} but ticker map says {current}{note}"
        )
    return warnings
