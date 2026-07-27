"""Wide-window validation of the lead-lag scalp + realistic fill sim.

Streams over trade shards (memory-safe). Reports the maker-entry/taker-exit
scalp conditional on realistic fills, overall and by regime:
  - chronological thirds (early / mid / late)
  - per-window underlying drift sign (did BTC rise or fall over the 15 min)
"""
from __future__ import annotations
import glob, math
import numpy as np
import pandas as pd

btc = pd.read_parquet("data/btc_1s_wide.parquet").drop_duplicates("ts").set_index("ts").sort_index()
spot = btc["close"]
mk = pd.read_csv("data/markets.csv").set_index("ticker")
mk_open_order = mk["open_ts"].rank(method="first")
nmk = len(mk)

THR, FILL_WINDOW, H = 0.0007, 5, 5
def taker_fee(p): return math.ceil(0.07 * p * (1 - p) * 100) / 100
def maker_fee(p): return 0.0175 * p * (1 - p)


def sim_market(tk, g):
    if tk not in mk.index:
        return []
    m = mk.loc[tk]; o, c = int(m.open_ts), int(m.close_ts)
    g = g.sort_values("ts")
    tts = g["ts"].values; typ = g["yes_price"].values; tside = g["taker_side"].values
    grid = np.arange(o, c)
    ai = np.searchsorted(tts, grid, side="right") - 1
    ask_ff = pd.Series(np.where(tside == "yes", typ, np.nan), index=tts).ffill().values
    bid_ff = pd.Series(np.where(tside == "no", typ, np.nan), index=tts).ffill().values
    grid_ask = np.where(ai >= 0, ask_ff[ai], np.nan)
    grid_bid = np.where(ai >= 0, bid_ff[ai], np.nan)
    u = spot.reindex(grid).ffill().values
    if np.isnan(u).any():
        return []
    mom = np.r_[[np.nan]*3, np.log(u[3:] / u[:-3])]
    third = int(mk_open_order.get(tk, 0) / nmk * 3)  # 0,1,2
    out = []
    for i, t in enumerate(grid):
        msig = mom[i]
        if not np.isfinite(msig) or abs(msig) < THR or c - t < H + FILL_WINDOW + 2:
            continue
        lo = np.searchsorted(tts, t, side="right"); hi = np.searchsorted(tts, t + FILL_WINDOW, side="right")
        win_p, win_s, win_t = typ[lo:hi], tside[lo:hi], tts[lo:hi]
        rec = {"filled": 0, "third": third, "res_yes": 1 if m.result == "yes" else 0}
        if msig > 0:
            px = grid_bid[i]
            if not np.isfinite(px):
                continue
            hit = np.where((win_s == "no") & (win_p <= px + 1e-9))[0]
            if len(hit):
                tf = win_t[hit[0]]; ei = np.searchsorted(grid, tf + H, side="left")
                if ei < len(grid) and np.isfinite(grid_bid[ei]):
                    ex = grid_bid[ei]
                    rec.update(filled=1, pnl=(ex - px) - maker_fee(px) - taker_fee(ex))
        else:
            px = grid_ask[i]
            if not np.isfinite(px):
                continue
            hit = np.where((win_s == "yes") & (win_p >= px - 1e-9))[0]
            if len(hit):
                tf = win_t[hit[0]]; ei = np.searchsorted(grid, tf + H, side="left")
                if ei < len(grid) and np.isfinite(grid_ask[ei]):
                    ex = grid_ask[ei]
                    rec.update(filled=1, pnl=(px - ex) - maker_fee(px) - taker_fee(ex))
        out.append(rec)
    return out


results = []
shards = sorted(glob.glob("data/trades_shards/shard_*.parquet"))
print(f"streaming {len(shards)} shards...")
for s in shards:
    df = pd.read_parquet(s)
    for tk, g in df.groupby("ticker"):
        results.extend(sim_market(tk, g))
    print(f"  {s}: cumulative posted={len(results)}", flush=True)

R = pd.DataFrame(results)
nf = R[R.filled == 1].copy()
def rep(p, lbl):
    if len(p) == 0: print(f"{lbl}: none"); return
    t = p.mean()/(p.std(ddof=1)/math.sqrt(len(p))) if p.std() > 0 else float("nan")
    print(f"{lbl:<26} n={len(p):>6}  mean={100*p.mean():+5.2f}c  win={(p>0).mean():.1%}  t={t:+5.2f}  tot=${p.sum():+7.1f}")

print(f"\nposted={len(R)}  filled={len(nf)}  fill_rate={len(nf)/max(len(R),1):.1%}\n")
print("OVERALL & BY REGIME (maker-entry / taker-exit, conditional on fill):")
rep(nf["pnl"].values, "ALL")
for th in (0, 1, 2):
    rep(nf[nf.third == th]["pnl"].values, f"  chrono third {th}")
