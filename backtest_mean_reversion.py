"""
Historical Backtester for Mean Reversion Strategy

Simulates 1-month of historical trading to validate strategy performance.
Completely standalone - uses public Coinbase API, generates isolated results.
"""

from __future__ import annotations

import re
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

import requests

from src.kalshi_bot.mean_reversion_strategy import MeanReversionStrategy, _asset_prefix_from_ticker
from src.kalshi_bot.models import Market


# ============================= CONSTANTS ==============================

COINBASE_PRODUCTS = {
    "KXBTC15M": "BTC-USD",
    "KXETH15M": "ETH-USD",
}

_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

OUTPUT_PATH = "logs/mean_reversion_backtest_results.csv"

# Regex for Kalshi ticker: KXBTC15M-26APR111700-00
_TICKER_RE = re.compile(
    r"^([A-Z][A-Z0-9]+)-(\d{2}[A-Z]{3}\d{6})-(\d+)$",
    re.IGNORECASE,
)


# ============================= TICKER PARSING ==============================

def parse_market_ticker(ticker: str) -> tuple[str, datetime, str]:
    """Parse Kalshi ticker like KXBTC15M-26APR111700-00."""
    m = _TICKER_RE.match((ticker or "").strip().upper())
    if not m:
        raise ValueError(f"Unrecognized ticker format: {ticker!r}")

    prefix = m.group(1)
    dt_str = m.group(2)
    suffix = m.group(3)

    yy = int(dt_str[0:2])
    mon_str = dt_str[2:5]
    day = int(dt_str[5:7])
    hour = int(dt_str[7:9])
    minute = int(dt_str[9:11])

    month = _MONTH_MAP.get(mon_str)
    if month is None:
        raise ValueError(f"Unrecognized month: {mon_str!r}")

    expiry = datetime(2000 + yy, month, day, hour, minute, 0, tzinfo=timezone.utc)
    return prefix, expiry, suffix


def get_precision_for_product(product: str) -> int:
    return {"BTC-USD": 2, "ETH-USD": 2}.get(product, 2)


def round_target(value: float, product: str) -> float:
    return round(value, get_precision_for_product(product))


# ============================= COINBASE API ==============================

def fetch_coinbase_candles(product: str, start: datetime, end: datetime, granularity: int = 60) -> list:
    """Fetch 1-minute candles from Coinbase."""
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    params = {
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "granularity": granularity,
    }

    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise ValueError(f"Unexpected response: {data}")
        return sorted(data, key=lambda x: x[0])
    except Exception as e:
        print(f"ERROR fetching candles: {e}")
        raise


def fetch_spot_near_time(product: str, target_time: datetime, window_minutes: int = 20) -> float:
    """Get spot price nearest to target time."""
    start = target_time - timedelta(minutes=window_minutes)
    end = target_time + timedelta(minutes=5)

    candles = fetch_coinbase_candles(product, start, end, granularity=60)
    if not candles:
        raise ValueError(f"No candles for {product}")

    target_ts = target_time.timestamp()
    best = min(candles, key=lambda c: abs(c[0] - target_ts))
    return float(best[4])


def resolve_yes_outcome(ticker: str, product: str, expiry_time: datetime) -> tuple[float, float, int]:
    """Determine YES/NO outcome."""
    window_open = expiry_time - timedelta(minutes=15)
    target_spot = fetch_spot_near_time(product, window_open)
    strike = round_target(target_spot, product)

    expiry_spot = fetch_spot_near_time(product, expiry_time)
    yes_outcome = 1 if expiry_spot >= strike else 0

    return strike, expiry_spot, yes_outcome


def pnl_for_trade(side: str, price: int, yes_outcome: int) -> tuple[bool, float]:
    """Calculate P&L."""
    side = side.lower()
    won = (yes_outcome == 1 and side == "yes") or (yes_outcome == 0 and side == "no")
    cost = price if side == "yes" else (100 - price)
    payout = 100 if won else 0
    pnl_cents = payout - cost
    return won, pnl_cents


# ============================= BACKTESTER ==============================

