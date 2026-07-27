"""Sub-minute lead-lag thesis.

(A) Mechanism: does the underlying (Binance 1s) lead the Kalshi traded price?
(B) Executable EV: at each Kalshi trade, use an underlying-derived fair value
    (no look-ahead) as a signal; if it disagrees with the trade price beyond a
    threshold, take that side at the trade price + fee, hold to settlement.
"""
import math
import numpy as np
import pandas as pd
from btlib import kalshi_fee

_erf = np.vectorize(math.erf)
def ncdf(x): return 0.5 * (1.0 + _erf(np.asarray(x, float) / math.sqrt(2.0)))

btc = pd.read_parquet("data/btc_1s.parquet").drop_duplicates("ts").set_index("ts").sort_index()
trades = pd.read_parquet("data/kalshi_trades.parquet")
mk = pd.read_csv("data/markets_hf.csv").set_index("ticker")
print(f"btc 1s: {len(btc)}  trades: {len(trades)}  markets: {mk.shape[0]}")

# spot known at integer second t = close of completed bucket [t-1, t)  -> close[t-1]
spot_ser = btc["close"]
# trailing 60s realized vol of 1s log-returns (per-second sigma)
logret_1s = np.log(btc["close"]).diff()
rv60 = logret_1s.rolling(60).std()

def spot_at(t):
    t = int(math.floor(t)) - 1
    return spot_ser.get(t, np.nan)
def rv_at(t):
    t = int(math.floor(t)) - 1
    return rv60.get(t, np.nan)

# ---------------------------------------------------------------------------
# (A) Lead-lag cross-correlation on a 1s grid, pooled across markets.
# corr( underlying_return[t-lag .. t], kalshi_pricechange[t .. t+1] )
# We forward-fill the last Kalshi trade price per market on a 1s grid.
# ---------------------------------------------------------------------------
print("\n" + "="*70)
print("(A) Lead-lag: corr of past underlying return vs next Kalshi price move")
print("="*70)
und_changes = {}   # lag -> list
kal_next = []
sample = 0
for tk, g in trades.groupby("ticker"):
    if tk not in mk.index: continue
    m = mk.loc[tk]
    o, c = int(m.open_ts), int(m.close_ts)
    g = g.sort_values("ts")
    grid = np.arange(o, c)
    # forward-fill last yes_price on the grid
    kp = pd.Series(np.nan, index=grid, dtype=float)
    idx = np.searchsorted(g["ts"].values, grid, side="right") - 1
    valid = idx >= 0
    kp[grid[valid]] = g["yes_price"].values[idx[valid]]
    kp = kp.ffill()
    if kp.notna().sum() < 60: continue
    u = spot_ser.reindex(grid).ffill()
    if u.isna().any(): continue
    uret = np.log(u).diff()
    dkal = kp.diff().shift(-1)   # next-second kalshi change
    for lag in (1, 2, 3, 5, 10):
        ur = np.log(u).diff(lag)
        und_changes.setdefault(lag, []).append(pd.DataFrame({"x": ur.values, "y": dkal.values}))
    sample += 1
for lag in (1, 2, 3, 5, 10):
    df = pd.concat(und_changes[lag]).dropna()
    if len(df) > 100:
        cc = np.corrcoef(df["x"], df["y"])[0, 1]
        print(f"  lag={lag:2d}s  corr(underlying_ret[t-lag,t], kalshi_dprice[t,t+1]) = {cc:+.4f}  n={len(df)}")
print(f"  markets used: {sample}")

# ---------------------------------------------------------------------------
# (B) Executable EV at each Kalshi trade print.  Vectorized.
# ---------------------------------------------------------------------------
print("\n" + "="*70)
print("(B) Executable EV: take the side the underlying-fair favors, @ trade price")
print("="*70)
ev = trades[trades["ticker"].isin(mk.index)].copy()
ev["close_ts"] = ev["ticker"].map(mk["close_ts"]).astype(float)
ev["open_ts"]  = ev["ticker"].map(mk["open_ts"]).astype(float)
ev["strike"]   = ev["ticker"].map(mk["floor_strike"]).astype(float)
ev["result_yes"] = (ev["ticker"].map(mk["result"]) == "yes").astype(int)
ev["secs_left"] = ev["close_ts"] - ev["ts"]
ev = ev[(ev.ts >= ev.open_ts) & (ev.ts <= ev.close_ts) & (ev.secs_left >= 5)]
ev = ev[(ev.yes_price > 0) & (ev.yes_price < 1)]
# no-look-ahead spot & vol: completed 1s bucket at floor(ts)-1
lk = np.floor(ev["ts"].values).astype(np.int64) - 1
ev["spot"] = spot_ser.reindex(lk).values
ev["rvsec"] = rv60.reindex(lk).values
ev = ev.dropna(subset=["spot", "rvsec"])
ev = ev[(ev.spot > 0) & (ev.rvsec > 0) & (ev.strike > 0)]
sig = ev["rvsec"].values * np.sqrt(np.maximum(ev["secs_left"].values, 1e-6))
ev["fair"] = ncdf(np.log(ev["spot"].values / ev["strike"].values) / np.maximum(sig, 1e-9))
ev["p"] = ev["yes_price"]
ev["gap"] = ev["fair"] - ev["p"]
ev["dist_bps"] = 1e4 * np.log(ev["spot"].values / ev["strike"].values)
print(f"  evaluable trades: {len(ev)}")

_feevec = np.vectorize(kalshi_fee)
def run(thr, spread_cushion=0.0):
    gap = ev["gap"].values; p = ev["p"].values
    res = ev["result_yes"].values
    buy_yes = gap > thr
    buy_no = gap < -thr
    cost = np.where(buy_yes, p + spread_cushion, (1 - p) + spread_cushion)
    payoff = np.where(buy_yes, res, 1 - res)
    take = (buy_yes | buy_no) & (cost < 1)
    cost = cost[take]; payoff = payoff[take]
    if len(cost) == 0: return None
    pnl = (payoff - cost) - _feevec(cost)
    mean = pnl.mean(); sd = pnl.std(ddof=1) if len(pnl) > 1 else 0
    t = mean / (sd / math.sqrt(len(pnl))) if sd > 0 else float("nan")
    return len(pnl), 100*mean, (pnl > 0).mean(), t, pnl.sum()

for cush in (0.0, 0.01):
    print(f"\n  -- spread cushion = {cush*100:.0f}c (cost added to every fill) --")
    for thr in (0.03, 0.05, 0.08, 0.12, 0.15):
        res = run(thr, cush)
        if res:
            n, mc, wr, t, tot = res
            print(f"    thr={thr:.2f}  n={n:>6}  mean={mc:+6.2f}c  win={wr:.1%}  t={t:+5.2f}  tot=${tot:+7.1f}")
