"""Collect settled KXBTC15M markets + their 1-minute candlesticks from Kalshi.

Outputs:
  data/markets.csv          one row per settled market (strike, result, times, volume)
  data/kalshi_candles.parquet  one row per (ticker, minute) with yes_bid/yes_ask/last

Public endpoints, no auth required.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXBTC15M"
SESSION = requests.Session()


def _get(url, params, retries=4):
    for a in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=25)
            if r.status_code == 429:
                time.sleep(0.5 * (2 ** a))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if a == retries - 1:
                raise
            time.sleep(0.4 * (2 ** a))
    raise RuntimeError("unreachable")


def iso_to_ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def collect_markets(max_markets: int) -> pd.DataFrame:
    rows = []
    cursor = None
    while len(rows) < max_markets:
        params = {"series_ticker": SERIES, "status": "settled", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        j = _get(f"{BASE}/markets", params)
        mk = j.get("markets", [])
        if not mk:
            break
        for m in mk:
            if m.get("result") not in ("yes", "no"):
                continue
            rows.append({
                "ticker": m["ticker"],
                "open_time": m["open_time"],
                "close_time": m["close_time"],
                "open_ts": iso_to_ts(m["open_time"]),
                "close_ts": iso_to_ts(m["close_time"]),
                "floor_strike": float(m.get("floor_strike") or 0.0),
                "result": m["result"],
                "volume": float(m.get("volume_fp") or 0.0),
                "open_interest": float(m.get("open_interest_fp") or 0.0),
            })
        cursor = j.get("cursor")
        print(f"  markets so far: {len(rows)} (last close {rows[-1]['close_time']})", flush=True)
        if not cursor:
            break
        time.sleep(0.15)
    df = pd.DataFrame(rows[:max_markets])
    return df


def _px(d, field, key="close_dollars"):
    v = d.get(field, {})
    val = v.get(key)
    return float(val) if val not in (None, "") else None


def collect_candles(markets: pd.DataFrame) -> pd.DataFrame:
    out = []
    n = len(markets)
    for i, m in enumerate(markets.itertuples(index=False)):
        url = f"{BASE}/series/{SERIES}/markets/{m.ticker}/candlesticks"
        try:
            j = _get(url, {"start_ts": m.open_ts - 120, "end_ts": m.close_ts + 120, "period_interval": 1})
        except Exception as e:
            print(f"  [{i+1}/{n}] {m.ticker} candle ERR {e}", flush=True)
            continue
        for c in j.get("candlesticks", []):
            out.append({
                "ticker": m.ticker,
                "end_ts": int(c["end_period_ts"]),
                "yes_bid": _px(c, "yes_bid"),
                "yes_ask": _px(c, "yes_ask"),
                "last": _px(c, "price"),
                "mean": _px(c, "price", "mean_dollars"),
                "volume": float(c.get("volume_fp") or 0.0),
            })
        if (i + 1) % 50 == 0:
            print(f"  candles [{i+1}/{n}] rows={len(out)}", flush=True)
        time.sleep(0.05)
    return pd.DataFrame(out)


def main():
    max_markets = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
    print(f"Collecting up to {max_markets} settled {SERIES} markets...")
    markets = collect_markets(max_markets)
    print(f"Got {len(markets)} markets, "
          f"range {markets['close_time'].min()} .. {markets['close_time'].max()}")
    markets.to_csv("data/markets.csv", index=False)
    print("Collecting candlesticks...")
    candles = collect_candles(markets)
    candles.to_parquet("data/kalshi_candles.parquet", index=False)
    print(f"Saved {len(markets)} markets and {len(candles)} candle rows.")


if __name__ == "__main__":
    main()
