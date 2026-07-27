"""Realistic maker-fill simulation using the trade stream as a matching engine.

We have no historical L2 book, but every Kalshi trade print carries a
`taker_side`, which reveals the touched price:
  taker_side == 'yes'  -> a taker LIFTED the ask  => ask ~= yes_price
  taker_side == 'no'   -> a taker HIT the bid     => bid ~= yes_price

Scalp signal: |underlying 3s log-return| > thr.
  bullish -> we want to be long YES: post a passive BUY YES at the bid.
             It fills only if a later taker SELL (taker_side=='no') prints at
             a price <= our bid within FILL_WINDOW seconds.
  bearish -> long NO: post passive BUY NO at (1 - ask), i.e. sell YES at ask;
             fills only if a later taker BUY (taker_side=='yes') prints >= ask.

This directly measures adverse selection: do we get filled on the moves that
go our way, or only when the move reverses?

Exit modeled as taker H seconds after fill (maker-entry / taker-exit = the
realistic +EV regime). Fees: maker on entry, taker on exit.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd

btc = pd.read_parquet("data/btc_1s.parquet").drop_duplicates("ts").set_index("ts").sort_index()
trades = pd.read_parquet("data/kalshi_trades.parquet")
mk = pd.read_csv("data/markets_hf.csv").set_index("ticker")
spot = btc["close"]

THR = 0.0007
FILL_WINDOW = 5     # seconds we leave the passive order resting
H = 5               # hold seconds after fill, then exit as taker
def taker_fee(p): return math.ceil(0.07 * p * (1 - p) * 100) / 100
def maker_fee(p): return 0.0175 * p * (1 - p)

results = []
fills = 0
signals = 0
for tk, g in trades.groupby("ticker"):
    if tk not in mk.index:
        continue
    m = mk.loc[tk]; o, c = int(m.open_ts), int(m.close_ts)
    g = g.sort_values("ts")
    tts = g["ts"].values
    typ = g["yes_price"].values
    tside = g["taker_side"].values
    # reconstructed touch series on 1s grid
    grid = np.arange(o, c)
    ask = pd.Series(np.where(tside == "yes", typ, np.nan), index=tts)
    bid = pd.Series(np.where(tside == "no", typ, np.nan), index=tts)
    # last touch as of each grid second
    ai = np.searchsorted(tts, grid, side="right") - 1
    # forward-filled touch arrays aligned to trade order
    ask_ff = ask.ffill().values
    bid_ff = bid.ffill().values
    grid_ask = np.where(ai >= 0, ask_ff[ai], np.nan)
    grid_bid = np.where(ai >= 0, bid_ff[ai], np.nan)
    u = spot.reindex(grid).ffill().values
    if np.isnan(u).any():
        continue
    mom = np.r_[[np.nan]*3, np.log(u[3:] / u[:-3])]

    for i, t in enumerate(grid):
        msig = mom[i]
        if not np.isfinite(msig) or abs(msig) < THR:
            continue
        if c - t < H + FILL_WINDOW + 2:
            continue
        signals += 1
        # window of trades to look for a fill
        lo = np.searchsorted(tts, t, side="right")
        hi = np.searchsorted(tts, t + FILL_WINDOW, side="right")
        win_p = typ[lo:hi]; win_s = tside[lo:hi]; win_t = tts[lo:hi]
        if msig > 0:  # long YES: rest BUY at bid; fill if a taker SELL <= bid
            px = grid_bid[i]
            if not np.isfinite(px):
                continue
            hit = np.where((win_s == "no") & (win_p <= px + 1e-9))[0]
            if len(hit) == 0:
                results.append({"filled": 0, "side": "yes"}); continue
            tf = win_t[hit[0]]
            ei = np.searchsorted(grid, tf + H, side="left")
            if ei >= len(grid):
                continue
            exit_px = grid_bid[ei]  # exit by hitting the bid (taker sell)
            if not np.isfinite(exit_px):
                continue
            pnl = (exit_px - px) - maker_fee(px) - taker_fee(exit_px)
        else:        # long NO == short YES: rest SELL at ask; fill if taker BUY >= ask
            px = grid_ask[i]
            if not np.isfinite(px):
                continue
            hit = np.where((win_s == "yes") & (win_p >= px - 1e-9))[0]
            if len(hit) == 0:
                results.append({"filled": 0, "side": "no"}); continue
            tf = win_t[hit[0]]
            ei = np.searchsorted(grid, tf + H, side="left")
            if ei >= len(grid):
                continue
            exit_px = grid_ask[ei]  # exit by lifting the ask (taker buy) to flatten short
            if not np.isfinite(exit_px):
                continue
            # short YES pnl = entry_ask - exit_ask
            pnl = (px - exit_px) - maker_fee(px) - taker_fee(exit_px)
        fills += 1
        results.append({"filled": 1, "side": "yes" if msig > 0 else "no", "pnl": pnl,
                        "fill_delay": tf - t})

R = pd.DataFrame(results)
nf = R[R.filled == 1]
print(f"signals: {signals}   posted: {len(R)}   filled: {len(nf)}  "
      f"fill_rate: {len(nf)/max(len(R),1):.1%}")
if len(nf):
    p = nf["pnl"].values
    t = p.mean() / (p.std(ddof=1)/math.sqrt(len(p))) if p.std() > 0 else float("nan")
    print(f"\nCONDITIONAL P&L on FILLED maker-entry/taker-exit scalps:")
    print(f"  n={len(p)}  mean={100*p.mean():+.2f}c  win={(p>0).mean():.1%}  "
          f"t={t:+.2f}  total=${p.sum():+.1f}")
    print(f"  median fill delay: {nf['fill_delay'].median():.1f}s")
    print(f"\nContrast: the earlier 'assume always filled' maker/taker model = +1.9c.")
    print("If conditional-on-fill P&L << +1.9c, that gap IS the adverse selection.")
