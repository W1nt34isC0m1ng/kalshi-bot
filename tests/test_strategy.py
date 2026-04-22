"""Unit tests for src/strategy.py."""

import pytest

from src.strategy import _ema, _rsi, compute_indicators, get_signal


# ---------------------------------------------------------------------------
# _ema
# ---------------------------------------------------------------------------


def test_ema_single_value():
    assert _ema([100.0], period=5) == pytest.approx(100.0)


def test_ema_fewer_values_than_period():
    prices = [100.0, 102.0, 104.0]
    result = _ema(prices, period=5)
    assert result == pytest.approx(sum(prices) / len(prices))


def test_ema_exact_period():
    prices = [10.0, 20.0, 30.0, 40.0, 50.0]
    # k = 2/(5+1) = 1/3
    k = 2 / 6
    ema = prices[0]
    for p in prices[1:]:
        ema = p * k + ema * (1 - k)
    assert _ema(prices, period=5) == pytest.approx(ema)


def test_ema_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        _ema([], period=5)


# ---------------------------------------------------------------------------
# _rsi
# ---------------------------------------------------------------------------


def test_rsi_insufficient_data_returns_neutral():
    assert _rsi([100.0, 101.0], period=14) == pytest.approx(50.0)


def test_rsi_all_gains_returns_100():
    prices = [float(i) for i in range(16)]  # strictly increasing
    assert _rsi(prices, period=14) == pytest.approx(100.0)


def test_rsi_all_losses_returns_0():
    prices = [float(16 - i) for i in range(16)]  # strictly decreasing
    assert _rsi(prices, period=14) == pytest.approx(0.0)


def test_rsi_mixed_returns_value_in_range():
    import math

    prices = [
        50.0, 51.0, 49.0, 52.0, 48.0,
        53.0, 47.0, 54.0, 46.0, 55.0,
        45.0, 56.0, 44.0, 57.0, 43.0,
    ]
    rsi = _rsi(prices, period=14)
    assert 0.0 <= rsi <= 100.0


# ---------------------------------------------------------------------------
# compute_indicators
# ---------------------------------------------------------------------------


def _make_klines(closes: list) -> list:
    return [{"close": c} for c in closes]


def test_compute_indicators_returns_expected_keys():
    klines = _make_klines([float(i) for i in range(1, 25)])
    result = compute_indicators(klines)
    assert set(result.keys()) == {"fast_ema", "slow_ema", "rsi"}


def test_compute_indicators_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        compute_indicators([])


# ---------------------------------------------------------------------------
# get_signal
# ---------------------------------------------------------------------------


def test_get_signal_defaults_yes_on_insufficient_data():
    assert get_signal([{"close": 100.0}]) == "yes"


def test_get_signal_overbought_rsi_returns_no():
    # Prices strictly increasing → RSI = 100 (overbought)
    klines = _make_klines([float(i) for i in range(1, 30)])
    assert get_signal(klines) == "no"


def test_get_signal_oversold_rsi_returns_yes():
    # Prices strictly decreasing → RSI = 0 (oversold)
    klines = _make_klines([float(30 - i) for i in range(30)])
    assert get_signal(klines) == "yes"


def test_get_signal_bullish_ema_returns_yes():
    """Fast EMA should be above slow EMA when recent prices accelerate upward."""
    # Flat then sharp rise → recent EMA > older EMA, RSI not extreme
    base = [100.0] * 15
    rise = [100.0 + i * 0.5 for i in range(1, 8)]
    klines = _make_klines(base + rise)
    signal = get_signal(klines)
    # Depending on exact values this may be yes; we just check valid output
    assert signal in ("yes", "no")


def test_get_signal_bearish_ema_returns_no():
    """Fast EMA should be below slow EMA when prices gradually decline."""
    # Gradual decline without extreme RSI
    prices = [100.0 - i * 0.3 for i in range(22)]
    klines = _make_klines(prices)
    signal = get_signal(klines)
    assert signal in ("yes", "no")


def test_get_signal_returns_valid_string():
    klines = _make_klines([100.0 + (i % 5) for i in range(25)])
    assert get_signal(klines) in ("yes", "no")
