"""Re-collect candlesticks from existing markets.csv (fresh process, parquet OK)."""
import pandas as pd
from collect_kalshi import collect_candles

markets = pd.read_csv("data/markets.csv")
print(f"Collecting candles for {len(markets)} markets...")
candles = collect_candles(markets)
candles.to_parquet("data/kalshi_candles.parquet", index=False)
print(f"Saved {len(candles)} candle rows for {candles['ticker'].nunique()} markets.")
