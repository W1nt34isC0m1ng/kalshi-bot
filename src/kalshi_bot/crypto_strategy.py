from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .client import KalshiHttpClient
from .coinbase import (
    ASSET_CONFIG,
    asset_prefix_from_ticker,
    compute_d2,
    compute_implied_vol,
    fetch_5m_momentum,
    fetch_rolling_vol,
    fetch_spot,
    fetch_spot_at_open,
    fetch_trend_strength,
    prob_above_strike,
)
from .models import Market, Signal


@dataclass
class CryptoProbStrategy:
    client: KalshiHttpClient | None = None

    min_edge_cents: int = 6
    max_edge_cents: int = 30
    max_spread_cents: int = 10
    min_score: float = 6.0
    momentum_scaling_factor: float = 0.15
    # Cap on |d2| (model confidence). Originally added because high d2 had 0%
    # WR — but that was because sigma was floored at 0.50, producing spuriously
    # confident model output. Now that Parkinson HLOC gives realistic sigma,
    # journal data shows d2 > 1.2 wins ~44% of the time. The cap stays for a
    # *different* reason: deep-strike trades have brutally asymmetric payoffs
    # (win +8c, lose -92c on a NO@92), so even 44% WR is a structural loser.
    # Per-trade pnl in d2 1.2-1.5 was -63c vs -32c at 0.9-1.2 (real-sigma data).
    max_d2: float = 1.2
    # Block trades that bet AGAINST a confirmed directional trend.
    # A NO bet in an uptrend (or YES in a downtrend) fought momentum and caused
    # most of April 22's losses. fetch_trend_strength returns a signed value:
    # positive = uptrend, negative = downtrend.
    max_trend_strength: float = 1.0

    def _resolve_sigma(
        self,
        market_price: float,
        spot: float,
        strike: float,
        secs_left: float,
        vol_mult: float,
    ) -> float:
        """Best available sigma estimate.

        Priority:
          1. Market-implied vol from Kalshi mid-price  — the market itself
             is pricing how uncertain the outcome is. Use it when available.
          2. Rolling 20-min realized vol from Coinbase candles.
          3. Hard fallback.

        We take max(implied, historical) so we never use a sigma the market
        is already telling us is too low.
        """
        # Historical realized vol (also warms the candle cache)
        hist_sigma = fetch_rolling_vol("BTC-USD", vol_mult=vol_mult, lookback_minutes=20)
        if hist_sigma is None:
            hist_sigma = 0.80 * vol_mult

        # Market-implied vol — back out sigma from the Kalshi mid-price
        market_frac = market_price / 100.0
        implied = compute_implied_vol(market_frac, spot, strike, secs_left)

        if implied is not None:
            sigma = max(hist_sigma, implied)
            logging.debug(
                "strategy: sigma hist=%.2f implied=%.2f → using %.2f",
                hist_sigma, implied, sigma,
            )
        else:
            sigma = hist_sigma

        return sigma

    def _momentum_boost(self, spot_now: float, product: str, side: str) -> float:
        """Confidence boost when 5-minute momentum aligns with trade direction."""
        try:
            return_5m = fetch_5m_momentum(product, spot_now)
            if side == "yes" and return_5m > 0:
                alignment = min(1.0, abs(return_5m) / 0.01)
            elif side == "no" and return_5m < 0:
                alignment = min(1.0, abs(return_5m) / 0.01)
            else:
                alignment = 0.0
            return alignment * self.momentum_scaling_factor
        except Exception as exc:
            logging.debug("strategy: momentum boost failed: %s", exc)
            return 0.0

    def evaluate(self, market: Market) -> Signal | None:
        prefix = asset_prefix_from_ticker(market.ticker)
        if prefix is None:
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
        if secs_left is None or secs_left < 30:
            logging.debug("strategy: REJECT %s secs_left too low", market.ticker)
            return None

        # ---- current spot ----------------------------------------- #
        try:
            spot_now = fetch_spot(product)
        except Exception as exc:
            logging.warning("strategy: could not fetch spot for %s: %s — skipping", product, exc)
            return None

        # ---- strike ----------------------------------------------- #
        if market.kalshi_strike and market.kalshi_strike > 0:
            strike_price = market.kalshi_strike
        else:
            strike_price = fetch_spot_at_open(product, secs_left)
            if strike_price is None or strike_price <= 0:
                logging.warning("strategy: could not fetch strike for %s — skipping", market.ticker)
                return None

        # ---- volatility ------------------------------------------- #
        # Uses max(realized, implied) so sigma is never lower than what the
        # Kalshi market itself is pricing in.
        sigma = self._resolve_sigma(market_price, spot_now, strike_price, secs_left, vol_mult)

        # ---- d2 guard --------------------------------------------- #
        # High d2 = model thinks outcome is "nearly certain" but this is only
        # true if sigma is correct. Since sigma can still be off, we cap d2
        # to avoid the overconfident deep-OTM/ITM zone that lost 100% in backtest.
        d2 = compute_d2(spot_now, strike_price, secs_left, sigma)
        if d2 > self.max_d2:
            logging.debug(
                "strategy: REJECT %s d2 too high: %.2f > %.2f (overconfidence guard)",
                market.ticker, d2, self.max_d2,
            )
            return None

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

        side = "yes" if raw_edge > 0 else "no"

        # ---- directional trend filter ----------------------------- #
        # Only block trades that OPPOSE a confirmed trend. A NO bet into a
        # rising market (or YES into a falling one) is the primary failure mode
        # from April 22 — crypto_prob kept calling the market wrong because BTC
        # had sustained directional momentum the BS model doesn't account for.
        # Trend-aligned trades (NO in downtrend, YES in uptrend) are allowed
        # through — those benefit from momentum, not fight it.
        trend = fetch_trend_strength(product, spot_now, sigma, lookback_minutes=20)
        if abs(trend) > self.max_trend_strength:
            opposing = (side == "no" and trend > 0) or (side == "yes" and trend < 0)
            if opposing:
                logging.info(
                    "strategy: REJECT %s side=%s opposes trend (trend=%.2f, threshold=%.2f)",
                    market.ticker, side, trend, self.max_trend_strength,
                )
                return None

        # ---- confidence (smooth sigmoid over d2) ------------------- #
        confidence = 0.55 + 0.45 * math.tanh(d2)

        # ---- momentum boost --------------------------------------- #
        momentum_boost = self._momentum_boost(spot_now, product, side)
        confidence = min(1.0, confidence + momentum_boost)

        # ---- score ------------------------------------------------- #
        premium_cents = market_price if side == "yes" else (100.0 - market_price)
        ev_cents = abs(raw_edge)
        ev_roi = ev_cents / max(premium_cents, 1e-9)

        spread_penalty = spread * 0.15
        adjusted_edge = (abs(raw_edge) * confidence) - spread_penalty

        if adjusted_edge < self.min_score:
            logging.debug("strategy: REJECT %s score too low: %.2f", market.ticker, adjusted_edge)
            return None

        logging.info(
            "strategy: KEEP %s side=%s spot=%.2f strike=%.2f market=%.1f fair=%.1f "
            "raw_edge=%.1f d2=%.2f conf=%.2f momentum_boost=%.2f score=%.2f sigma=%.2f secs_left=%.0f trend=%.2f",
            market.ticker, side, spot_now, strike_price, market_price, fair_cents,
            raw_edge, d2, confidence - momentum_boost, momentum_boost, adjusted_edge, sigma, secs_left, trend,
        )

        return Signal(
            ticker=market.ticker,
            title=market.title,
            side=side,
            price=max(1, min(99, int(round(market_price)))),
            edge_cents=int(round(abs(raw_edge))),
            ev_cents=float(round(ev_cents, 2)),
            ev_roi=float(round(ev_roi, 4)),
            spread_cents=int(round(spread)),
            score=float(round(adjusted_edge, 2)),
            reason=(
                f"asset={prefix}, spot={spot_now:.2f}, strike={strike_price:.2f}, "
                f"secs_left={secs_left:.0f}, sigma={sigma:.2f}, d2={d2:.2f}, "
                f"fair={fair_cents:.1f}, market={market_price:.1f}, ev={ev_cents:.1f}, "
                f"ev_roi={ev_roi:.4f}, conf={confidence - momentum_boost:.2f}, "
                f"momentum_boost={momentum_boost:.2f}, trend={trend:.2f}"
            ),
            momentum_boost=momentum_boost,
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            strategy="crypto_prob",
        )
