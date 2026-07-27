"""Collect high-frequency data to test the sub-minute lead-lag thesis.

  data/btc_1s.parquet      Binance 1s klines (underlying) over recent window
  data/kalshi_trades.parquet  Kalshi trade prints (sub-second) for window markets
"""
from __future__ import annotations
import sys, time
from datetime import datetime, timezone
import pandas as pd, requests

KBASE = "https://api.elections.kalshi.com/trade-api/v2"
BVISION = "https://data-api.binance.vision/api/v3/klines"
S = requests.Session()
DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 2.5


def get(url, params, retries=5):
    for a in range(retries):
        r = S.get(url, params=params, timeout=25)
        if r.status_code == 429:
            time.sleep(0.8 * (2 ** a)); continue
        r.raise_for_status(); return r.json()
    raise RuntimeError("rate limited")


def collect_binance_1s(start_ms, end_ms):
    rows, cur = [], start_ms
    while cur < end_ms:
        data = get(BVISION, {"symbol": "BTCUSDT", "interval": "1s",
                             "startTime": cur, "endTime": end_ms, "limit": 1000})
        if not data:
            break
        for k in data:
            rows.append({"ts": k[0] // 1000, "open": float(k[1]), "high": float(k[2]),
                         "low": float(k[3]), "close": float(k[4]), "vol": float(k[5])})
        cur = data[-1][0] + 1000
        if len(rows) % 50000 < 1000:
            print(f"  binance 1s rows={len(rows)} at {datetime.fromtimestamp(cur/1000, timezone.utc)}", flush=True)
        time.sleep(0.12)
    return pd.DataFrame(rows).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def collect_kalshi_trades(tickers):
    rows = []
    n = len(tickers)
    for i, t in enumerate(tickers):
        cursor = None
        while True:
            params = {"ticker": t, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            j = get(f"{KBASE}/markets/trades", params)
            tr = j.get("trades", [])
            for x in tr:
                rows.append({
                    "ticker": t,
                    "ts": pd.Timestamp(x["created_time"]).timestamp(),
                    "yes_price": float(x.get("yes_price_dollars") or 0),
                    "no_price": float(x.get("no_price_dollars") or 0),
                    "count": float(x.get("count_fp") or 0),
                    "taker_side": x.get("taker_side"),
                })
            cursor = j.get("cursor")
            if not cursor or not tr:
                break
            time.sleep(0.03)
        if (i + 1) % 25 == 0:
            print(f"  kalshi trades [{i+1}/{n}] rows={len(rows)}", flush=True)
        time.sleep(0.03)
    return pd.DataFrame(rows)


def main():
    markets = pd.read_csv("data/markets.csv")
    end_ts = int(markets["close_ts"].max())
    start_ts = int(end_ts - DAYS * 86400)
    win = markets[(markets.close_ts <= end_ts) & (markets.open_ts >= start_ts)].copy()
    print(f"Window {datetime.fromtimestamp(start_ts,timezone.utc)} .. {datetime.fromtimestamp(end_ts,timezone.utc)}; {len(win)} markets")

    print("Binance 1s...")
    btc = collect_binance_1s((start_ts - 120) * 1000, (end_ts + 120) * 1000)
    btc.to_parquet("data/btc_1s.parquet", index=False)
    print(f"  saved {len(btc)} 1s candles")

    print("Kalshi trades...")
    tr = collect_kalshi_trades(win["ticker"].tolist())
    tr.to_parquet("data/kalshi_trades.parquet", index=False)
    print(f"  saved {len(tr)} trades for {tr['ticker'].nunique() if len(tr) else 0} markets")
    win.to_csv("data/markets_hf.csv", index=False)


if __name__ == "__main__":
    main()
