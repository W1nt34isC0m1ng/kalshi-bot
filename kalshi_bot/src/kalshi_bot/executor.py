from __future__ import annotations

import logging
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

        # cooldown tracking: {(ticker, side): last_sent_ts}
        self._last_sent: dict[tuple[str, str], float] = {}

    def intent_from_signal(self, signal: Signal) -> OrderIntent:
        return OrderIntent(
            ticker=signal.ticker,
            side=signal.side,
            action="buy",
            count=min(self.settings.order_count, self.settings.max_position_per_market),
            price=signal.price,
            client_order_id=str(uuid.uuid4()),
            expiration_ts=int(time.time()) + self.settings.order_ttl_seconds,
            reason=signal.reason,
        )

    def _cooldown_key(self, signal: Signal) -> tuple[str, str]:
        return (signal.ticker, signal.side)

    def _cooldown_active(self, signal: Signal) -> tuple[bool, int]:
        key = self._cooldown_key(signal)
        now = time.time()
        last_ts = self._last_sent.get(key)

        if last_ts is None:
            return False, 0

        elapsed = now - last_ts
        remaining = int(max(0, self.settings.cooldown_seconds - elapsed))
        return elapsed < self.settings.cooldown_seconds, remaining

    def _mark_sent(self, signal: Signal) -> None:
        self._last_sent[self._cooldown_key(signal)] = time.time()

    def maybe_send(self, signal: Signal) -> dict:
        cooldown_active, remaining = self._cooldown_active(signal)
        if cooldown_active:
            return {
                "status": "cooldown",
                "reason": f"cooldown_active_{remaining}s",
                "signal": signal,
            }

        intent = self.intent_from_signal(signal)
        approved, reason = self.risk.approve(intent)
        if not approved:
            return {"status": "blocked", "reason": reason, "intent": intent}

        if self.settings.dry_run:
            self.risk.mark_sent(intent)
            self._mark_sent(signal)
            return {"status": "dry_run", "intent": intent}

        try:
            response = self.client.create_order(
                ticker=intent.ticker,
                side=intent.side,
                action=intent.action,
                count=intent.count,
                price=intent.price,
                expiration_ts=intent.expiration_ts,
                post_only=True,
            )
        except Exception as exc:
            # Order may or may not have reached the exchange (e.g. timeout).
            # Do NOT update risk or cooldown — startup reconciliation via
            # get_positions() will catch any orphaned orders on next restart.
            logging.error("executor: order failed for %s: %s", intent.ticker, exc)
            return {"status": "error", "reason": str(exc), "intent": intent}

        # Only mark risk and cooldown after confirmed exchange acknowledgement.
        self.risk.mark_sent(intent)
        self._mark_sent(signal)
        return {"status": "sent", "intent": intent, "response": response}
