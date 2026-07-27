"""Characterize the lead-lag scalp: exit-horizon, spread cost, fee regime, and
time-robustness. Signal = underlying 3s momentum; trade the Kalshi quote.

Fill model (per leg), using last-trade price k as the mid proxy:
  taker: cross half-spread -> pay fee 0.07*p*(1-p)
  maker: rest at mid +/- half-spread (earn spread) -> fee = MAKER_FEE
We report several (entry,exit) fill regimes.
"""
import math
import numpy as np
import pandas as pd
from btlib import kalshi_fee

_feevec = np.vectorize(kalshi_fee)
btc = pd.read_parquet("data/btc_1s.parquet").drop_duplicates("ts").set_index("ts").sort_index()
trades = pd.read_parquet("data/kalshi_trades.parquet")
mk = pd.read_csv("data/markets_hf.csv").set_index("ticker")
spot = btc["close"]

# Build scalp observation table once, with multiple exit horizons -------------
HS = [2, 3, 5, 8]
recs = []
median_open = mk["open_ts"].median()
for tk, g in trades.groupby("ticker"):
    if tk not in mk.index: continue
    m = mk.loc[tk]; o, c = int(m.open_ts), int(m.close_ts)
    grid = np.arange(o, c)
    g = g.sort_values("ts")
    idx = np.searchsorted(g["ts"].values, grid, side="right") - 1
    kp = pd.Series(np.nan, index=grid, dtype=float); v = idx >= 0
    kp[grid[v]] = g["yes_price"].values[idx[v]]; kp = kp.ffill()
    u = spot.reindex(grid).ffill()
    if kp.isna().all() or u.isna().any(): continue
    mom = np.log(u).diff(3).values
    kv = kp.values
    d = {"mom": mom, "k": kv, "first_half": o < median_open}
    for H in HS:
        d[f"kf{H}"] = np.r_[kv[H:], [np.nan]*H]
    recs.append(pd.DataFrame(d))
sc = pd.concat(recs)
sc = sc[(sc.k > 0) & (sc.k < 1)].dropna()
print(f"scalp obs: {len(sc)}\n")

def stats(pnl):
    if len(pnl) == 0: return None
    m = pnl.mean(); sd = pnl.std(ddof=1) if len(pnl) > 1 else 0
    t = m/(sd/math.sqrt(len(pnl))) if sd > 0 else float('nan')
    return len(pnl), 100*m, (pnl > 0).mean(), t, pnl.sum()
def show(lbl, r):
    if not r: print(f"{lbl}: none"); return
    n, mc, wr, t, tot = r
    print(f"{lbl:<42} n={n:>6} mean={mc:+6.2f}c win={wr:.1%} t={t:+6.1f} tot=${tot:+7.0f}")

def maker_fee(p):  # conservative: ~1/4 of taker, 1.75% * p * (1-p)
    return 0.0175 * p * (1.0 - p)
_makervec = np.vectorize(maker_fee)
def pnl_regime(sel, H, spread, entry_taker, exit_taker):
    sgn = np.sign(sel.mom.values)
    k = sel.k.values; kf = sel[f"kf{H}"].values
    hs = spread / 2.0
    # entry price (what we pay): long pays k+hs (taker) or k-hs (maker, earns spread)
    if entry_taker:
        entry_cost = np.where(sgn > 0, k + hs, (1 - k) + hs)
        entry_fee = _feevec(np.clip(entry_cost, 1e-4, 0.9999))
    else:
        entry_cost = np.where(sgn > 0, k - hs, (1 - k) - hs)
        entry_fee = _makervec(np.clip(np.where(sgn>0,k-hs,(1-k)-hs),1e-4,0.9999))
    # exit price (what we receive when closing the position we opened):
    if exit_taker:
        exit_recv = np.where(sgn > 0, kf - hs, (1 - kf) - hs)
        exit_fee = _feevec(np.clip(np.where(sgn > 0, kf, 1 - kf), 1e-4, 0.9999))
    else:
        exit_recv = np.where(sgn > 0, kf + hs, (1 - kf) + hs)
        exit_fee = _makervec(np.clip(np.where(sgn>0,kf,1-kf),1e-4,0.9999))
    return (exit_recv - entry_cost) - entry_fee - exit_fee

print("="*92)
print("EXIT-HORIZON x THRESHOLD, taker/taker, spread=1.0c (conservative)")
print("="*92)
for H in HS:
    for thr in (0.0005, 0.0007, 0.0010):
        sel = sc[sc.mom.abs() > thr]
        show(f"H={H}s thr={thr} TT", stats(pnl_regime(sel, H, 0.01, True, True)))

print("\n" + "="*92)
print("FEE/FILL REGIMES at H=5s, thr=0.0007, spread=1.0c")
print("="*92)
sel = sc[sc.mom.abs() > 0.0007]
for lbl, et, xt in [("taker/taker", True, True), ("maker/taker", False, True),
                    ("taker/maker", True, False), ("maker/maker", False, False)]:
    show(f"  {lbl}", stats(pnl_regime(sel, 5, 0.01, et, xt)))

print("\n" + "="*92)
print("MAKER/MAKER sensitivity to spread & threshold (H=5s)")
print("="*92)
for spread in (0.0, 0.005, 0.01):
    for thr in (0.0005, 0.0007, 0.0010):
        sel = sc[sc.mom.abs() > thr]
        show(f"  spread={spread*100:.1f}c thr={thr} MM", stats(pnl_regime(sel, 5, spread, False, False)))

print("\n" + "="*92)
print("TIME ROBUSTNESS: first half vs second half (H=5s, thr=0.0007)")
print("="*92)
for half, name in [(True, "first_half"), (False, "second_half")]:
    sel = sc[(sc.mom.abs() > 0.0007) & (sc.first_half == half)]
    show(f"  {name} taker/taker", stats(pnl_regime(sel, 5, 0.01, True, True)))
    show(f"  {name} maker/maker", stats(pnl_regime(sel, 5, 0.01, False, False)))
