"""Regression tests for how the EDGAR client talks to the network.

These exist because of a real bug: the client pinned `Host: data.sec.gov` in its
default headers, which made every request to www.sec.gov (the ticker map) come
back 404 -- the server resolved the path against the Host it was given rather
than the host we connected to. Fixture-based parsing tests cannot catch that,
so the request itself is asserted on here.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from factline.clients.edgar import DATA_BASE, TICKER_MAP_URL, EdgarClient

TICKER_MAP_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
}


@pytest.fixture
def client(settings) -> EdgarClient:
    return EdgarClient(settings)


def test_no_host_header_is_pinned(client: EdgarClient):
    """The client spans two hosts, so it must not hardcode Host for either."""
    headers = {k.lower() for k in client.http._client.headers}
    assert "host" not in headers, (
        "a pinned Host header makes requests to the other sec.gov host 404"
    )


@respx.mock
def test_ticker_map_request_targets_www_host(client: EdgarClient):
    """The ticker map lives on www.sec.gov, not data.sec.gov."""
    route = respx.get(TICKER_MAP_URL).mock(
        return_value=httpx.Response(200, json=TICKER_MAP_PAYLOAD)
    )

    assert client.ticker_to_cik("AAPL") == 320193
    assert route.called

    request = route.calls[0].request
    assert request.url.host == "www.sec.gov"
    # The Host header must match the host actually being connected to.
    assert request.headers["host"] == "www.sec.gov"


@respx.mock
def test_companyfacts_request_targets_data_host(client: EdgarClient):
    """The XBRL APIs live on data.sec.gov, and must still resolve correctly."""
    url = f"{DATA_BASE}/api/xbrl/companyfacts/CIK0000320193.json"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"cik": 320193, "facts": {}}))

    client.fetch_company_facts(320193)
    assert route.called

    request = route.calls[0].request
    assert request.url.host == "data.sec.gov"
    assert request.headers["host"] == "data.sec.gov"


@respx.mock
def test_contact_header_is_always_sent(client: EdgarClient):
    """Without a contact User-Agent the SEC replies 403 to everything."""
    route = respx.get(TICKER_MAP_URL).mock(
        return_value=httpx.Response(200, json=TICKER_MAP_PAYLOAD)
    )
    client.ticker_to_cik("MSFT")

    ua = route.calls[0].request.headers["user-agent"]
    assert "@" in ua and len(ua.split()) >= 2


@respx.mock
def test_unknown_ticker_raises_rather_than_guessing(client: EdgarClient):
    respx.get(TICKER_MAP_URL).mock(return_value=httpx.Response(200, json=TICKER_MAP_PAYLOAD))
    with pytest.raises(KeyError, match="NOSUCH"):
        client.ticker_to_cik("NOSUCH")
