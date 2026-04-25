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


def norm_ppf(p: float) -> float:
    """Inverse normal CDF via Acklam's rational approximation (error < 1e-9).

    Used to back out implied vol from binary option prices without scipy.
    Reference: Peter Acklam, https://web.archive.org/web/20151030215612/
               http://home.online.no/~pjacklam/notes/invnorm/
    """
    p = max(1e-9, min(1 - 1e-9, p))

    a = (-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00)
    b = (-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00)
    d = ( 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00)

    p_lo, p_hi = 0.02425, 1.0 - 0.02425

    if p < p_lo:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    elif p <= p_hi:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)


def compute_implied_vol(
    market_price_frac: float,
    spot: float,
    strike: float,
    secs_left: float,
) -> float | None:
    """Back out annualized vol implied by the binary option's market price.

    For a binary call (YES contract): market_price = N(d2)
      → d2_implied = N^{-1}(market_price)
      → sigma_implied = log(spot/strike) / (d2_implied * sqrt(T))

    Returns None when the inputs are degenerate (extreme prices, near-expiry,
    spot == strike, etc.).  Caller should fall back to historical sigma.

    Key insight: if the Kalshi market is pricing YES at 0.30 but our model
    says fair=0.85, one of two things is true — either the market is wrong
    (edge!) or our sigma is far too low.  This function tells us which.
    """
    if not (0.03 <= market_price_frac <= 0.97):
        return None
    if spot <= 0 or strike <= 0 or secs_left < 30:
        return None

    log_moneyness = math.log(spot / strike)
    if abs(log_moneyness) < 1e-8:
        return None  # exactly ATM — d2=0, sigma undefined

    t_years = secs_left / (365.25 * 24 * 3600)
    sqrt_t = math.sqrt(max(t_years, 1e-12))

    d2_implied = norm_ppf(market_price_frac)
    if abs(d2_implied) < 1e-6:
        return None

    sigma_implied = log_moneyness / (d2_implied * sqrt_t)

    # Negative implied vol means the market price and log-moneyness have
    # inconsistent signs — a stale quote or model mismatch. Discard.
    if sigma_implied <= 0:
        return None

    # Clamp to a sane range — below 0.20 is noise; above 10.0 is a bad quote
    return max(0.20, min(10.0, sigma_implied))


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

        # Parkinson HLOC estimator. Uses each candle's high/low range, which is
        # ~5x more statistically efficient than close-to-close because it captures
        # intra-minute movement we'd otherwise discard. Coinbase candle format:
        # [time, low, high, open, close, volume].
        # Variance per minute = (1 / (4 ln 2)) * mean( ln(H/L)^2 )
        # Diagnostic in trade journal showed close-to-close was hitting the 0.50
        # floor 41% of the time; trades fired at the floor won at 14% vs 37%
        # otherwise. Switching estimators removes the need for an aggressive floor.
        recent = candles[-lookback_minutes:]
        hl_terms = []
        for c in recent:
            low, high = float(c[1]), float(c[2])
            if low > 0 and high > low:
                hl_terms.append(math.log(high / low) ** 2)

        if len(hl_terms) < 5:
            logging.warning("coinbase: insufficient HL ranges for vol estimate (%s)", product)
            return None

        parkinson_var = sum(hl_terms) / len(hl_terms) / (4 * math.log(2))
        std_per_minute = math.sqrt(max(parkinson_var, 1e-20))

        minutes_per_year = 365.25 * 24 * 60
        sigma = std_per_minute * math.sqrt(minutes_per_year) * vol_mult
        # Low safety floor only for degenerate data (flat candles → sigma → 0
        # → d2 → infinity, model thinks every trade is a sure thing). Parkinson
        # gives us realistic numbers, so this should rarely bind.
        sigma = max(0.10, min(4.00, sigma))

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


def fetch_trend_strength(product: str, spot_now: float, sigma: float, lookback_minutes: int = 20) -> float:
    """Trend strength: total directional move over lookback, normalized by expected vol.

    Returns a z-score-style value:
      < 1.0  → quiet / ranging — mean reversion has a good environment
      1.0–1.5 → moderate trend — borderline
      > 1.5  → strong trend — mean reversion should stand down

    Reuses candles already cached by fetch_rolling_vol, so no extra API call.
    Also checks directional consistency: what fraction of 1-min candles moved
    the same way as the overall trend, to distinguish a smooth trend from a
    noisy spike that happens to net a big move.
    """
    now_mono = time.monotonic()
    cached_candles, cached_at = _candles_cache.get(product, ([], 0.0))

    if not cached_candles or (now_mono - cached_at) >= _VOL_CACHE_TTL:
        return 0.0

    if len(cached_candles) < 5:
        return 0.0

    # Find candle closest to lookback_minutes ago
    target_ts = time.time() - (lookback_minutes * 60)
    past_candle = min(cached_candles, key=lambda c: abs(c[0] - target_ts))
    spot_past = float(past_candle[4])

    if spot_past <= 0 or sigma <= 0:
        return 0.0

    # Total log return over window (unsigned)
    log_return = abs(math.log(spot_now / spot_past))

    # Expected vol over the same window
    t_years = (lookback_minutes * 60) / (365.25 * 24 * 3600)
    expected_move = sigma * math.sqrt(max(t_years, 1e-12))

    magnitude = log_return / expected_move

    # Directional consistency: fraction of 1-min candles that closed in the
    # direction of the overall trend.  A smooth trend has consistency > 0.6;
    # a spike-and-reverse has consistency closer to 0.5.
    direction = 1 if spot_now >= spot_past else -1
    recent = cached_candles[-lookback_minutes:]
    if len(recent) >= 2:
        same_dir = sum(
            1 for i in range(1, len(recent))
            if (float(recent[i][4]) - float(recent[i - 1][4])) * direction > 0
        )
        consistency = same_dir / (len(recent) - 1)
    else:
        consistency = 0.5

    # Blend magnitude and consistency: a big move with consistent direction
    # is a much stronger trend signal than a big move with random candles.
    # Return signed: positive = uptrend, negative = downtrend.
    # Callers that only care about magnitude should use abs(result).
    trend_strength = direction * magnitude * (0.5 + consistency)

    logging.debug(
        "coinbase: trend_strength %s magnitude=%.2f consistency=%.2f direction=%+d strength=%.2f",
        product, magnitude, consistency, direction, trend_strength,
    )
    return trend_strength


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
