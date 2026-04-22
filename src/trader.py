"""Orchestrates one complete 15-minute trading cycle.

Workflow
--------
1. Fetch recent 1-minute BTC/USDT candles from Binance.
2. Compute the trading signal (YES / NO).
3. Find the nearest open Kalshi BTC 15-minute binary market.
4. Optionally cancel any stale open orders for that market.
5. Place a market order in the direction of the signal.

Set ``dry_run=True`` to log everything without actually placing orders.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .btc_price import get_btc_klines
from .kalshi_client import KalshiClient
from .strategy import get_signal

logger = logging.getLogger(__name__)

# Default series ticker for the Kalshi BTC 15-minute up/down market.
# Override via the BTC_SERIES environment variable or the constructor.
DEFAULT_BTC_SERIES = "KXBTCD"

# How many 1-minute candles to use when computing the signal.
_KLINE_LOOKBACK = 20


class Trader:
    """High-level trading orchestrator."""

    def __init__(
        self,
        client: KalshiClient,
        num_contracts: int = 1,
        series_ticker: str = DEFAULT_BTC_SERIES,
        dry_run: bool = False,
    ) -> None:
        """
        Args:
            client:         Authenticated :class:`~src.kalshi_client.KalshiClient`.
            num_contracts:  Contracts to buy per cycle (default 1).
            series_ticker:  Kalshi market series to trade (default ``"KXBTCD"``).
            dry_run:        When ``True``, compute signals but skip order placement.
        """
        self.client = client
        self.num_contracts = num_contracts
        self.series_ticker = series_ticker
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Market helpers
    # ------------------------------------------------------------------

    def find_active_market(self) -> Optional[dict[str, Any]]:
        """Return the soonest-expiring open market for the configured series.

        Returns ``None`` if no open market is found.
        """
        markets = self.client.get_markets(
            series_ticker=self.series_ticker, status="open"
        )
        if not markets:
            logger.warning(
                "No open markets found for series %s", self.series_ticker
            )
            return None
        # Pick the market that closes first.
        market = min(markets, key=lambda m: m.get("close_time", ""))
        logger.info(
            "Active market: %s (closes %s)",
            market.get("ticker"),
            market.get("close_time"),
        )
        return market

    # ------------------------------------------------------------------
    # Core trading cycle
    # ------------------------------------------------------------------

    def run_once(self) -> dict[str, Any]:
        """Execute a single trading cycle.

        Returns a summary dict with keys:
        ``signal``, ``market``, ``order`` (or ``None`` if skipped).
        """
        result: dict[str, Any] = {"signal": None, "market": None, "order": None}

        # 1. Fetch price data and compute signal
        try:
            klines = get_btc_klines(interval="1m", limit=_KLINE_LOOKBACK)
        except Exception as exc:
            logger.error("Failed to fetch BTC klines: %s", exc)
            return result

        signal = get_signal(klines)
        result["signal"] = signal
        logger.info("Trading signal: %s", signal)

        # 2. Find market to trade
        try:
            market = self.find_active_market()
        except Exception as exc:
            logger.error("Failed to fetch markets: %s", exc)
            return result

        if market is None:
            return result

        ticker = market["ticker"]
        result["market"] = ticker

        # 3. Check available balance
        try:
            balance_cents = self.client.get_balance()
            logger.info("Available balance: $%.2f", balance_cents / 100)
            if balance_cents <= 0:
                logger.warning("Insufficient balance; skipping order")
                return result
        except Exception as exc:
            logger.warning("Could not fetch balance (%s); proceeding anyway", exc)

        # 4. Place order (or log if dry_run)
        if self.dry_run:
            logger.info(
                "[DRY RUN] Would place %s order: %d × %s on %s",
                signal,
                self.num_contracts,
                signal.upper(),
                ticker,
            )
            return result

        try:
            order = self.client.place_order(
                ticker=ticker,
                side=signal,
                count=self.num_contracts,
                order_type="market",
            )
            result["order"] = order
            logger.info("Order placed successfully: %s", order)
        except Exception as exc:
            logger.error("Failed to place order on %s: %s", ticker, exc)

        return result
