"""Seed the lake from the test fixture so the CLI can be demoed without network."""

from __future__ import annotations

import json
from pathlib import Path

from factline.clients.edgar import EdgarClient
from factline.config import load_settings
from factline.store.lake import FactLake

fixture = Path("tests/fixtures/companyfacts_sample.json")
settings = load_settings()
lake = FactLake(settings.clean_dir, settings.quarantine_dir)

with fixture.open() as fh:
    payload = json.load(fh)

parsed = EdgarClient.parse_company_facts(payload, "AAPL")
lake.write_facts("AAPL", parsed.rows)
lake.write_quarantine("AAPL", parsed.quarantined)
print(f"seeded {parsed.accepted_count} facts, quarantined {len(parsed.quarantined)}")
