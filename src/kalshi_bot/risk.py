from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .models import OrderIntent
from .tickers import parse_expiry_utc


class RiskManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._state_path = Path(settings.risk_state_path)
        self.market_position_counts: dict[str, int] = {}
        self.market_notional_cents: dict[str, int] = {}
        self.total_notional_cents: int = 0
        self._load_state()

    @staticmethod
    def _premium_cents(side: str, yes_price_cents: int) -> int:
        """Actual premium paid per contract for a YES/NO buy."""
        return yes_price_cents if side == "yes" else (100 - yes_price_cents)

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
            self.market_position_counts = {
                ticker: int(count)
                for ticker, count in data.get("market_position_counts", {}).items()
            }
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
                        "market_position_counts": self.market_position_counts,
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
        self.market_position_counts.clear()
        self.market_notional_cents.clear()
        self.total_notional_cents = 0

        for pos in positions:
            ticker = pos.get("market_ticker", "")
            if not ticker:
                continue

            signed_position = int(pos.get("position", 0) or 0)
            contract_count = abs(signed_position)
            if contract_count == 0:
                continue

            side = "yes" if signed_position > 0 else "no"
            # average_price is reported as the YES price in dollars.
            avg_yes_price_cents = int(round(float(pos.get("average_price", 0) or 0) * 100))
            premium_cents = self._premium_cents(side, avg_yes_price_cents)
            notional = contract_count * premium_cents

            self.market_position_counts[ticker] = (
                self.market_position_counts.get(ticker, 0) + contract_count
            )
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
    # Expiry-based pruning (DRY_RUN mode)                                  #
    # ------------------------------------------------------------------ #

    def prune_expired_markets(self, now_utc: datetime | None = None) -> int:
        """Drop positions on markets whose expiry has passed.

        In live mode, `reconcile_from_positions` rebuilds risk state from the
        exchange every poll cycle, so stale positions naturally evaporate. In
        DRY_RUN mode there's no exchange to reconcile against, and `mark_sent`
        accumulates fake positions forever. Without this method, the portfolio
        notional cap eventually saturates with ghost positions on expired
        markets and starts blocking otherwise-valid signals.

        Returns the number of tickers pruned. Caller is responsible for
        calling this on a sensible cadence (typically once per poll loop).
        """
        if not self.market_position_counts:
            return 0

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        expired = []
        for ticker in list(self.market_position_counts.keys()):
            expiry = parse_expiry_utc(ticker)
            # Unparseable tickers stay — better than dropping a real position
            # because of a parser miss.
            if expiry is None:
                continue
            if expiry < now_utc:
                expired.append(ticker)

        if not expired:
            return 0

        for ticker in expired:
            self.market_position_counts.pop(ticker, None)
            notional = self.market_notional_cents.pop(ticker, 0)
            self.total_notional_cents -= notional

        # Floor at 0 in case of arithmetic drift from imports of historical state
        self.total_notional_cents = max(0, self.total_notional_cents)
        self._save_state()

        logging.info(
            "risk: pruned %d expired markets — total_notional now %d cents",
            len(expired), self.total_notional_cents,
        )
        return len(expired)

    # ------------------------------------------------------------------ #
    # Core risk checks                                                     #
    # ------------------------------------------------------------------ #

    def approve(self, intent: OrderIntent) -> tuple[bool, str]:
        order_notional = intent.count * self._premium_cents(intent.side, intent.price)
        market_count = self.market_position_counts.get(intent.ticker, 0) + intent.count
        market_total = self.market_notional_cents.get(intent.ticker, 0) + order_notional
        total = self.total_notional_cents + order_notional

        if market_count > self.settings.max_position_per_market:
            return False, "market position cap breached"
        if market_total > self.settings.max_notional_cents_per_market:
            return False, "market notional cap breached"
        if total > self.settings.max_total_notional_cents:
            return False, "portfolio notional cap breached"
        return True, "approved"

    def mark_sent(self, intent: OrderIntent) -> None:
        order_notional = intent.count * self._premium_cents(intent.side, intent.price)
        self.market_position_counts[intent.ticker] = (
            self.market_position_counts.get(intent.ticker, 0) + intent.count
        )
        self.market_notional_cents[intent.ticker] = (
            self.market_notional_cents.get(intent.ticker, 0) + order_notional
        )
        self.total_notional_cents += order_notional
        self._save_state()
