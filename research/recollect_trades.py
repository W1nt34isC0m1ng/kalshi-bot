"""Re-collect Kalshi trades (Binance 1s already saved)."""
import pandas as pd
from collect_hifreq import collect_kalshi_trades

markets = pd.read_csv("data/markets.csv")
btc = pd.read_parquet("data/btc_1s.parquet")
start_ts = int(btc["ts"].min()) + 120
end_ts = int(btc["ts"].max()) - 120
win = markets[(markets.close_ts <= end_ts) & (markets.open_ts >= start_ts)].copy()
win.to_csv("data/markets_hf.csv", index=False)
print(f"{len(win)} markets in window")
tr = collect_kalshi_trades(win["ticker"].tolist())
tr.to_parquet("data/kalshi_trades.parquet", index=False)
print(f"saved {len(tr)} trades for {tr['ticker'].nunique() if len(tr) else 0} markets")
