from __future__ import annotations

import time
import uuid

from .client import KalshiHttpClient
from .config import Settings
from .models import OrderIntent, Signal
from .risk import RiskManager


class ExecutionEngine:
    def __init__(self, client: KalshiHttpClient, settings: Settings, risk: RiskManager):
        self.client = client
        self.settings = settings
        self.risk = risk

    def intent_from_signal(self, signal: Signal) -> OrderIntent:
        return OrderIntent(
            ticker=signal.ticker,
            side=signal.side,
            action="buy",
            count=min(2, self.settings.max_position_per_market),
            price=signal.price,
            client_order_id=str(uuid.uuid4()),
            expiration_ts=int(time.time()) + self.settings.order_ttl_seconds,
            reason=signal.reason,
        )

    def maybe_send(self, signal: Signal) -> dict:
        intent = self.intent_from_signal(signal)
        approved, reason = self.risk.approve(intent)
        if not approved:
            return {"status": "blocked", "reason": reason, "intent": intent}

        if self.settings.dry_run:
            self.risk.mark_sent(intent)
            return {"status": "dry_run", "intent": intent}

        response = self.client.create_order(
            ticker=intent.ticker,
            side=intent.side,
            action=intent.action,
            count=intent.count,
            price=intent.price,
            expiration_ts=intent.expiration_ts,
            post_only=True,
        )
        self.risk.mark_sent(intent)
        return {"status": "sent", "intent": intent, "response": response}
