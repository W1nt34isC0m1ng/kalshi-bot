from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests


JOURNAL_PATH = "logs/trade_journal.csv"
OUTPUT_PATH = "logs/trade_backtest_results.csv"

COINBASE_PRODUCTS = {
    "KXBTC15M": "BTC-USD",
    "KXETH15M": "ETH-USD",
    "KXSOL15M": "SOL-USD",
    "KXDOGE15M": "DOGE-USD",
    "KXXRP15M": "XRP-USD",
}

# Compiled regex for the Kalshi ticker format: KXBTC15M-26APR111700-00
# Groups: (series_prefix, YYMMMDDHHMM, suffix)
# The date+time chunk is 11 chars: YY (2) + MMM (3) + DD (2) + HH (2) + MM (2)
_TICKER_RE = re.compile(
    r"^([A-Z][A-Z0-9]+)-(\d{2}[A-Z]{3}\d{6})-(\d+)$",
    re.IGNORECASE,
)


def asset_prefix_from_ticker(ticker: str) -> str | None:
    t = (ticker or "").upper()
    for prefix in COINBASE_PRODUCTS:
        if t.startswith(prefix):
            return prefix
    return None


_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_MARKET_TICKER_TZ = ZoneInfo("America/New_York")


def parse_market_ticker(ticker: str) -> tuple[str, datetime, str]:
    """Parse a Kalshi ticker like KXBTC15M-26APR111700-00.

    Returns (series_prefix, expiry_utc, suffix_str).

    The 11-char date+time chunk is YYMMMDDHHMM:
      YY  = 2-digit year   (e.g. "26" → 2026)
      MMM = month abbrev   (e.g. "APR")
      DD  = day            (e.g. "11")
      HH  = hour in America/New_York
      MM  = minute in America/New_York

    The suffix is the last two digits of the expiry minute (00, 15, 30, 45)
    and is purely a naming convention — it does NOT indicate market direction.
    """
    m = _TICKER_RE.match((ticker or "").strip().upper())
    if not m:
        raise ValueError(f"Unrecognized ticker format: {ticker!r}")

    prefix = m.group(1)
    dt_str = m.group(2)   # YYMMMDDHHMM (11 chars)
    suffix = m.group(3)

    yy = int(dt_str[0:2])
    mon_str = dt_str[2:5]
    day = int(dt_str[5:7])
    hour = int(dt_str[7:9])
    minute = int(dt_str[9:11])

    month = _MONTH_MAP.get(mon_str)
    if month is None:
        raise ValueError(f"Unrecognized month abbreviation {mon_str!r} in {ticker!r}")

    expiry_local = datetime(2000 + yy, month, day, hour, minute, 0, tzinfo=_MARKET_TICKER_TZ)
    expiry_utc = expiry_local.astimezone(timezone.utc)
    return prefix, expiry_utc, suffix


def get_precision_for_product(product: str) -> int:
    return {
        "BTC-USD": 2,
        "ETH-USD": 2,
        "SOL-USD": 4,
        "DOGE-USD": 7,
        "XRP-USD": 5,
    }.get(product, 2)


def round_target(value: float, product: str) -> float:
    return round(value, get_precision_for_product(product))


