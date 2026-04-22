"""Trading signal logic for the Kalshi BTC 15-minute binary market.

Strategy: combine a short-term momentum indicator (EMA crossover) with
the Relative Strength Index (RSI) to decide whether to bet YES (BTC up)
or NO (BTC down).

Rules
-----
* Compute fast EMA (5-period) and slow EMA (10-period) on 1-minute closes.
* If fast EMA > slow EMA  → bullish bias.
* If fast EMA < slow EMA  → bearish bias.
* If RSI(14) > 70         → overbought; override to NO.
* If RSI(14) < 30         → oversold;   override to YES.
* Otherwise follow the EMA crossover signal.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# EMA periods
_FAST_PERIOD = 5
_SLOW_PERIOD = 10
# RSI period and thresholds
_RSI_PERIOD = 14
_RSI_OVERBOUGHT = 70
_RSI_OVERSOLD = 30


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ema(prices: list[float], period: int) -> float:
    """Return the EMA of a price series using the most recent *period* values."""
    if not prices:
        raise ValueError("prices list is empty")
    if len(prices) < period:
        return sum(prices) / len(prices)
    k = 2.0 / (period + 1)
    ema = prices[-period]
    for price in prices[-period + 1 :]:
        ema = price * k + ema * (1 - k)
    return ema


def _rsi(prices: list[float], period: int = _RSI_PERIOD) -> float:
    """Return the RSI for the most recent *period* + 1 prices."""
    if len(prices) < period + 1:
        return 50.0  # neutral when insufficient data
    recent = prices[-(period + 1) :]
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(recent)):
        delta = recent[i] - recent[i - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-delta)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_indicators(klines: list[dict[str, Any]]) -> dict[str, float]:
    """Compute EMA and RSI indicators from a list of OHLCV klines.

    Returns a dict with keys: ``fast_ema``, ``slow_ema``, ``rsi``.
    """
    if not klines:
        raise ValueError("klines list is empty")
    closes = [k["close"] for k in klines]
    return {
        "fast_ema": _ema(closes, _FAST_PERIOD),
        "slow_ema": _ema(closes, _SLOW_PERIOD),
        "rsi": _rsi(closes, _RSI_PERIOD),
    }


def get_signal(klines: list[dict[str, Any]]) -> str:
    """Return ``"yes"`` (bet BTC goes UP) or ``"no"`` (bet BTC goes DOWN).

    Uses EMA crossover as the base signal with RSI as an override for
    extreme overbought / oversold conditions.
    """
    if len(klines) < 2:
        logger.warning("Not enough klines to compute signal; defaulting to 'yes'")
        return "yes"

    indicators = compute_indicators(klines)
    fast_ema = indicators["fast_ema"]
    slow_ema = indicators["slow_ema"]
    rsi = indicators["rsi"]

    logger.info(
        "Indicators → fast_ema=%.2f  slow_ema=%.2f  rsi=%.2f",
        fast_ema,
        slow_ema,
        rsi,
    )

    # RSI extremes override EMA signal
    if rsi >= _RSI_OVERBOUGHT:
        logger.info("RSI overbought (%.2f) → signal=no", rsi)
        return "no"
    if rsi <= _RSI_OVERSOLD:
        logger.info("RSI oversold (%.2f) → signal=yes", rsi)
        return "yes"

    # EMA crossover
    if fast_ema > slow_ema:
        logger.info("EMA crossover bullish → signal=yes")
        return "yes"
    logger.info("EMA crossover bearish → signal=no")
    return "no"
