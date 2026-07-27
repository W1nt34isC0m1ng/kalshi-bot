"""Kalshi trades for ALL markets in markets.csv, sharded to parquet (memory-safe).

Writes data/trades_shards/shard_NNNN.parquet every SHARD_MARKETS markets, then
the analysis reads the whole directory. Resumable: skips markets already covered
by an existing shard manifest.
"""
from __future__ import annotations
import os, time, glob
import pandas as pd
from collect_hifreq import get, KBASE

SHARD_DIR = "data/trades_shards"
SHARD_MARKETS = 100
os.makedirs(SHARD_DIR, exist_ok=True)


def done_tickers():
    done = set()
    for f in glob.glob(f"{SHARD_DIR}/shard_*.parquet"):
        try:
            done |= set(pd.read_parquet(f, columns=["ticker"])["ticker"].unique())
        except Exception:
            pass
    return done


def fetch_one(ticker):
    rows, cursor = [], None
    while True:
        params = {"ticker": ticker, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        j = get(f"{KBASE}/markets/trades", params)
        tr = j.get("trades", [])
        for x in tr:
            rows.append({
                "ticker": ticker,
                "ts": pd.Timestamp(x["created_time"]).timestamp(),
                "yes_price": float(x.get("yes_price_dollars") or 0),
                "no_price": float(x.get("no_price_dollars") or 0),
                "count": float(x.get("count_fp") or 0),
                "taker_side": x.get("taker_side"),
            })
        cursor = j.get("cursor")
        if not cursor or not tr:
            break
        time.sleep(0.02)
    return rows


def main():
    markets = pd.read_csv("data/markets.csv")
    done = done_tickers()
    todo = [t for t in markets["ticker"].tolist() if t not in done]
    print(f"{len(markets)} markets, {len(done)} already done, {len(todo)} to fetch")
    buf, shard_idx = [], len(glob.glob(f"{SHARD_DIR}/shard_*.parquet"))
    processed = 0
    for i, tk in enumerate(todo):
        try:
            buf.extend(fetch_one(tk))
        except Exception as e:
            print(f"  ERR {tk}: {e}", flush=True)
        processed += 1
        if processed % SHARD_MARKETS == 0 or i == len(todo) - 1:
            if buf:
                pd.DataFrame(buf).to_parquet(f"{SHARD_DIR}/shard_{shard_idx:04d}.parquet", index=False)
                print(f"  shard {shard_idx}: {len(buf)} trades after {i+1}/{len(todo)} markets", flush=True)
                shard_idx += 1
                buf = []
        time.sleep(0.02)
    print("done")


if __name__ == "__main__":
    main()
