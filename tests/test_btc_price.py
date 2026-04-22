"""Unit tests for src/btc_price.py (HTTP calls are mocked)."""

import pytest
import responses as resp_mock

from src.btc_price import get_btc_price, get_btc_klines


_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"
_KLINES_URL = "https://api.binance.com/api/v3/klines"

# A minimal raw kline entry from the Binance API
_RAW_KLINE = [
    1_700_000_000_000,  # open_time
    "40000.00",         # open
    "40100.00",         # high
    "39900.00",         # low
    "40050.00",         # close
    "10.5",             # volume
    1_700_000_059_999,  # close_time
    "420525.00",        # quote asset volume
    100,                # number of trades
    "5.0",              # taker buy base
    "200262.50",        # taker buy quote
    "0",                # ignore
]


@resp_mock.activate
def test_get_btc_price_returns_float():
    resp_mock.add(
        resp_mock.GET,
        _TICKER_URL,
        json={"symbol": "BTCUSDT", "price": "42000.12"},
    )
    price = get_btc_price()
    assert isinstance(price, float)
    assert price == pytest.approx(42000.12)


@resp_mock.activate
def test_get_btc_price_http_error_raises():
    resp_mock.add(resp_mock.GET, _TICKER_URL, status=500)
    with pytest.raises(Exception):
        get_btc_price()


@resp_mock.activate
def test_get_btc_klines_returns_list_of_dicts():
    resp_mock.add(
        resp_mock.GET,
        _KLINES_URL,
        json=[_RAW_KLINE] * 5,
    )
    klines = get_btc_klines(interval="1m", limit=5)
    assert len(klines) == 5
    first = klines[0]
    assert first["open"] == pytest.approx(40000.00)
    assert first["high"] == pytest.approx(40100.00)
    assert first["low"] == pytest.approx(39900.00)
    assert first["close"] == pytest.approx(40050.00)
    assert first["volume"] == pytest.approx(10.5)
    assert first["open_time"] == 1_700_000_000_000


@resp_mock.activate
def test_get_btc_klines_correct_params_sent():
    resp_mock.add(resp_mock.GET, _KLINES_URL, json=[_RAW_KLINE])
    get_btc_klines(interval="5m", limit=3)
    assert len(resp_mock.calls) == 1
    req = resp_mock.calls[0].request
    assert "symbol=BTCUSDT" in req.url
    assert "interval=5m" in req.url
    assert "limit=3" in req.url


@resp_mock.activate
def test_get_btc_klines_http_error_raises():
    resp_mock.add(resp_mock.GET, _KLINES_URL, status=429)
    with pytest.raises(Exception):
        get_btc_klines()
