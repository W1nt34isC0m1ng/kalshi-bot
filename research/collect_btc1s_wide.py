"""Binance 1s klines for the FULL markets.csv span (regime-diverse window)."""
import pandas as pd
from collect_hifreq import collect_binance_1s

m = pd.read_csv("data/markets.csv")
start = int(m["open_ts"].min() - 120)
end = int(m["close_ts"].max() + 120)
print(f"btc 1s {start}..{end} ({(end-start)/86400:.1f} days)")
btc = collect_binance_1s(start * 1000, end * 1000)
btc.to_parquet("data/btc_1s_wide.parquet", index=False)
print(f"saved {len(btc)} 1s candles")
