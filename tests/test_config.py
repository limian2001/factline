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
