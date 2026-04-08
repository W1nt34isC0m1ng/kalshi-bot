from __future__ import annotations

from .config import Settings
from .models import OrderIntent


class RiskManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.market_notional_cents: dict[str, int] = {}
        self.total_notional_cents: int = 0

    def approve(self, intent: OrderIntent) -> tuple[bool, str]:
        order_notional = intent.count * intent.price
        market_total = self.market_notional_cents.get(intent.ticker, 0) + order_notional
        total = self.total_notional_cents + order_notional

        if intent.count > self.settings.max_position_per_market:
            return False, "count exceeds max_position_per_market"
        if market_total > self.settings.max_notional_cents_per_market:
            return False, "market notional cap breached"
        if total > self.settings.max_total_notional_cents:
            return False, "portfolio notional cap breached"
        return True, "approved"

    def mark_sent(self, intent: OrderIntent) -> None:
        order_notional = intent.count * intent.price
        self.market_notional_cents[intent.ticker] = self.market_notional_cents.get(intent.ticker, 0) + order_notional
        self.total_notional_cents += order_notional
