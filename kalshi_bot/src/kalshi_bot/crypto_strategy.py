from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from .client import KalshiHttpClient
from .models import Market, Signal


ASSET_CONFIG = {
    "KXBTC15M": {"product": "BTC-USD", "vol_mult": 1.00},
    "KXETH15M": {"product": "ETH-USD", "vol_mult": 1.10},
}

# Coinbase API cache TTLs
_SPOT_CACHE_TTL = 20.0     # current spot: 20s
_OPEN_SPOT_CACHE_TTL = 300.0  # market-open strike: cache for 5 min (it's static per market)
_VOL_CACHE_TTL = 60.0      # rolling vol: recompute once per minute


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def asset_prefix_from_ticker(ticker: str) -> str | None:
    t = (ticker or "").upper()
    for prefix in ASSET_CONFIG:
        if t.startswith(prefix):
            return prefix
    return None


# ------------------------------------------------------------------ #
# Coinbase API — caches and retry                                      #
# ------------------------------------------------------------------ #

# {product: (price, fetched_at)}
_spot_cache: dict[str, tuple[float, float]] = {}

# {(product, minutes_since_open_bucket): (price, fetched_at)}
# Key rounds seconds_since_open to nearest 60s so the same open price is
# reused across loop iterations without refetching every 2 seconds.
_open_spot_cache: dict[tuple[str, int], tuple[float, float]] = {}

# {product: (sigma, fetched_at)}
_vol_cache: dict[str, tuple[float, float]] = {}


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
    """Return the Coinbase spot at the time this 15-minute market opened.

    For a KXBTC15M market that expires in `secs_left` seconds:
      - total window  = 900 s
      - market opened = 900 - secs_left seconds ago
      - that opening spot IS the strike the market resolves against

    We find the 1-minute candle whose timestamp is closest to the open time.
    The result is cached so we pay at most one extra API call per market per
    minute of the bot's loop, not one call per loop iteration.
    """
    seconds_since_open = max(0.0, 900.0 - secs_left)
    # Round to nearest 60 s for the cache key — fine-grained enough, avoids
    # refetching every loop while still tracking the correct minute bucket.
    bucket = int(seconds_since_open / 60)
    cache_key = (product, bucket)

    now_mono = time.monotonic()
    cached_price, cached_at = _open_spot_cache.get(cache_key, (0.0, 0.0))
    if cached_price > 0 and (now_mono - cached_at) < _OPEN_SPOT_CACHE_TTL:
        return cached_price

    if seconds_since_open < 30:
        # Market opened <30 s ago — current spot is effectively the strike.
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
        price = float(best[4])  # close of nearest 1-min candle
        _open_spot_cache[cache_key] = (price, now_mono)
        return price
    except Exception as exc:
        logging.warning("coinbase: could not fetch open spot for %s: %s", product, exc)
        return None


def fetch_rolling_vol(product: str, vol_mult: float = 1.0, lookback_minutes: int = 20) -> float | None:
    """Realized vol from a rolling window of recent 1-minute returns.

    Single-observation vol estimates (e.g. one 5-minute move) are extremely
    noisy and hit the clamp floor almost every call.  A 20-minute window of
    1-minute log-returns gives a much more stable estimate, at the cost of
    one extra candle fetch per minute (cached below).
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

        closes = [float(c[4]) for c in candles[-lookback_minutes:]]
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

        # Zero-mean realized variance (appropriate for short crypto windows)
        variance = sum(r ** 2 for r in log_returns) / len(log_returns)
        std_per_minute = math.sqrt(max(variance, 1e-20))

        minutes_per_year = 365.25 * 24 * 60
        sigma = std_per_minute * math.sqrt(minutes_per_year) * vol_mult
        sigma = max(0.20, min(2.50, sigma))

        _vol_cache[product] = (sigma, now_mono)
        return sigma
    except Exception as exc:
        logging.warning("coinbase: could not fetch rolling vol for %s: %s", product, exc)
        return None


# ------------------------------------------------------------------ #
# Probability math                                                     #
# ------------------------------------------------------------------ #

def prob_above_strike(
    spot_now: float,
    strike_price: float,
    secs_left: float,
    annualized_vol: float,
) -> float:
    """P(BTC at expiry > strike) under the zero-drift log-normal model.

    This is the standard digital/binary call pricing formula:
        P = N(d2)   where d2 = log(S / K) / (sigma * sqrt(T))

    Zero drift is the right assumption here because:
    - Risk-neutral pricing of a binary option uses zero drift
    - Physical drift over a 15-minute crypto window is negligible vs. vol
    - We have no reliable intraday drift forecast
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


