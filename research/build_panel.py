"""Join markets + Kalshi minute quotes + Coinbase spot into a modeling panel.

One row per (market, minute-inside-window) with executable prices, the
underlying spot path, engineered features, and the realized outcome.

Saves data/panel.parquet.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW = 900  # 15 min


def load():
    markets = pd.read_csv("data/markets.csv")
    candles = pd.read_parquet("data/kalshi_candles.parquet")
    btc = pd.read_parquet("data/btc_1m.parquet").sort_values("ts").reset_index(drop=True)
    return markets, candles, btc


def build():
    markets, candles, btc = load()

    # --- Coinbase spot lookups, minute-aligned --------------------------------
    btc = btc.drop_duplicates("ts").set_index("ts")
    btc_close = btc["close"]
    # trailing realized vol (std of 1-min log returns) as a Series indexed by ts
    logret = np.log(btc["close"]).diff()
    rv20 = logret.rolling(20).std()       # per-minute sigma, 20-min window
    rv5 = logret.rolling(5).std()
    ret5 = np.log(btc["close"]).diff(5)   # 5-min momentum
    ret3 = np.log(btc["close"]).diff(3)

    def at(ts, series):
        # NO LOOK-AHEAD: a quote at end_ts=T can only use the Coinbase candle
        # that has fully closed by T. Coinbase `time` is the bucket START and the
        # close prints at start+60s, so the last realized close at T is the
        # bucket starting at T-60.
        ts = int(round(ts / 60) * 60) - 60
        if ts in series.index:
            v = series.loc[ts]
            return float(v) if pd.notna(v) else np.nan
        return np.nan

    m = markets.set_index("ticker")
    rows = []
    for c in candles.itertuples(index=False):
        if c.ticker not in m.index:
            continue
        mk = m.loc[c.ticker]
        end_ts = int(c.end_ts)
        secs_left = int(mk.close_ts) - end_ts
        # keep only quotes strictly inside the trading window
        if secs_left < 0 or secs_left > WINDOW or end_ts < int(mk.open_ts):
            continue
        if c.yes_bid is None or c.yes_ask is None:
            continue
        if not (0 <= c.yes_bid <= 1 and 0 <= c.yes_ask <= 1) or c.yes_ask < c.yes_bid:
            continue

        spot = at(end_ts, btc_close)
        if np.isnan(spot):
            continue
        strike = float(mk.floor_strike)
        if strike <= 0:
            continue

        mid = (c.yes_bid + c.yes_ask) / 2.0
        log_ratio = np.log(spot / strike)
        rows.append({
            "ticker": c.ticker,
            "end_ts": end_ts,
            "secs_left": secs_left,
            "yes_bid": c.yes_bid,
            "yes_ask": c.yes_ask,
            "mid": mid,
            "spread": c.yes_ask - c.yes_bid,
            "kvol": c.volume,
            "spot": spot,
            "strike": strike,
            "log_ratio": log_ratio,
            "dist_bps": 1e4 * log_ratio,
            "rv20": at(end_ts, rv20),
            "rv5": at(end_ts, rv5),
            "ret5": at(end_ts, ret5),
            "ret3": at(end_ts, ret3),
            "result_yes": 1 if mk.result == "yes" else 0,
            "mkt_volume": float(mk.volume),
        })

    panel = pd.DataFrame(rows)
    panel.to_parquet("data/panel.parquet", index=False)

    # --- sanity / data-quality report -----------------------------------------
    print(f"Panel rows: {len(panel)}  markets: {panel['ticker'].nunique()}")
    # Validate: does Coinbase spot at expiry agree with Kalshi's result?
    last = panel.sort_values("secs_left").groupby("ticker").first()  # smallest secs_left
    last_spot_above = (last["spot"] >= last["strike"]).astype(int)
    agree = (last_spot_above == last["result_yes"]).mean()
    print(f"Coinbase-spot-at-last-quote vs Kalshi result agreement: {agree:.3%}")
    print(f"Base rate YES (per market): {m['result'].eq('yes').mean():.3%}")
    print(f"Median spread (cents): {100*panel['spread'].median():.1f}")
    print(panel[["secs_left", "mid", "spread", "dist_bps", "rv20"]].describe().to_string())
    return panel


if __name__ == "__main__":
    build()
