"""
Mean Reversion Strategy for Kalshi Binary Options

Fades extreme moves in crypto markets rather than chasing trends.
Uses Black-Scholes probability pricing with volatility regimes and anti-momentum signals.

Completely isolated from CryptoProbStrategy - all utilities are self-contained.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections import deque

import requests

from .models import Market, Signal


# ============================= CONSTANTS ==============================

ASSET_CONFIG = {
    "KXBTC15M": {"product": "BTC-USD", "vol_mult": 1.00},
    "KXETH15M": {"product": "ETH-USD", "vol_mult": 1.10},
}

# API cache TTLs
_SPOT_CACHE_TTL = 20.0           # current spot
_OPEN_SPOT_CACHE_TTL = 300.0     # market-open strike
_VOL_CACHE_TTL = 60.0            # rolling vol

# ============================= CACHES ==============================

_spot_cache: dict[str, tuple[float, float]] = {}
_open_spot_cache: dict[tuple[str, int], tuple[float, float]] = {}
_vol_cache: dict[str, tuple[float, float]] = {}
_vol_history_cache: dict[str, deque] = {}  # 5-day rolling vol for regime detection


# ============================= MATH UTILITIES ==============================

def norm_cdf(x: float) -> float:
    """Cumulative normal distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_above_strike(
    spot_now: float,
    strike_price: float,
    secs_left: float,
    annualized_vol: float,
) -> float:
    """P(BTC at expiry > strike) under zero-drift log-normal model.

    Standard digital/binary call pricing: P = N(d2)
    where d2 = log(S / K) / (sigma * sqrt(T))
    """
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


def _compute_d2(spot: float, strike: float, secs_left: float, sigma: float) -> float:
    """Moneyness metric: how far from strike in vol units."""
    if spot <= 0 or strike <= 0 or sigma <= 0 or secs_left <= 0:
        return 0.0

    t_years = secs_left / (365.25 * 24 * 60 * 60)
    sigma_t = sigma * math.sqrt(max(t_years, 1e-12))
    return abs(math.log(spot / strike)) / max(sigma_t, 1e-9)


# ============================= COINBASE API ==============================

def _fetch_with_retry(url: str, params: dict | None = None, retries: int = 2) -> requests.Response:
    """Fetch from Coinbase with retry logic and backoff."""
    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp
        except requests.exceptions.Timeout as exc:
            last_exc = exc
            logging.warning("coinbase: timeout (attempt %d)", attempt + 1)
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
    """Return the Coinbase spot at market open time."""
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
        logging.warning("coinbase: could not fetch open spot: %s", exc)
        return None


def fetch_rolling_vol(product: str, vol_mult: float = 1.0, lookback_minutes: int = 20) -> float | None:
    """Realized vol from rolling 20-minute window of 1-minute returns."""
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
            logging.warning("coinbase: not enough candles for vol (%s)", product)
            return None

        closes = [float(c[4]) for c in candles[-lookback_minutes:]]
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

        variance = sum(r ** 2 for r in log_returns) / len(log_returns)
        std_per_minute = math.sqrt(max(variance, 1e-20))

        minutes_per_year = 365.25 * 24 * 60
        sigma = std_per_minute * math.sqrt(minutes_per_year) * vol_mult
        sigma = max(0.20, min(2.50, sigma))

        _vol_cache[product] = (sigma, now_mono)

        # Track vol for regime detection
        if product not in _vol_history_cache:
            _vol_history_cache[product] = deque(maxlen=288)  # 5 days @ 1 sample/min
        _vol_history_cache[product].append(sigma)

        return sigma
    except Exception as exc:
        logging.warning("coinbase: could not fetch rolling vol: %s", exc)
        return None


def get_average_vol_5d(product: str) -> float | None:
    """5-day rolling average volatility for regime detection."""
    if product not in _vol_history_cache or len(_vol_history_cache[product]) < 10:
        return None
    return sum(_vol_history_cache[product]) / len(_vol_history_cache[product])


def fetch_5m_momentum(product: str, spot_now: float) -> float:
    """Calculate 5-minute price momentum.

    Returns: (spot_now - spot_5m_ago) / spot_5m_ago
    """
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
        if spot_5m_ago <= 0:
            return 0.0

        return (spot_now - spot_5m_ago) / spot_5m_ago
    except Exception as e:
        logging.debug("mean_reversion: momentum calc failed: %s", e)
        return 0.0


# ============================= STRATEGY ==============================