# ------------------------------------------------------------------ #
# Strategy                                                            #
# ------------------------------------------------------------------ #

@dataclass
class CryptoProbStrategy:
    client: KalshiHttpClient | None = None

    min_edge_cents: int = 6
    # 45¢ cap rejects truly implausible edges that indicate a model failure
    # (e.g. wrong strike fetch, extreme vol spike).  All real edges trade.
    max_edge_cents: int = 45
    max_spread_cents: int = 10
    min_score: float = 6.0
    momentum_scaling_factor: float = 0.15  # Confidence boost (10-20% range) when price momentum aligns

    def _calculate_momentum_boost(self, spot_now: float, product: str, side: str) -> float:
        """Calculate confidence boost from 5-minute price momentum.

        Returns value in [0.0, momentum_scaling_factor] representing boost when price
        momentum aligns with trade direction.

        Args:
            spot_now: Current spot price
            product: Coinbase product (e.g., "BTC-USD")
            side: Trade side ("yes" or "no")

        Returns:
            Confidence boost (0.0 if momentum opposes or absent)
        """
        try:
            # Fetch 1-minute candles from ~5 minutes ago
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

            # Get spot from earliest candle (closest to 5 min ago)
            spot_5m_ago = float(sorted(candles, key=lambda c: c[0])[0][4])

            if spot_5m_ago <= 0:
                return 0.0

            # Calculate 5-minute return
            return_5m = (spot_now - spot_5m_ago) / spot_5m_ago

            # Determine if momentum aligns with trade direction
            if side == "yes" and return_5m > 0:
                # Betting YES and price is up — favorable momentum
                momentum_alignment = min(1.0, abs(return_5m) / 0.01)  # normalize 1% move
            elif side == "no" and return_5m < 0:
                # Betting NO and price is down — favorable momentum
                momentum_alignment = min(1.0, abs(return_5m) / 0.01)
            else:
                # Momentum opposes or is flat
                momentum_alignment = 0.0

            return momentum_alignment * self.momentum_scaling_factor

        except Exception as e:
            logging.debug("strategy: momentum calculation failed: %s", e)
            return 0.0

    def evaluate(self, market: Market) -> Signal | None:
        prefix = asset_prefix_from_ticker(market.ticker)
        if prefix is None:
            return None

        if not market.ticker.startswith(("KXBTC15M", "KXETH15M")):
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
            logging.debug("strategy: REJECT %s spread too wide: %.1f", market.ticker, spread)
            return None

        secs_left = market.secs_left
        if secs_left is None:
            logging.debug("strategy: REJECT %s missing secs_left", market.ticker)
            return None
        if secs_left < 30:
            return None

        # ---- current spot ----------------------------------------- #
        try:
            spot_now = fetch_spot(product)
        except Exception as exc:
            logging.warning("strategy: could not fetch spot for %s: %s — skipping", product, exc)
            return None

        # ---- actual market strike ---------------------------------- #
        # Prefer the authoritative Kalshi target parsed from the event title.
        # Fall back to fetching the Coinbase spot at market-open time only if
        # the title-based target is unavailable.
        if market.kalshi_strike and market.kalshi_strike > 0:
            strike_price = market.kalshi_strike
        else:
            strike_price = fetch_spot_at_open(product, secs_left)
            if strike_price is None or strike_price <= 0:
                logging.warning("strategy: could not fetch strike for %s — skipping", market.ticker)
                return None

        # ---- volatility ------------------------------------------- #
        # Primary: rolling 20-min realized vol from 1-min candles
        # Fallback: single 5-min observation (noisy but better than nothing)
        sigma = fetch_rolling_vol(product, vol_mult=vol_mult, lookback_minutes=20)
        if sigma is None:
            spot_5m_ago_resp = None
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
                if candles:
                    spot_5m_ago_resp = float(sorted(candles, key=lambda c: c[0])[0][4])
            except Exception:
                pass

            if spot_5m_ago_resp and spot_5m_ago_resp > 0:
                move = abs(math.log(spot_now / spot_5m_ago_resp))
                five_min_years = 300.0 / (365.25 * 24 * 3600)
                sigma = max(0.20, min(2.50, (move / math.sqrt(five_min_years)) * vol_mult))
            else:
                sigma = 0.80 * vol_mult  # last-resort default

        # ---- probability and edge ---------------------------------- #
        fair_prob = prob_above_strike(
            spot_now=spot_now,
            strike_price=strike_price,
            secs_left=secs_left,
            annualized_vol=sigma,
        )
        fair_cents = fair_prob * 100.0
        raw_edge = fair_cents - market_price

        if abs(raw_edge) > self.max_edge_cents:
            logging.debug(
                "strategy: REJECT %s edge implausibly large: %.1f (model error?)",
                market.ticker, raw_edge,
            )
            return None

        if abs(raw_edge) < self.min_edge_cents:
            logging.debug("strategy: REJECT %s raw_edge too small: %.1f", market.ticker, raw_edge)
            return None

        # ---- confidence ------------------------------------------- #
        # Confidence measures how far the current spot is from the strike
        # in units of remaining vol (|d2|).  Large |d2| means the outcome is
        # nearly determined; small |d2| means we're at the money and a coin
        # flip either way.  This is physically correct: near-expiry deep
        # ITM/OTM positions deserve MORE confidence, not less.
        sigma_t = sigma * math.sqrt(max(secs_left, 1.0) / (365.25 * 24 * 3600))
        d2 = abs(math.log(spot_now / strike_price)) / max(sigma_t, 1e-9)

        if d2 >= 2.0:
            confidence = 1.00   # deep ITM/OTM — outcome nearly certain
        elif d2 >= 1.0:
            confidence = 0.85
        elif d2 >= 0.5:
            confidence = 0.70
        else:
            confidence = 0.55   # at the money — genuine coin flip

        # ---- momentum boost ---------------------------------------- #
        # Amplify confidence when recent price movement aligns with trade direction
        side = "yes" if raw_edge > 0 else "no"
        momentum_boost = self._calculate_momentum_boost(spot_now, product, side)
        confidence = min(1.0, confidence + momentum_boost)

        # spread_penalty: 0.15¢ per cent of spread, accounts for the cost
        # of getting lifted / adverse fill at expiry.
        spread_penalty = spread * 0.15
        adjusted_edge = (abs(raw_edge) * confidence) - spread_penalty

        if adjusted_edge < self.min_score:
            logging.debug(
                "strategy: REJECT %s score too low: %.2f", market.ticker, adjusted_edge
            )
            return None

        # Note: side is already determined above in momentum boost calculation


        logging.info(
            "strategy: KEEP %s side=%s spot=%.2f strike=%.2f market=%.1f fair=%.1f "
            "raw_edge=%.1f d2=%.2f conf=%.2f momentum_boost=%.2f score=%.2f sigma=%.2f secs_left=%.0f",
            market.ticker, side, spot_now, strike_price, market_price, fair_cents,
            raw_edge, d2, confidence - momentum_boost, momentum_boost, adjusted_edge, sigma, secs_left,
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
                f"asset={prefix}, spot={spot_now:.2f}, strike={strike_price:.2f}, "
                f"secs_left={secs_left:.0f}, sigma={sigma:.2f}, d2={d2:.2f}, "
                f"fair={fair_cents:.1f}, market={market_price:.1f}, conf={confidence - momentum_boost:.2f}, "
                f"momentum_boost={momentum_boost:.2f}"
            ),
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
        )
