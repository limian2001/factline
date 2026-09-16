from __future__ import annotations

import pytest
from pydantic import ValidationError

from factline.config import Settings
from factline.universe import OFFSET_FISCAL_YEAR_TICKERS, TICKERS


@pytest.mark.parametrize("bad", ["", "factline", "no-at-sign here", "just@email.com"])
def test_rejects_user_agent_that_sec_would_403(bad):
    with pytest.raises(ValidationError):
        Settings(sec_user_agent=bad, _env_file=None)


def test_accepts_proper_contact_header():
    s = Settings(sec_user_agent="Mian Li mian@example.com", _env_file=None)
    assert s.sec_rate_limit_rps <= 10, "must stay under the SEC's hard cap"


def test_layout_separates_raw_from_derived(tmp_path):
    s = Settings(sec_user_agent="A B a@b.com", data_dir=tmp_path, _env_file=None)
    assert s.raw_dir != s.clean_dir
    assert s.quarantine_dir.name == "quarantine"


def test_universe_covers_offset_fiscal_years():
    """A universe of only December filers would hide the most common data bug."""
    assert len(TICKERS) == 15
    assert len(set(TICKERS)) == 15
    assert len(OFFSET_FISCAL_YEAR_TICKERS) >= 4


# --- pinned CIKs -----------------------------------------------------------
# These exist because resolving tickers at runtime silently analysed the wrong
# company: XOM maps to a 2024 reorganisation entity with ~2 years of filings.


def test_every_holding_has_a_pinned_cik():
    from factline.universe import UNIVERSE

    assert all(h.cik > 0 for h in UNIVERSE)
    assert len({h.cik for h in UNIVERSE}) == len(UNIVERSE), "duplicate CIK"


def test_xom_is_pinned_to_the_operating_company_not_the_holding_shell():
    from factline.universe import BY_TICKER

    xom = BY_TICKER["XOM"]
    assert xom.cik == 34088, "Exxon Mobil Corporation, twenty years of filings"
    assert xom.cik != 2115436, "ExxonMobil Holdings Corporation, the 2024 shell"
    assert xom.pin_note, "a deliberate divergence must carry its reason"


def test_cik_drift_is_detected_not_silently_followed():
    from factline.universe import check_cik_drift

    # The live ticker map today: XOM points at the holding company.
    warnings = check_cik_drift({"XOM": 2115436, "AAPL": 320193})
    assert len(warnings) == 1
    assert "XOM" in warnings[0]
    assert "2115436" in warnings[0] and "34088" in warnings[0]
    assert "Holdings" in warnings[0], "the pin note should travel with the warning"


def test_agreeing_map_produces_no_warnings():
    from factline.universe import UNIVERSE, check_cik_drift

    assert check_cik_drift({h.ticker: h.cik for h in UNIVERSE}) == []


def test_unknown_tickers_in_the_map_are_ignored():
    from factline.universe import check_cik_drift

    assert check_cik_drift({"TSLA": 1318605}) == []
