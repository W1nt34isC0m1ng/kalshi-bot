from __future__ import annotations

import datetime
from .config import Settings
from .models import Market, Signal


class MomentumMaker:
    """Momentum-based strategy for short-term markets.

    Bets on continuation of recent price movement in markets closing soon.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def evaluate(self, market: Market) -> Signal | None:
        if self.settings.category_filter and market.category not in self.settings.category_filter:
            return None
        if market.volume_24h < self.settings.min_daily_volume:
            return None
        if market.yes_bid <= 0 or market.yes_ask <= 0:
            return None

        # Check time to close: focus on markets closing in 10-20 minutes
        if not market.close_time:
            return None
        try:
            close_dt = datetime.datetime.fromisoformat(market.close_time.replace('Z', '+00:00'))
            now = datetime.datetime.now(datetime.timezone.utc)
            time_to_close = (close_dt - now).total_seconds() / 60  # minutes
            if not (10 <= time_to_close <= 20):
                return None
        except ValueError:
            return None

        spread = max(0, market.yes_ask - market.yes_bid)
        if spread > 12:
            return None

        midpoint = (market.yes_bid + market.yes_ask) / 2
        drift = int(round(midpoint - market.last_price))
        if abs(drift) < self.settings.edge_threshold_cents:
            return None

        # Momentum: bet on continuation, opposite of reversion
        side = "no" if drift > 0 else "yes"  # If last < mid (drift >0), bet no (continue down)
        price = market.yes_bid + 1 if side == "yes" else market.no_bid + 1

        # Estimate probability of continuation (lower than reversion)
        max_drift = 50
        prob_continue = min(0.6, 0.5 + (abs(drift) / max_drift) * 0.1)  # Up to 60%
        prob_reverse = 1 - prob_continue

        # EV: prob_win * gain - prob_loss * cost
        ev_cents = prob_continue * abs(drift) - prob_reverse * (price / 100) * 100

        spread_penalty = spread * 0.1
        volume_bonus = min(5, market.volume_24h / 500)

        score = ev_cents + volume_bonus - spread_penalty
        if score < 0:
            return None

        reason = f"midpoint={midpoint:.1f}, last={market.last_price}, drift={drift}, prob_cont={prob_continue:.2f}, EV={ev_cents:.1f}, spread={spread}, vol24h={market.volume_24h:.0f}, ttc={time_to_close:.1f}min"
        return Signal(
            ticker=market.ticker,
            title=market.title,
            side=side,
            price=int(price),
            edge_cents=int(ev_cents),
            spread_cents=spread,
            score=score,
            reason=reason,
        )
