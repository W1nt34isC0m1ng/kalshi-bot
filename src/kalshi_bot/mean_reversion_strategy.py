"""Mean Reversion Strategy for Kalshi Binary Options.

Fades extreme moves in crypto markets using Black-Scholes fair value,
vol-regime detection, and anti-momentum confirmation.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .coinbase import (
    ASSET_CONFIG,
    asset_prefix_from_ticker,
    compute_d2,
    fetch_5m_momentum,
    fetch_rolling_vol,
    fetch_spot,
    fetch_spot_at_open,
    get_average_vol_5d,
    prob_above_strike,
)
from .models import Market, Signal


def _compute_position_size(edge_cents: int, confidence: float) -> int:
    """Scale contracts with confidence: 1 at 0.5 confidence, up to 5 at full."""
    size = 1 + int((max(0.5, confidence) - 0.5) / 0.25)
    return max(1, min(5, size))


@dataclass
class MeanReversionStrategy:
    """Fades extreme moves using vol-regime detection and anti-momentum signals."""

    min_edge_cents: int = 4
    max_edge_cents: int = 30
    max_spread_cents: int = 10
    min_score: float = 4.0
    vol_regime_high_mult: float = 1.2
    vol_regime_low_mult: float = 0.8

    def _anti_momentum_boost(self, product: str, spot_now: float, side: str) -> float:
        """Boost confidence when 5-minute momentum opposes trade direction.

        Mean reversion thesis: if price just surged but we're fading it (betting
        NO on the up-move), strong prior momentum is a confirming signal.
        Side is derived from raw_edge — the same direction used for the trade.
        """
        try:
            momentum = fetch_5m_momentum(product, spot_now)
            alignment = min(1.0, abs(momentum) / 0.01)

            if side == "yes" and momentum < -0.005:    # fading a down-move
                return alignment * 0.10
            elif side == "no" and momentum > 0.005:    # fading an up-move
                return alignment * 0.10
            return 0.0
        except Exception as exc:
            logging.debug("mean_reversion: anti-momentum calc failed: %s", exc)
            return 0.0

    def _vol_regime_boost(self, product: str, sigma: float) -> float:
        """Boost in high-vol regimes (mean reversion more likely); penalize low-vol."""
        avg_vol = get_average_vol_5d(product)
        if avg_vol is None:
            return 0.0

        vol_ratio = sigma / avg_vol
        if vol_ratio > self.vol_regime_high_mult:
            return min(0.15, (vol_ratio - self.vol_regime_high_mult) * 0.5)
        elif vol_ratio < self.vol_regime_low_mult:
            return -min(0.10, (self.vol_regime_low_mult - vol_ratio) * 0.3)
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

        # ---- strike ----------------------------------------------- #
        if market.kalshi_strike and market.kalshi_strike > 0:
            strike_price = market.kalshi_strike
        else:
            strike_price = fetch_spot_at_open(product, secs_left)
            if strike_price is None or strike_price <= 0:
                logging.warning("mean_reversion: could not fetch strike")
                return None

        # ---- volatility ------------------------------------------- #
        # fetch_rolling_vol also populates the candle cache for fetch_5m_momentum
        sigma = fetch_rolling_vol(product, vol_mult=vol_mult, lookback_minutes=20)
        if sigma is None:
            sigma = 0.80 * vol_mult

        # ---- moneyness -------------------------------------------- #
        d2 = compute_d2(spot_now, strike_price, secs_left, sigma)

        # Require meaningful distance from ATM — no edge in fading noise
        if d2 < 0.5:
            logging.debug("mean_reversion: REJECT %s at-the-money (d2=%.2f)", market.ticker, d2)
            return None

        # ---- fair value & edge ------------------------------------ #
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

        # Derive side from the BS model edge — trust the model, not just spot vs strike.
        # This is consistent with how anti_momentum_boost is applied below.
        side = "yes" if raw_edge > 0 else "no"

        # ---- confidence (smooth sigmoid over d2) ------------------- #
        # Range [0.65, 0.90]: higher floor than crypto_prob because MR only fires
        # at d2 >= 0.5, already implying a somewhat extreme move.
        confidence = 0.65 + 0.25 * math.tanh(d2 - 0.5)

        vol_boost = self._vol_regime_boost(product, sigma)
        confidence = max(0.5, min(1.0, confidence + vol_boost))

        # Anti-momentum boost uses the same `side` as the final trade direction,
        # fixing the prior bug where initial_side (spot vs strike) could disagree
        # with the BS-model direction.
        anti_momentum = self._anti_momentum_boost(product, spot_now, side)
        confidence = min(1.0, confidence + anti_momentum)

        # ---- position sizing & score ------------------------------ #
        position_size = _compute_position_size(int(round(abs(raw_edge))), confidence)

        spread_penalty = spread * 0.15
        adjusted_edge = (abs(raw_edge) * confidence) - spread_penalty

        if adjusted_edge < self.min_score:
            logging.debug("mean_reversion: REJECT %s score too low: %.2f", market.ticker, adjusted_edge)
            return None

        premium_cents = market_price if side == "yes" else (100.0 - market_price)
        ev_cents = abs(raw_edge)
        ev_roi = ev_cents / max(premium_cents, 1e-9)

        logging.info(
            "mean_reversion: KEEP %s side=%s spot=%.2f strike=%.2f market=%.1f fair=%.1f "
            "raw_edge=%.1f d2=%.2f conf=%.2f vol_boost=%.2f anti_mom=%.2f score=%.2f size=%d sigma=%.2f",
            market.ticker, side, spot_now, strike_price, market_price, fair_cents,
            raw_edge, d2, confidence, vol_boost, anti_momentum, adjusted_edge, position_size, sigma,
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
                f"mean_rev: spot={spot_now:.2f}, strike={strike_price:.2f}, "
                f"d2={d2:.2f}, conf={confidence:.2f}, vol={sigma:.2f}, "
                f"fair={fair_cents:.1f}, market={market_price:.1f}, "
                f"ev={ev_cents:.1f}, ev_roi={ev_roi:.4f}, size={position_size}"
            ),
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            position_size=position_size,
            strategy="mean_reversion",
        )
