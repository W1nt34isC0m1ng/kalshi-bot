"""HourBiasStrategy — rules-based strategy derived from journal calibration.

Empirical finding from 1,450-trade calibration analysis (CHANGELOG 2026-05-20):
the Kalshi 15-min crypto market is well-calibrated on aggregate, BUT specific
feature combinations show large gaps between market-implied probability and
actual outcome rate. The biggest measured signals:

  Hour-of-day buckets (UTC):
    NO bias (market overprices YES by 10-23pp): 0, 11, 13, 14, 18, 22
    YES bias (market underprices YES by 6-10pp): 3, 12, 17, 19

  Momentum regime:
    Bullish (drift>0 AND trend>+0.5): actual P(YES) = 31% vs market 48%
    → mean-reversion edge, NO bet wins ~69% of the time
    Bearish (drift<0 AND trend<-0.5): smaller signal, NO bet still ~58% WR

  ATM filter: only fires when yes_price ∈ [40, 60] — outside this range the
  market is too well-calibrated to extract edge.

This strategy ABANDONS the BS-mispricing thesis entirely. It does not compute
fair value, edge, or model probability. Trade decisions are pure rules over
(hour_of_day, drift, trend, market_price).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .client import KalshiHttpClient
from .coinbase import (
    ASSET_CONFIG,
    asset_prefix_from_ticker,
    fetch_recent_log_drift,
    fetch_rolling_vol,
    fetch_spot,
    fetch_trend_strength,
)
from .models import Market, Signal


@dataclass
class HourBiasStrategy:
    """Trade on measured hour-of-day biases + bullish mean-reversion."""

    client: KalshiHttpClient | None = None

    # Hours where market overprices YES (bet NO). UTC.
    # Source: calibration analysis 2026-05-20 over 1450 settled trades.
    no_hours: frozenset[int] = field(default_factory=lambda: frozenset({0, 11, 13, 14, 18, 22}))
    # Hours where market underprices YES (bet YES). UTC.
    yes_hours: frozenset[int] = field(default_factory=lambda: frozenset({3, 12, 17, 19}))

    # ATM filter: only fire when market_yes_price is in [atm_min, atm_max].
    # Outside this window the market is well-calibrated (gap < 5pp) and
    # there's no exploitable edge.
    atm_min_yes_price: int = 40
    atm_max_yes_price: int = 60

    # Bullish-mean-revert thresholds. drift is per-minute log return; trend
    # is the signed strength from fetch_trend_strength.
    drift_threshold: float = 0.00002
    trend_threshold: float = 0.5

    # Standard sanity gates
    max_spread_cents: int = 7
    min_secs_left: int = 30

    def _decide(
        self,
        hour_utc: int,
        market_price: float,
        drift: float,
        trend: float,
    ) -> tuple[str | None, str]:
        """Apply the rule cascade. Returns (side_or_None, rule_name)."""
        # Bullish/bearish momentum overrides (strongest measured signals)
        if drift > self.drift_threshold and trend > self.trend_threshold:
            return "no", "bullish-mean-revert"
        if drift < -self.drift_threshold and trend < -self.trend_threshold:
            return "no", "bearish"

        # Hour-of-day rules require ATM market price
        if not (self.atm_min_yes_price <= market_price <= self.atm_max_yes_price):
            return None, "non-atm"

        if hour_utc in self.no_hours:
            return "no", f"hour-{hour_utc}-NO"
        if hour_utc in self.yes_hours:
            return "yes", f"hour-{hour_utc}-YES"

        return None, "no-edge-hour"

    def evaluate(self, market: Market) -> Signal | None:
        prefix = asset_prefix_from_ticker(market.ticker)
        if prefix is None:
            return None

        cfg = ASSET_CONFIG[prefix]
        product = cfg["product"]
        vol_mult = cfg["vol_mult"]

        # ---- sanity gates ----------------------------------------- #
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
            logging.debug("hour_bias: REJECT %s spread too wide: %.1f", market.ticker, spread)
            return None

        secs_left = market.secs_left
        if secs_left is None or secs_left < self.min_secs_left:
            logging.debug("hour_bias: REJECT %s secs_left too low", market.ticker)
            return None

        # ---- fetch the features we need --------------------------- #
        # Even though we don't use sigma directly, we need rolling vol to warm
        # the candle cache so drift and trend fetches don't re-call Coinbase.
        sigma = fetch_rolling_vol(product, vol_mult=vol_mult, lookback_minutes=20)
        if sigma is None:
            logging.debug("hour_bias: REJECT %s sigma unavailable", market.ticker)
            return None

        try:
            spot_now = fetch_spot(product)
        except Exception as exc:
            logging.warning("hour_bias: spot fetch failed for %s: %s", product, exc)
            return None

        drift = fetch_recent_log_drift(product, lookback_minutes=20)
        trend = fetch_trend_strength(product, spot_now, sigma, lookback_minutes=20)

        # ---- apply the rule cascade ------------------------------- #
        hour_utc = datetime.now(timezone.utc).hour
        side, rule = self._decide(hour_utc, market_price, drift, trend)

        if side is None:
            logging.debug(
                "hour_bias: REJECT %s no matching rule (hour=%d, market=%.1f, drift=%.5f, trend=%.2f) [%s]",
                market.ticker, hour_utc, market_price, drift, trend, rule,
            )
            return None

        # ---- score / ev placeholders ----------------------------- #
        # Rule-based; no model edge to compute. Set conservative defaults so
        # the executor and risk layer can still rank and log.
        premium_cents = market_price if side == "yes" else (100.0 - market_price)
        # Historical EV per contract for this strategy was ~+17c (bullish-MR);
        # use that as a reasonable proxy for ev_cents so logs are interpretable.
        ev_cents = 17.0
        ev_roi = ev_cents / max(premium_cents, 1e-9)
        score = 10.0  # high enough to pass any min_score gate; rule-based

        logging.info(
            "hour_bias: KEEP %s side=%s rule=%s hour=%d market=%.1f drift=%.5f trend=%.2f secs_left=%.0f",
            market.ticker, side, rule, hour_utc, market_price, drift, trend, secs_left,
        )

        return Signal(
            ticker=market.ticker,
            title=market.title,
            side=side,
            price=max(1, min(99, int(round(market_price)))),
            edge_cents=int(round(ev_cents)),
            ev_cents=float(round(ev_cents, 2)),
            ev_roi=float(round(ev_roi, 4)),
            spread_cents=int(round(spread)),
            score=score,
            reason=(
                f"asset={prefix}, rule={rule}, hour={hour_utc}, "
                f"market={market_price:.1f}, drift={drift:.5f}, trend={trend:.2f}, "
                f"spot={spot_now:.2f}, secs_left={secs_left:.0f}, sigma={sigma:.2f}"
            ),
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            strategy="hour_bias",
        )
