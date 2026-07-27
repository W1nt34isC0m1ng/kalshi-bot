"""Collect 1-minute BTC-USD candles from Coinbase over the span of markets.csv.

Coinbase settles the Kalshi crypto markets, so this is the correct underlying.
Saves data/btc_1m.parquet with columns: ts, open, high, low, close, volume.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

PRODUCT = "BTC-USD"
URL = f"https://api.exchange.coinbase.com/products/{PRODUCT}/candles"
SESSION = requests.Session()


def _get(params, retries=5):
    for a in range(retries):
        r = SESSION.get(URL, params=params, timeout=25)
        if r.status_code == 429:
            time.sleep(0.6 * (2 ** a))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("rate limited")


def main():
    markets = pd.read_csv("data/markets.csv")
    start = datetime.fromtimestamp(markets["open_ts"].min() - 1800, tz=timezone.utc)
    end = datetime.fromtimestamp(markets["close_ts"].max() + 1800, tz=timezone.utc)
    print(f"Coinbase 1m candles {start} .. {end}")

    rows = []
    cur = start
    step = timedelta(minutes=300)  # 300 candles/request max
    while cur < end:
        chunk_end = min(cur + step, end)
        data = _get({
            "granularity": 60,
            "start": cur.isoformat().replace("+00:00", "Z"),
            "end": chunk_end.isoformat().replace("+00:00", "Z"),
        })
        for c in data:  # [time, low, high, open, close, volume]
            rows.append({"ts": int(c[0]), "low": c[1], "high": c[2],
                         "open": c[3], "close": c[4], "volume": c[5]})
        cur = chunk_end
        time.sleep(0.25)

    df = pd.DataFrame(rows).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df.to_parquet("data/btc_1m.parquet", index=False)
    gaps = df["ts"].diff().dropna()
    print(f"Saved {len(df)} candles. Expected ~{int((end-start).total_seconds()/60)}. "
          f"Max gap {gaps.max():.0f}s")


if __name__ == "__main__":
    main()
