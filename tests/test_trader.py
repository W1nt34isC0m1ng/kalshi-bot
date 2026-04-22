"""Unit tests for src/trader.py (Kalshi client and BTC price are mocked)."""

from unittest.mock import MagicMock, patch

import pytest

from src.trader import Trader, DEFAULT_BTC_SERIES


def _make_client():
    client = MagicMock()
    client.is_authenticated = True
    client.get_balance.return_value = 5000  # $50.00
    return client


def _make_klines(n: int = 20, base: float = 40000.0) -> list:
    return [{"close": base + i * 10} for i in range(n)]


def _make_market(ticker: str = "KXBTCD-TEST", close_time: str = "2025-01-01T00:15:00Z") -> dict:
    return {"ticker": ticker, "close_time": close_time}


# ---------------------------------------------------------------------------
# find_active_market
# ---------------------------------------------------------------------------


def test_find_active_market_returns_soonest_expiring():
    client = _make_client()
    client.get_markets.return_value = [
        {"ticker": "MKT-B", "close_time": "2025-01-01T00:30:00Z"},
        {"ticker": "MKT-A", "close_time": "2025-01-01T00:15:00Z"},
    ]
    trader = Trader(client=client, series_ticker=DEFAULT_BTC_SERIES)
    market = trader.find_active_market()
    assert market["ticker"] == "MKT-A"


def test_find_active_market_returns_none_when_no_markets():
    client = _make_client()
    client.get_markets.return_value = []
    trader = Trader(client=client)
    assert trader.find_active_market() is None


# ---------------------------------------------------------------------------
# run_once – happy path
# ---------------------------------------------------------------------------


def test_run_once_places_order_with_signal(mocker):
    client = _make_client()
    client.get_markets.return_value = [_make_market()]
    client.place_order.return_value = {"order_id": "abc123", "status": "resting"}

    mocker.patch("src.trader.get_btc_klines", return_value=_make_klines())

    trader = Trader(client=client, num_contracts=2)
    result = trader.run_once()

    assert result["signal"] in ("yes", "no")
    assert result["market"] == "KXBTCD-TEST"
    assert result["order"]["order_id"] == "abc123"
    client.place_order.assert_called_once_with(
        ticker="KXBTCD-TEST",
        side=result["signal"],
        count=2,
        order_type="market",
    )


# ---------------------------------------------------------------------------
# run_once – dry run
# ---------------------------------------------------------------------------


def test_run_once_dry_run_does_not_call_place_order(mocker):
    client = _make_client()
    client.get_markets.return_value = [_make_market()]

    mocker.patch("src.trader.get_btc_klines", return_value=_make_klines())

    trader = Trader(client=client, dry_run=True)
    result = trader.run_once()

    client.place_order.assert_not_called()
    assert result["order"] is None
    assert result["signal"] in ("yes", "no")


# ---------------------------------------------------------------------------
# run_once – edge cases
# ---------------------------------------------------------------------------


def test_run_once_returns_early_when_klines_fail(mocker):
    client = _make_client()
    mocker.patch("src.trader.get_btc_klines", side_effect=RuntimeError("network"))

    trader = Trader(client=client)
    result = trader.run_once()

    assert result["signal"] is None
    assert result["market"] is None
    client.place_order.assert_not_called()


def test_run_once_returns_early_when_no_market(mocker):
    client = _make_client()
    client.get_markets.return_value = []

    mocker.patch("src.trader.get_btc_klines", return_value=_make_klines())

    trader = Trader(client=client)
    result = trader.run_once()

    assert result["market"] is None
    client.place_order.assert_not_called()


def test_run_once_skips_order_when_balance_zero(mocker):
    client = _make_client()
    client.get_balance.return_value = 0
    client.get_markets.return_value = [_make_market()]

    mocker.patch("src.trader.get_btc_klines", return_value=_make_klines())

    trader = Trader(client=client)
    result = trader.run_once()

    client.place_order.assert_not_called()
    assert result["order"] is None


def test_run_once_handles_order_exception(mocker):
    client = _make_client()
    client.get_markets.return_value = [_make_market()]
    client.place_order.side_effect = Exception("order rejected")

    mocker.patch("src.trader.get_btc_klines", return_value=_make_klines())

    trader = Trader(client=client)
    result = trader.run_once()

    # Should not raise; order will be None
    assert result["order"] is None
