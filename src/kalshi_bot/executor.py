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

    def _maker_yes_price(self, signal: Signal) -> int:
        """Convert a signal into a maker-safe YES-equivalent price.

        The strategy scores midpoint prices, but post-only orders must rest on
        the bid side of the chosen contract. For BUY YES that is `yes_bid`; for
        BUY NO that is the NO bid, equivalent to `yes_ask` in YES-price terms.
        """
        if signal.side == "yes":
            if signal.yes_bid is not None and signal.yes_bid > 0:
                return signal.yes_bid
            if signal.spread_cents > 0:
                return max(1, signal.price - 1)
            return signal.price

        if signal.yes_ask is not None and signal.yes_ask > 0:
            return signal.yes_ask
        if signal.spread_cents > 0:
            return min(99, signal.price + 1)
        return signal.price

    @staticmethod
    def _premium_cents(side: str, yes_price_cents: int) -> int:
        return yes_price_cents if side == "yes" else (100 - yes_price_cents)

    def _order_count(self, signal: Signal, maker_yes_price: int) -> int:
        if not self.settings.auto_sizing:
            # Honour strategy-computed sizing (e.g. MeanReversionStrategy scales
            # by confidence), falling back to the fixed ORDER_COUNT setting.
            base = signal.position_size if signal.position_size > 1 else self.settings.order_count
            return min(base, self.settings.max_position_per_market)

        premium_cents = max(1, self._premium_cents(signal.side, maker_yes_price))
        risk_budget_cents = int(self.settings.bankroll_cents * self.settings.risk_fraction_per_trade)
        sized_count = risk_budget_cents // premium_cents
        sized_count = max(self.settings.min_order_count, sized_count)
        sized_count = min(sized_count, self.settings.max_order_count)
        return min(sized_count, self.settings.max_position_per_market)

    def intent_from_signal(self, signal: Signal) -> OrderIntent:
        maker_yes_price = self._maker_yes_price(signal)
        count = self._order_count(signal, maker_yes_price)
        return OrderIntent(
            ticker=signal.ticker,
            side=signal.side,
            action="buy",
            count=count,
            price=maker_yes_price,
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
