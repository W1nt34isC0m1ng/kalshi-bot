"""Unit tests for src/kalshi_client.py (HTTP calls are mocked)."""

import pytest
import responses as resp_mock

from src.kalshi_client import KalshiClient, KalshiError

_BASE = "https://trading-api.kalshi.com/trade-api/v2"


def _client() -> KalshiClient:
    return KalshiClient(email="test@example.com", password="secret")


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@resp_mock.activate
def test_login_stores_token():
    resp_mock.add(
        resp_mock.POST,
        f"{_BASE}/login",
        json={"token": "tok123", "member_id": "mem456"},
    )
    client = _client()
    token = client.login()
    assert token == "tok123"
    assert client.is_authenticated
    assert client._session.headers["Authorization"] == "tok123"


@resp_mock.activate
def test_login_raises_on_failure():
    resp_mock.add(resp_mock.POST, f"{_BASE}/login", status=401, json={"error": "bad creds"})
    client = _client()
    with pytest.raises(KalshiError, match="401"):
        client.login()


# ---------------------------------------------------------------------------
# get_markets
# ---------------------------------------------------------------------------


@resp_mock.activate
def test_get_markets_returns_list():
    resp_mock.add(
        resp_mock.GET,
        f"{_BASE}/markets",
        json={"markets": [{"ticker": "MKT-1"}, {"ticker": "MKT-2"}]},
    )
    client = _client()
    markets = client.get_markets(series_ticker="KXBTCD")
    assert len(markets) == 2
    assert markets[0]["ticker"] == "MKT-1"


@resp_mock.activate
def test_get_markets_passes_query_params():
    resp_mock.add(resp_mock.GET, f"{_BASE}/markets", json={"markets": []})
    client = _client()
    client.get_markets(series_ticker="KXBTCD", status="open", limit=50)
    req = resp_mock.calls[0].request
    assert "series_ticker=KXBTCD" in req.url
    assert "status=open" in req.url
    assert "limit=50" in req.url


# ---------------------------------------------------------------------------
# place_order
# ---------------------------------------------------------------------------


@resp_mock.activate
def test_place_order_sends_correct_payload():
    resp_mock.add(
        resp_mock.POST,
        f"{_BASE}/orders",
        json={"order": {"order_id": "ord1", "status": "resting"}},
    )
    client = _client()
    result = client.place_order(
        ticker="KXBTCD-TEST", side="yes", count=3, order_type="market"
    )
    assert result["order_id"] == "ord1"
    import json
    body = json.loads(resp_mock.calls[0].request.body)
    assert body["ticker"] == "KXBTCD-TEST"
    assert body["side"] == "yes"
    assert body["count"] == 3
    assert body["action"] == "buy"


def test_place_order_invalid_side_raises():
    client = _client()
    with pytest.raises(ValueError, match="side"):
        client.place_order(ticker="T", side="maybe", count=1)


def test_place_order_invalid_count_raises():
    client = _client()
    with pytest.raises(ValueError, match="count"):
        client.place_order(ticker="T", side="yes", count=0)


# ---------------------------------------------------------------------------
# get_balance
# ---------------------------------------------------------------------------


@resp_mock.activate
def test_get_balance_returns_int():
    resp_mock.add(resp_mock.GET, f"{_BASE}/portfolio/balance", json={"balance": 10000})
    client = _client()
    assert client.get_balance() == 10000


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------


@resp_mock.activate
def test_get_positions_returns_list():
    resp_mock.add(
        resp_mock.GET,
        f"{_BASE}/portfolio/positions",
        json={"market_positions": [{"ticker": "MKT-1", "position": 2}]},
    )
    client = _client()
    positions = client.get_positions()
    assert len(positions) == 1
    assert positions[0]["ticker"] == "MKT-1"


# ---------------------------------------------------------------------------
# _raise_for_status
# ---------------------------------------------------------------------------


@resp_mock.activate
def test_api_error_includes_status_code():
    resp_mock.add(
        resp_mock.GET,
        f"{_BASE}/portfolio/balance",
        status=503,
        json={"message": "unavailable"},
    )
    client = _client()
    with pytest.raises(KalshiError, match="503"):
        client.get_balance()
