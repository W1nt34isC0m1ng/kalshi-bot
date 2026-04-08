from __future__ import annotations

from .config import Settings
from .models import Market, Signal


class MeanReversionMaker:
    """Simple first-pass strategy.

    Looks for open markets with enough liquidity where the quoted spread is acceptable,
    and last trade has drifted away from the book midpoint.
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

        spread = max(0, market.yes_ask - market.yes_bid)
        if spread > 12:
            return None

        midpoint = (market.yes_bid + market.yes_ask) / 2
        drift = int(round(midpoint - market.last_price))
        if abs(drift) < self.settings.edge_threshold_cents:
            return None

        # Improved EV calculation: estimate probability of reversion based on drift magnitude
        # Assume larger drift indicates higher probability of reversion (up to 70% at max drift)
        max_drift = 50  # Assume max meaningful drift
        prob_reversion = min(0.7, 0.5 + (abs(drift) / max_drift) * 0.2)
        prob_no_reversion = 1 - prob_reversion

        side = "yes" if drift > 0 else "no"
        price = market.yes_bid + 1 if side == "yes" else market.no_bid + 1

        # EV in cents: prob_win * win_amount - prob_loss * loss_amount
        # Win: if reversion, gain the drift (approx)
        # Loss: lose the price paid
        ev_cents = prob_reversion * abs(drift) - prob_no_reversion * (price / 100) * 100  # Rough approximation

        # Adjust for spread and volume
        spread_penalty = spread * 0.1  # Penalize wide spreads
        volume_bonus = min(5, market.volume_24h / 500)  # Bonus for liquidity

        score = ev_cents + volume_bonus - spread_penalty
        if score < 0:
            return None  # Only signal if positive EV

        reason = f"midpoint={midpoint:.1f}, last={market.last_price}, drift={drift}, prob_rev={prob_reversion:.2f}, EV={ev_cents:.1f}, spread={spread}, vol24h={market.volume_24h:.0f}"
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
