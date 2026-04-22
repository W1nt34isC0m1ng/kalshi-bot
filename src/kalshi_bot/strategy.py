from __future__ import annotations

from .config import Settings
from .models import Market, Signal


class MeanReversionMaker:
    def __init__(self, settings: Settings):
        self.settings = settings

    def evaluate(self, market: Market) -> Signal | None:
        yes_bid = market.yes_bid
        yes_ask = market.yes_ask
        last_price = market.last_price
        volume_24h = market.volume_24h

        if yes_bid is None or yes_ask is None or last_price is None:
            return None

        if yes_bid <= 0 or yes_ask >= 100:
            return None

        if yes_bid >= yes_ask:
            return None

        spread = yes_ask - yes_bid
        midpoint = (yes_bid + yes_ask) / 2.0
        drift = midpoint - last_price

        # For short-term crypto, ignore tiny noise
        if abs(drift) < 2:
            return None

        side = "yes" if drift > 0 else "no"
        price = yes_bid + 1 if side == "yes" else market.no_bid + 1
        price = max(1, min(99, int(round(price))))

        drift_score = abs(drift)
        spread_penalty = spread * 0.4
        volume_bonus = min(8.0, volume_24h / 500.0)

        score = drift_score + volume_bonus - spread_penalty
        if score <= 0:
            return None

        return Signal(
            ticker=market.ticker,
            title=market.title,
            side=side,
            price=price,
            edge_cents=int(round(abs(drift))),
            spread_cents=int(round(spread)),
            score=score,
            reason=(
                f"crypto-short-term midpoint={midpoint:.1f}, last={last_price:.1f}, "
                f"drift={drift:.1f}, spread={spread:.1f}, vol24h={volume_24h:.0f}"
            ),
        )