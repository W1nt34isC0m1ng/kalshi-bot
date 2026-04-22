"""Shared Coinbase API utilities — single source of truth for all strategies.

All caches live here so both CryptoProbStrategy and MeanReversionStrategy share
the same data without redundant API calls.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import requests


ASSET_CONFIG = {
    "KXBTC15M": {"product": "BTC-USD", "vol_mult": 1.00},
    "KXETH15M": {"product": "ETH-USD", "vol_mult": 1.10},
}

_SPOT_CACHE_TTL = 20.0
_OPEN_SPOT_CACHE_TTL = 300.0
_VOL_CACHE_TTL = 60.0

# {product: (price, fetched_at)}
_spot_cache: dict[str, tuple[float, float]] = {}

# {(product, minute_bucket): (price, fetched_at)}
_open_spot_cache: dict[tuple[str, int], tuple[float, float]] = {}

# {product: (sigma, fetched_at)}
_vol_cache: dict[str, tuple[float, float]] = {}

# {product: (sorted_candles, fetched_at)} — reused by fetch_5m_momentum
_candles_cache: dict[str, tuple[list, float]] = {}

# {product: deque of sigma samples} — 5-day rolling history for regime detection
_vol_history_cache: dict[str, deque] = {}


# ------------------------------------------------------------------ #
# Math utilities                                                       #
# ------------------------------------------------------------------ #

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_above_strike(
    spot_now: float,
    strike_price: float,
    secs_left: float,
    annualized_vol: float,
) -> float:
    """P(price at expiry > strike) under zero-drift log-normal model — N(d2)."""
    if spot_now <= 0 or strike_price <= 0:
        return 0.50
    if secs_left <= 1:
        return 1.0 if spot_now > strike_price else 0.0

    t_years = secs_left / (365.25 * 24 * 60 * 60)
    sigma_t = annualized_vol * math.sqrt(max(t_years, 1e-12))
    if sigma_t <= 0:
        return 1.0 if spot_now > strike_price else 0.0

    d2 = math.log(spot_now / strike_price) / sigma_t
    return max(0.02, min(0.98, norm_cdf(d2)))


def compute_d2(spot: float, strike: float, secs_left: float, sigma: float) -> float:
    """Distance from strike in vol units (unsigned moneyness)."""
    if spot <= 0 or strike <= 0 or sigma <= 0 or secs_left <= 0:
        return 0.0
    t_years = secs_left / (365.25 * 24 * 60 * 60)
    sigma_t = sigma * math.sqrt(max(t_years, 1e-12))
    return abs(math.log(spot / strike)) / max(sigma_t, 1e-9)


def asset_prefix_from_ticker(ticker: str) -> str | None:
    t = (ticker or "").upper()
    for prefix in ASSET_CONFIG:
        if t.startswith(prefix):
            return prefix
    return None


# ------------------------------------------------------------------ #
# HTTP                                                                 #
# ------------------------------------------------------------------ #

def _fetch_with_retry(url: str, params: dict | None = None, retries: int = 2) -> requests.Response:
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout as exc:
            last_exc = exc
            logging.warning("coinbase: timeout fetching %s (attempt %d)", url, attempt + 1)
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                wait = 0.5 * (2 ** attempt)
                logging.warning("coinbase: rate-limited, waiting %.1fs", wait)
                time.sleep(wait)
                last_exc = exc
            else:
                raise
        except Exception as exc:
            last_exc = exc
            logging.warning("coinbase: error fetching %s: %s", url, exc)
    raise last_exc


# ------------------------------------------------------------------ #
# Price fetching                                                       #
# ------------------------------------------------------------------ #

def fetch_spot(product: str) -> float:
    """Current Coinbase spot price, cached for _SPOT_CACHE_TTL seconds."""
    now = time.monotonic()
    cached_price, cached_at = _spot_cache.get(product, (0.0, 0.0))
    if cached_price > 0 and (now - cached_at) < _SPOT_CACHE_TTL:
        return cached_price

    resp = _fetch_with_retry(f"https://api.coinbase.com/v2/prices/{product}/spot")
    price = float(resp.json()["data"]["amount"])
    _spot_cache[product] = (price, now)
    return price


def fetch_spot_at_open(product: str, secs_left: float) -> float | None:
    """Coinbase spot at the moment this 15-minute market opened (the strike).

    Cached per minute-bucket so the same strike isn't re-fetched every loop.
    """
    seconds_since_open = max(0.0, 900.0 - secs_left)
    bucket = int(seconds_since_open / 60)
    cache_key = (product, bucket)

    now_mono = time.monotonic()
    cached_price, cached_at = _open_spot_cache.get(cache_key, (0.0, 0.0))
    if cached_price > 0 and (now_mono - cached_at) < _OPEN_SPOT_CACHE_TTL:
        return cached_price

    if seconds_since_open < 30:
        price = fetch_spot(product)
        _open_spot_cache[cache_key] = (price, now_mono)
        return price

    now_utc = datetime.now(timezone.utc)
    target = now_utc - timedelta(seconds=seconds_since_open)
    start = target - timedelta(minutes=5)
    end = target + timedelta(minutes=2)

    try:
        resp = _fetch_with_retry(
            f"https://api.exchange.coinbase.com/products/{product}/candles",
            params={
                "granularity": 60,
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
            },
        )
        candles = resp.json()
        if not candles:
            return None
        target_ts = target.timestamp()
        best = min(candles, key=lambda c: abs(c[0] - target_ts))
        price = float(best[4])
        _open_spot_cache[cache_key] = (price, now_mono)
        return price
    except Exception as exc:
        logging.warning("coinbase: could not fetch open spot for %s: %s", product, exc)
        return None


def fetch_rolling_vol(product: str, vol_mult: float = 1.0, lookback_minutes: int = 20) -> float | None:
    """Realized vol from a rolling window of 1-minute log-returns.

    Also populates _candles_cache so fetch_5m_momentum can reuse the same
    candles without an extra API round-trip.
    """
    now_mono = time.monotonic()
    cached_sigma, cached_at = _vol_cache.get(product, (0.0, 0.0))
    if cached_sigma > 0 and (now_mono - cached_at) < _VOL_CACHE_TTL:
        return cached_sigma

    now_utc = datetime.now(timezone.utc)
    start = now_utc - timedelta(minutes=lookback_minutes + 3)

    try:
        resp = _fetch_with_retry(
            f"https://api.exchange.coinbase.com/products/{product}/candles",
            params={
                "granularity": 60,
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": now_utc.isoformat().replace("+00:00", "Z"),
            },
        )
        candles = sorted(resp.json(), key=lambda c: c[0])
        if len(candles) < 5:
            logging.warning("coinbase: not enough candles for vol estimate (%s)", product)
            return None

        _candles_cache[product] = (candles, now_mono)

        closes = [float(c[4]) for c in candles[-lookback_minutes:]]
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

        variance = sum(r ** 2 for r in log_returns) / len(log_returns)
        std_per_minute = math.sqrt(max(variance, 1e-20))

        minutes_per_year = 365.25 * 24 * 60
        sigma = std_per_minute * math.sqrt(minutes_per_year) * vol_mult
        sigma = max(0.20, min(2.50, sigma))

        _vol_cache[product] = (sigma, now_mono)

        if product not in _vol_history_cache:
            _vol_history_cache[product] = deque(maxlen=288)  # ~5 days at 1/min
        _vol_history_cache[product].append(sigma)

        return sigma
    except Exception as exc:
        logging.warning("coinbase: could not fetch rolling vol for %s: %s", product, exc)
        return None


def get_average_vol_5d(product: str) -> float | None:
    """5-day rolling average volatility for vol-regime detection."""
    history = _vol_history_cache.get(product)
    if not history or len(history) < 10:
        return None
    return sum(history) / len(history)


def fetch_5m_momentum(product: str, spot_now: float) -> float:
    """5-minute price return: (spot_now - spot_5m_ago) / spot_5m_ago.

    Reuses candles already cached by fetch_rolling_vol — no extra API call
    when vol has been fetched in the same loop iteration.
    """
    now_mono = time.monotonic()
    cached_candles, cached_at = _candles_cache.get(product, ([], 0.0))

    if cached_candles and (now_mono - cached_at) < _VOL_CACHE_TTL:
        target_ts = time.time() - 300.0
        best = min(cached_candles, key=lambda c: abs(c[0] - target_ts))
        spot_5m_ago = float(best[4])
        if spot_5m_ago > 0:
            return (spot_now - spot_5m_ago) / spot_5m_ago

    # Fallback: fetch directly (cold start before first vol call)
    try:
        now_utc = datetime.now(timezone.utc)
        start = now_utc - timedelta(minutes=7)
        end = now_utc - timedelta(minutes=4)
        resp = _fetch_with_retry(
            f"https://api.exchange.coinbase.com/products/{product}/candles",
            params={
                "granularity": 60,
                "start": start.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
            },
        )
        candles = resp.json()
        if not candles:
            return 0.0
        spot_5m_ago = float(sorted(candles, key=lambda c: c[0])[0][4])
        return (spot_now - spot_5m_ago) / spot_5m_ago if spot_5m_ago > 0 else 0.0
    except Exception as exc:
        logging.debug("coinbase: momentum fetch failed for %s: %s", product, exc)
        return 0.0