class MeanReversionBacktester:
    def __init__(self, strategy: MeanReversionStrategy, lookback_days: int = 30):
        self.strategy = strategy
        self.lookback_days = lookback_days
        self.now = datetime.now(timezone.utc)
        self.start_date = self.now - timedelta(days=lookback_days)

    def simulate_historical_trades(self) -> list[dict]:
        """Generate simulated markets for past month and run strategy."""
        results = []
        current_time = self.start_date.replace(hour=0, minute=0, second=0, microsecond=0)

        while current_time < self.now:
            for asset, product in [("KXBTC15M", "BTC-USD"), ("KXETH15M", "ETH-USD")]:
                ticker = f"{asset}-{current_time.strftime('%y%b%d%H%M')}-00"
                ticker = ticker.upper()

                try:
                    result = self._simulate_market_outcome(ticker, asset, product, current_time)
                    if result:
                        results.append(result)
                except Exception as e:
                    pass

            current_time += timedelta(minutes=15)

        return results

    def _simulate_market_outcome(self, ticker: str, asset: str, product: str, expiry_time: datetime) -> dict | None:
        """Simulate one market."""
        if expiry_time > self.now:
            return None

        market_time = expiry_time - timedelta(seconds=15)

        try:
            candles = fetch_coinbase_candles(product, market_time - timedelta(minutes=2), market_time)
            if not candles or len(candles) < 2:
                return None

            last_close = float(candles[-1][4])
            yes_bid = int(last_close * 100 - 50)
            yes_ask = yes_bid + 100
            yes_bid = max(1, min(99, yes_bid))
            yes_ask = max(1, min(99, yes_ask))

            strike_price = fetch_spot_near_time(product, expiry_time - timedelta(minutes=15))
            kalshi_strike = round_target(strike_price, product)

            market = Market(
                ticker=ticker,
                title=f"{asset}",
                category="Crypto",
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                no_bid=100 - yes_ask,
                no_ask=100 - yes_bid,
                last_price=int(last_close * 100),
                volume_24h=1000.0,
                liquidity_cents=50000,
                open_interest=1.0,
                kalshi_strike=kalshi_strike,
                secs_left=15.0,
            )

            signal = self.strategy.evaluate(market)
            if signal is None:
                return {"ticker": ticker, "expiry_time": expiry_time.isoformat(), "status": "rejected"}

            strike, spot_at_expiry, yes_outcome = resolve_yes_outcome(ticker, product, expiry_time)
            won, pnl_cents = pnl_for_trade(signal.side, signal.price, yes_outcome)

            return {
                "ticker": ticker,
                "expiry_time": expiry_time.isoformat(),
                "status": "resolved",
                "side": signal.side,
                "price": signal.price,
                "score": signal.score,
               "won": won,
                "pnl_cents": pnl_cents,
                "product": product,
            }
        except Exception as e:
            return {"ticker": ticker, "expiry_time": expiry_time.isoformat(), "status": "error", "error": str(e)}

    def print_summary(self, results: list[dict]) -> None:
        """Print summary."""
        resolved = [r for r in results if r.get("status") == "resolved"]

        print("\n" + "=" * 60)
        print("MEAN REVERSION BACKTEST SUMMARY")
        print("=" * 60)
        print(f"Total Markets: {len(results)}, Resolved: {len(resolved)}")

        if not resolved:
            print("No trades resolved.")
            return

        wins = sum(1 for r in resolved if r.get("won"))
        total_pnl = sum(r.get("pnl_cents", 0) for r in resolved)
        avg_pnl = total_pnl / len(resolved)
        win_rate = wins / len(resolved)

        print(f"\nWins: {wins}, Losses: {len(resolved) - wins}")
        print(f"Win Rate: {win_rate:.1%}")
        print(f"Total P&L: ${total_pnl/100:.2f}")
        print(f"Avg P&L/trade: {avg_pnl:.2f} cents")
        print("=" * 60)

    def save_results(self, results: list[dict], path: str = OUTPUT_PATH) -> None:
        """Save results."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ticker", "expiry_time", "status", "side", "price", "score", "won", "pnl_cents", "product"])
            w.writeheader()
            for r in results:
                w.writerow(r)


def main():
    print("Mean Reversion Backtest Starting...")
    strategy = MeanReversionStrategy()
    backtester = MeanReversionBacktester(strategy, lookback_days=14)

    print("Fetching historical data...")
    results = backtester.simulate_historical_trades()
    backtester.print_summary(results)
    backtester.save_results(results)


if __name__ == "__main__":
    main()
