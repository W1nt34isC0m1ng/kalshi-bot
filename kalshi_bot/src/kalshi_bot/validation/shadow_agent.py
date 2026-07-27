from __future__ import annotations

import csv
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest import (  # noqa: E402
    COINBASE_PRODUCTS,
    asset_prefix_from_ticker,
    parse_market_ticker,
    pnl_for_trade,
    resolve_yes_outcome,
)

from ..models import Market, Signal


class StrategyProtocol(Protocol):
    def evaluate(self, market: Market) -> Signal | None: ...


_FIELDNAMES = [
    "ts_utc", "ticker", "side", "price", "expiry_time",
    "product", "status", "yes_outcome", "won", "pnl_cents",
]


@dataclass
class PendingFill:
    ticker: str
    side: str
    price: int
    expiry_time: datetime
    product: str


@dataclass
class ShadowResult:
    win_rate: float
    n_fills: int
    n_wins: int


class ShadowWorker:
    def __init__(
        self,
        strategy: StrategyProtocol,
        market_data,
        shadow_journal_path: str,
        min_fills: int = 100,
        poll_interval_seconds: float = 2.0,
    ):
        self.strategy = strategy
        self.market_data = market_data
        self.shadow_journal_path = Path(shadow_journal_path)
        self.min_fills = min_fills
        self.poll_interval_seconds = poll_interval_seconds
        self._pending: list[PendingFill] = []
        self._resolved_n = 0
        self._resolved_wins = 0
        self._ensure_journal()

    def _ensure_journal(self) -> None:
        self.shadow_journal_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.shadow_journal_path.exists() or self.shadow_journal_path.stat().st_size == 0:
            with self.shadow_journal_path.open("w", newline="") as f:
                csv.DictWriter(f, fieldnames=_FIELDNAMES).writeheader()

    def _append(self, row: dict) -> None:
        with self.shadow_journal_path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=_FIELDNAMES, extrasaction="ignore").writerow(row)

    def _record_pending(self, fill: PendingFill) -> None:
        self._append({
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "ticker": fill.ticker, "side": fill.side, "price": fill.price,
            "expiry_time": fill.expiry_time.isoformat(), "product": fill.product,
            "status": "shadow_pending", "yes_outcome": "", "won": "", "pnl_cents": "",
        })

    def _try_resolve(self) -> None:
        now = datetime.now(timezone.utc)
        still_pending: list[PendingFill] = []
        for fill in self._pending:
            if fill.expiry_time > now:
                still_pending.append(fill)
                continue
            try:
                _, _, yes_outcome = resolve_yes_outcome(
                    ticker=fill.ticker,
                    product=fill.product,
                    expiry_time=fill.expiry_time,
                )
                won, pnl_cents = pnl_for_trade(fill.side, fill.price, yes_outcome)
                self._resolved_n += 1
                if won:
                    self._resolved_wins += 1
                self._append({
                    "ts_utc": datetime.now(timezone.utc).isoformat(),
                    "ticker": fill.ticker, "side": fill.side, "price": fill.price,
                    "expiry_time": fill.expiry_time.isoformat(), "product": fill.product,
                    "status": "shadow_win" if won else "shadow_loss",
                    "yes_outcome": yes_outcome, "won": str(won), "pnl_cents": pnl_cents,
                })
                logging.info(
                    "shadow: resolved %s won=%s n=%d/%d",
                    fill.ticker, won, self._resolved_n, self.min_fills,
                )
            except Exception as exc:
                logging.warning("shadow: could not resolve %s: %s", fill.ticker, exc)
                still_pending.append(fill)
        self._pending = still_pending

    def run(self) -> ShadowResult:
        seen_tickers: set[str] = set()

        while self._resolved_n < self.min_fills:
            try:
                markets = list(self.market_data.iter_open_markets(limit_per_page=200))
            except Exception as exc:
                logging.warning("shadow: market fetch failed: %s", exc)
                time.sleep(self.poll_interval_seconds)
                continue

            for market in markets:
                if market.ticker in seen_tickers:
                    continue
                sig = self.strategy.evaluate(market)
                if sig is None:
                    continue
                prefix = asset_prefix_from_ticker(market.ticker)
                if prefix is None:
                    continue
                try:
                    _, expiry_time, _ = parse_market_ticker(market.ticker)
                except Exception:
                    continue
                product = COINBASE_PRODUCTS.get(prefix)
                if not product:
                    continue
                fill = PendingFill(
                    ticker=market.ticker, side=sig.side, price=sig.price,
                    expiry_time=expiry_time, product=product,
                )
                self._pending.append(fill)
                seen_tickers.add(market.ticker)
                self._record_pending(fill)
                logging.info(
                    "shadow: recorded %s side=%s (pending=%d resolved=%d/%d)",
                    fill.ticker, fill.side, len(self._pending),
                    self._resolved_n, self.min_fills,
                )

            self._try_resolve()
            if self._resolved_n < self.min_fills:
                time.sleep(self.poll_interval_seconds)

        wr = self._resolved_wins / self._resolved_n if self._resolved_n else 0.0
        return ShadowResult(win_rate=wr, n_fills=self._resolved_n, n_wins=self._resolved_wins)
