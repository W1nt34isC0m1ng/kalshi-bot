#!/usr/bin/env python3
"""Entry point for the Kalshi BTC 15-minute binary trading bot.

Usage
-----
    python main.py              # live trading, runs every 15 minutes
    python main.py --dry-run    # simulate signals without placing orders
    python main.py --once       # run a single cycle and exit

Environment Variables (see .env.example)
-----------------------------------------
    KALSHI_EMAIL        Kalshi account email (required)
    KALSHI_PASSWORD     Kalshi account password (required)
    NUM_CONTRACTS       Contracts per trade (default: 1)
    BTC_SERIES          Kalshi series ticker (default: KXBTCD)
"""

from __future__ import annotations

import argparse
import logging
import os
import time

import schedule
from dotenv import load_dotenv

from src.kalshi_client import KalshiClient, KalshiError
from src.trader import Trader, DEFAULT_BTC_SERIES

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kalshi BTC 15-minute binary market auto-trader"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute signals but do not place real orders",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single trading cycle and exit",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # ------------------------------------------------------------------ Config
    email = os.environ.get("KALSHI_EMAIL")
    password = os.environ.get("KALSHI_PASSWORD")

    if not email or not password:
        raise SystemExit(
            "KALSHI_EMAIL and KALSHI_PASSWORD must be set "
            "(see .env.example for reference)"
        )

    num_contracts = int(os.environ.get("NUM_CONTRACTS", "1"))
    series_ticker = os.environ.get("BTC_SERIES", DEFAULT_BTC_SERIES)

    if args.dry_run:
        logger.info("*** DRY-RUN mode: no real orders will be placed ***")

    # ----------------------------------------------------------- Authenticate
    client = KalshiClient(email=email, password=password)
    try:
        client.login()
    except KalshiError as exc:
        raise SystemExit(f"Kalshi login failed: {exc}") from exc

    # -------------------------------------------------------------- Trader
    trader = Trader(
        client=client,
        num_contracts=num_contracts,
        series_ticker=series_ticker,
        dry_run=args.dry_run,
    )

    # --------------------------------------------------------------- Run
    if args.once:
        trader.run_once()
        return

    # Run immediately, then every 15 minutes on the quarter-hour
    logger.info("Starting bot – trading every 15 minutes (series=%s)", series_ticker)
    trader.run_once()
    schedule.every(15).minutes.do(trader.run_once)

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