def fetch_coinbase_candles(product: str, start: datetime, end: datetime, granularity: int = 60):
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    params = {
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "granularity": granularity,
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected candle response: {data}")
    return sorted(data, key=lambda x: x[0])


def fetch_spot_near_time(product: str, target_time: datetime, window_minutes: int = 20) -> float:
    """Return the close price of the 1-minute candle nearest to target_time."""
    start = target_time - timedelta(minutes=window_minutes)
    end = target_time + timedelta(minutes=5)

    candles = fetch_coinbase_candles(product, start, end, granularity=60)
    if not candles:
        raise ValueError(f"No candles found for {product} near {target_time.isoformat()}")

    target_ts = target_time.timestamp()
    best = min(candles, key=lambda c: abs(c[0] - target_ts))
    return float(best[4])  # close price


def resolve_yes_outcome(ticker: str, product: str, expiry_time: datetime):
    """Determine the YES/NO outcome for a resolved KXBTC15M / KXETH15M market.

    Market structure:
      - The 15-minute window opens at (expiry_time - 15 minutes).
      - The *strike* is the Coinbase spot at window open.
      - The *settlement* price is the Coinbase spot at expiry_time.
      - YES wins if settlement >= strike  (price was flat or higher by expiry).
      - NO  wins if settlement <  strike  (price fell).

    The ticker suffix (00, 15, 30, 45) is only the minute component of the
    expiry time — it encodes WHEN the market expires, not which direction it
    pays.  All KXBTC15M markets use the same YES=up / NO=down convention.
    """
    window_open = expiry_time - timedelta(minutes=15)

    # Strike: Coinbase spot at the moment the 15-minute window opened
    target_spot = fetch_spot_near_time(product, window_open)
    target = round_target(target_spot, product)

    # Settlement: Coinbase spot at the moment the window closed (expiry)
    expiry_spot = fetch_spot_near_time(product, expiry_time)

    # YES wins when price ended at or above the opening strike
    yes_outcome = 1 if expiry_spot >= target else 0

    return target, expiry_spot, yes_outcome


def pnl_for_trade(side: str, price: int, yes_outcome: int):
    """Compute P&L for a trade.

    `price` is always the YES mid-price (as stored in the journal).
    For a YES trade you paid `price` cents; for a NO trade you paid
    `100 - price` cents.  P&L is always relative to cost_paid:
        win  → 100 - cost_paid
        lose → -cost_paid
    """
    side = side.lower()
    won = (yes_outcome == 1 and side == "yes") or (yes_outcome == 0 and side == "no")
    cost = price if side == "yes" else (100 - price)
    pnl_cents = (100 - cost) if won else -cost
    return won, pnl_cents


def backtest_journal(journal_path: str) -> pd.DataFrame:
    df = pd.read_csv(journal_path)
    dry = df[df["status"] == "dry_run"].copy()

    results = []
    now = datetime.now(timezone.utc)

    for _, row in dry.iterrows():
        ticker = str(row["ticker"])
        prefix = asset_prefix_from_ticker(ticker)

        base = {
            "ts_utc": row["ts_utc"],
            "ticker": ticker,
            "side": row["side"],
            "price": int(row["price"]),
            "edge_cents": int(row["edge_cents"]),
            "spread_cents": int(row["spread_cents"]),
            "score": float(row["score"]),
            "reason": row["reason"],
        }

        if prefix is None:
            results.append({**base, "status_bt": "error", "error": "unknown asset prefix"})
            continue

        product = COINBASE_PRODUCTS[prefix]

        try:
            _, expiry_time, _ = parse_market_ticker(ticker)
        except Exception as e:
            results.append({**base, "status_bt": "error", "error": f"parse error: {e}"})
            continue

        if expiry_time > now:
            results.append({
                **base,
                "status_bt": "skipped_future",
                "expiry_time": expiry_time.isoformat(),
            })
            continue

        try:
            strike, spot_at_expiry, yes_outcome = resolve_yes_outcome(
                ticker=ticker,
                product=product,
                expiry_time=expiry_time,
            )
            won, pnl_cents = pnl_for_trade(str(row["side"]), int(row["price"]), yes_outcome)

            results.append({
                **base,
                "status_bt": "resolved",
                "product": product,
                "expiry_time": expiry_time.isoformat(),
                "strike": strike,
                "spot_at_expiry": spot_at_expiry,
                "yes_outcome": yes_outcome,
                "predicted_side_won": won,
                "pnl_cents": pnl_cents,
            })
        except Exception as e:
            results.append({
                **base,
                "status_bt": "error",
                "product": product,
                "expiry_time": expiry_time.isoformat(),
                "error": str(e),
            })

    return pd.DataFrame(results)


def print_summary(results: pd.DataFrame) -> None:
    print("\n===== BACKTEST STATUS =====")
    print(results["status_bt"].value_counts(dropna=False).to_string())

    resolved = results[results["status_bt"] == "resolved"].copy()
    if resolved.empty:
        print("\nNo trades could be resolved.")
        return

    total = len(resolved)
    wins = int((resolved["pnl_cents"] > 0).sum())
    losses = int((resolved["pnl_cents"] <= 0).sum())
    print("\n===== BACKTEST SUMMARY =====")
    print(f"Resolved trades: {total}")
    print(f"Wins:            {wins}")
    print(f"Losses:          {losses}")
    print(f"Win rate:        {wins/total:.2%}")
    print(f"Total P&L:       {resolved['pnl_cents'].sum():.1f} cents")
    print(f"Average P&L:     {resolved['pnl_cents'].mean():.2f} cents/trade")

    resolved["asset"] = resolved["ticker"].str.extract(r"^(KX[A-Z0-9]+15M)")
    print("\n===== BY ASSET =====")
    print(
        resolved.groupby("asset")["pnl_cents"]
        .agg(["count", "sum", "mean"])
        .sort_values("sum", ascending=False)
        .to_string()
    )


def main():
    results = backtest_journal(JOURNAL_PATH)
    Path(OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(OUTPUT_PATH, index=False)
    print_summary(results)
    print(f"\nSaved detailed results to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
