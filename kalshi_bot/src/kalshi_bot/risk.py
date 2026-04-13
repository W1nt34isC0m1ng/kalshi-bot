from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import Settings
from .models import OrderIntent


class RiskManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._state_path = Path(settings.risk_state_path)
        self.market_notional_cents: dict[str, int] = {}
        self.total_notional_cents: int = 0
        self._load_state()

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
            self.market_notional_cents = data.get("market_notional_cents", {})
            self.total_notional_cents = int(data.get("total_notional_cents", 0))
            logging.info(
                "risk: loaded persisted state — total_notional=%d cents", self.total_notional_cents
            )
        except Exception as exc:
            logging.warning("risk: could not load state file (%s), starting fresh", exc)

    def _save_state(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(
                    {
                        "market_notional_cents": self.market_notional_cents,
                        "total_notional_cents": self.total_notional_cents,
                    },
                    indent=2,
                )
            )
        except Exception as exc:
            logging.warning("risk: could not save state file (%s)", exc)

    # ------------------------------------------------------------------ #
    # Startup reconciliation                                               #
    # ------------------------------------------------------------------ #

    def reconcile_from_positions(self, positions: list[dict]) -> None:
        """Rebuild exposure from live positions fetched at startup.

        Replaces the persisted state with ground truth from the exchange so
        that a restart never causes double-counting or blind spots.
        """
        self.market_notional_cents.clear()
        self.total_notional_cents = 0

        for pos in positions:
            ticker = pos.get("market_ticker", "")
            if not ticker:
                continue
            # Kalshi position counts can be negative (short NO = long YES)
            yes_count = abs(int(pos.get("position", 0) or 0))
            # average_price comes back as a dollar float (e.g. 0.46 = 46¢)
            avg_price_cents = int(round(float(pos.get("average_price", 0) or 0) * 100))
            notional = yes_count * avg_price_cents
            self.market_notional_cents[ticker] = (
                self.market_notional_cents.get(ticker, 0) + notional
            )
            self.total_notional_cents += notional

        self._save_state()
        logging.info(
            "risk: reconciled from %d live positions — total_notional=%d cents",
            len(positions),
            self.total_notional_cents,
        )

    # ------------------------------------------------------------------ #
    # Core risk checks                                                     #
    # ------------------------------------------------------------------ #

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
        self.market_notional_cents[intent.ticker] = (
            self.market_notional_cents.get(intent.ticker, 0) + order_notional
        )
        self.total_notional_cents += order_notional
        self._save_state()
