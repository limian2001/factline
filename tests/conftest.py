from __future__ import annotations

import json
from pathlib import Path

import pytest

from factline.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def companyfacts() -> dict:
    with (FIXTURES / "companyfacts_sample.json").open() as fh:
        return json.load(fh)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        sec_user_agent="Test Runner test@example.com",
        data_dir=tmp_path / "data",
        _env_file=None,
    )