def _asset_prefix_from_ticker(ticker: str) -> str | None:
    """Extract asset prefix (e.g., 'KXBTC15M') from ticker."""
    t = (ticker or "").upper()
    for prefix in ASSET_CONFIG:
        if t.startswith(prefix):
            return prefix
    return None


def _compute_position_size(edge_cents: int, confidence: float) -> int:
    """Dynamic position sizing based on edge and confidence.

    Base 1 contract, scale up with confidence.
    """
    # Confidence-based: 1 + (confidence - 0.5) / 0.25, capped at 5
    size = 1 + int((max(0.5, confidence) - 0.5) / 0.25)
    return max(1, min(5, size))


@dataclass
class MeanReversionStrategy:
    """Mean reversion strategy for binary options.

    Fades extreme moves using:
    - Volatility regime detection
    - Moneyness (d2 statistic)
    - Anti-momentum signals
    - Black-Scholes fair value
    """

    min_edge_cents: int = 4          # Lower threshold for MR vs trend-following
    max_edge_cents: int = 45         # Reject implausible edges
    max_spread_cents: int = 10       # Reject illiquid markets
    min_score: float = 4.0           # MR needs lower score than trending
    vol_regime_high_mult: float = 1.2   # Vol trigger for high regime
    vol_regime_low_mult: float = 0.8    # Vol trigger for low regime

    def _calculate_anti_momentum_boost(self, spot_now: float, product: str, side: str) -> float:
        """Boost confidence when price momentum OPPOSES trade direction (mean revert).

        If price went up fast but we're betting NO (pullback):
          → strong mean reversion signal, boost confidence
        If price went down but we're betting YES (bounce):
          → strong mean reversion signal, boost confidence
        """
        try:
            momentum = fetch_5m_momentum(product, spot_now)

            # Normalize to [-1, 1]
            alignment = min(1.0, abs(momentum) / 0.01)

            # Check if momentum opposes trade direction
            if side == "yes" and momentum < -0.005:  # Down move but betting YES (bounce)
                return alignment * 0.10
            elif side == "no" and momentum > 0.005:   # Up move but betting NO (pullback)
                return alignment * 0.10
            else:
                return 0.0
        except Exception as e:
            logging.debug("mean_reversion: anti-momentum calc failed: %s", e)
            return 0.0

    def _calculate_vol_regime_boost(self, product: str, sigma: float) -> float:
        """Boost confidence in high vol regimes (more likely to revert).

        Reduce confidence in low vol (moves more sticky).
        """
        avg_vol = get_average_vol_5d(product)
        if avg_vol is None:
            return 0.0

        vol_ratio = sigma / avg_vol

        if vol_ratio > self.vol_regime_high_mult:
            # High vol → confidence boost
            return min(0.15, (vol_ratio - self.vol_regime_high_mult) * 0.5)
        elif vol_ratio < self.vol_regime_low_mult:
            # Low vol → confidence penalty
            return -min(0.10, (self.vol_regime_low_mult - vol_ratio) * 0.3)
        else:
            return 0.0

    def evaluate(self, market: Market) -> Signal | None:
        """Generate mean reversion signal or None."""

        prefix = _asset_prefix_from_ticker(market.ticker)
        if prefix is None or not market.ticker.startswith(("KXBTC15M", "KXETH15M")):
            return None

        cfg = ASSET_CONFIG[prefix]
        product = cfg["product"]
        vol_mult = cfg["vol_mult"]

        # ---- market price ----------------------------------------- #
        sane_book = (
            market.yes_bid >= 0
            and market.yes_ask > 0
            and market.yes_bid < market.yes_ask
        )
        if sane_book and market.yes_bid > 0:
            market_price = (market.yes_bid + market.yes_ask) / 2.0
        elif market.last_price > 0:
            market_price = float(market.last_price)
        else:
            return None

        if market_price <= 1 or market_price >= 99:
            return None

        spread = max(0.0, market.yes_ask - market.yes_bid)
        if spread > self.max_spread_cents:
            logging.debug("mean_reversion: REJECT %s spread too wide: %.1f", market.ticker, spread)
            return None

        secs_left = market.secs_left
        if secs_left is None or secs_left < 30:
            logging.debug("mean_reversion: REJECT %s insufficient time left", market.ticker)
            return None

        # ---- current spot ----------------------------------------- #
        try:
            spot_now = fetch_spot(product)
        except Exception as exc:
            logging.warning("mean_reversion: could not fetch spot: %s", exc)
            return None

        # ---- strike ------------------------------------------------ #
        if market.kalshi_strike and market.kalshi_strike > 0:
            strike_price = market.kalshi_strike
        else:
            strike_price = fetch_spot_at_open(product, secs_left)
            if strike_price is None or strike_price <= 0:
                logging.warning("mean_reversion: could not fetch strike")
                return None

        # ---- volatility ------------------------------------------- #
        sigma = fetch_rolling_vol(product, vol_mult=vol_mult, lookback_minutes=20)
        if sigma is None:
            sigma = 0.80 * vol_mult  # fallback

        # ---- moneyness & confidence -------------------------------- #
        # Moneyness tells us how extreme the move is
        d2 = _compute_d2(spot_now, strike_price, secs_left, sigma)

        # Base confidence from moneyness
        if d2 >= 2.0:
            confidence = 0.90    # deep ITM/OTM
        elif d2 >= 1.5:
            confidence = 0.80    # strong setup
        elif d2 >= 1.0:
            confidence = 0.70    # moderate setup
        elif d2 >= 0.5:
            confidence = 0.65    # weak setup
        else:
            # At-the-money: skip (no edge to fade)
            logging.debug("mean_reversion: REJECT %s at-the-money (d2=%.2f)", market.ticker, d2)
            return None

        # Vol regime boost
        vol_boost = self._calculate_vol_regime_boost(product, sigma)
        confidence = max(0.5, min(1.0, confidence + vol_boost))

        # ---- anti-momentum ------------------------------------------ #
        # Determine initial side based on price vs strike (mean revert tendency)
        # If price is above strike, expect pullback to strike (bet NO)
        # If price is below strike, expect bounce to strike (bet YES)
        if spot_now >= strike_price:
            initial_side = "no"   # expect pullback
        else:
            initial_side = "yes"  # expect bounce

        # Anti-momentum boost when momentum opposes our reversion bet
        anti_momentum_boost = self._calculate_anti_momentum_boost(spot_now, product, initial_side)
        confidence = min(1.0, confidence + anti_momentum_boost)

        # ---- fair value & edge -------------------------------------- #
        fair_prob = prob_above_strike(
            spot_now=spot_now,
            strike_price=strike_price,
            secs_left=secs_left,
            annualized_vol=sigma,
        )
        fair_cents = fair_prob * 100.0
        raw_edge = fair_cents - market_price

        if abs(raw_edge) > self.max_edge_cents:
            logging.debug("mean_reversion: REJECT %s edge too large: %.1f", market.ticker, raw_edge)
            return None

        if abs(raw_edge) < self.min_edge_cents:
            logging.debug("mean_reversion: REJECT %s edge too small: %.1f", market.ticker, raw_edge)
            return None

        # ---- position sizing ---------------------------------------- #
        position_size = _compute_position_size(int(round(abs(raw_edge))), confidence)

        # ---- score -------------------------------------------------- #
        spread_penalty = spread * 0.15
        adjusted_edge = (abs(raw_edge) * confidence) - spread_penalty

        if adjusted_edge < self.min_score:
            logging.debug("mean_reversion: REJECT %s score too low: %.2f", market.ticker, adjusted_edge)
            return None

        # Determine side based on highest edge direction
        side = "yes" if raw_edge > 0 else "no"

        logging.info(
            "mean_reversion: KEEP %s side=%s spot=%.2f strike=%.2f market=%.1f fair=%.1f "
            "raw_edge=%.1f d2=%.2f conf=%.2f vol_boost=%.2f momentum_boost=%.2f score=%.2f size=%d sigma=%.2f",
            market.ticker, side, spot_now, strike_price, market_price, fair_cents,
            raw_edge, d2, confidence, vol_boost, anti_momentum_boost, adjusted_edge, position_size, sigma,
        )

        return Signal(
            ticker=market.ticker,
            title=market.title,
            side=side,
            price=max(1, min(99, int(round(market_price)))),
            edge_cents=int(round(abs(raw_edge))),
            spread_cents=int(round(spread)),
            score=float(round(adjusted_edge, 2)),
            reason=(
                f"mean_reversion: spot={spot_now:.2f}, strike={strike_price:.2f}, "
                f"d2={d2:.2f}, conf={confidence:.2f}, vol={sigma:.2f}, "
                f"fair={fair_cents:.1f}, position_size={position_size}"
            ),
        )
