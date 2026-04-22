"""Fetch live BTC/USDT price data from the Binance public REST API.

No API key is required for these public endpoints.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BINANCE_BASE = "https://api.binance.com/api/v3"
_DEFAULT_TIMEOUT = 10


def get_btc_price() -> float:
    """Return the latest BTC/USDT spot price."""
    resp = requests.get(
        f"{_BINANCE_BASE}/ticker/price",
        params={"symbol": "BTCUSDT"},
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    price = float(resp.json()["price"])
    logger.debug("BTC spot price: %.2f", price)
    return price


def get_btc_klines(interval: str = "1m", limit: int = 15) -> list[dict[str, Any]]:
    """Return recent OHLCV candles for BTC/USDT.

    Args:
        interval: Binance kline interval string, e.g. ``"1m"``, ``"5m"``.
        limit:    Number of candles to return (max 1000).

    Returns:
        List of dicts with keys:
        ``open_time``, ``open``, ``high``, ``low``, ``close``, ``volume``.
    """
    resp = requests.get(
        f"{_BINANCE_BASE}/klines",
        params={"symbol": "BTCUSDT", "interval": interval, "limit": limit},
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    klines = [
        {
            "open_time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in resp.json()
    ]
    logger.debug("Fetched %d klines (interval=%s)", len(klines), interval)
    return klines
