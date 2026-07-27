"""Decisive tests on the lead-lag:
 (B2) GROSS settlement edge of the underlying-fair gap (no fees) -> is there ANY info?
 (C)  Underlying momentum -> settlement edge (gross & net).
 (D)  Quote-capture scalp: buy on signal, sell H seconds later at the Kalshi price.
      Report gross, and net under taker (0.07*p(1-p) both sides) and maker(=0) fees.
"""
import math
import numpy as np
import pandas as pd
from btlib import kalshi_fee

_erf = np.vectorize(math.erf)
def ncdf(x): return 0.5 * (1.0 + _erf(np.asarray(x, float) / math.sqrt(2.0)))
_feevec = np.vectorize(kalshi_fee)

btc = pd.read_parquet("data/btc_1s.parquet").drop_duplicates("ts").set_index("ts").sort_index()
trades = pd.read_parquet("data/kalshi_trades.parquet")
mk = pd.read_csv("data/markets_hf.csv").set_index("ticker")
spot_ser = btc["close"]
rv60 = np.log(btc["close"]).diff().rolling(60).std()

ev = trades[trades["ticker"].isin(mk.index)].copy()
for col, src in [("close_ts","close_ts"),("open_ts","open_ts"),("strike","floor_strike")]:
    ev[col] = ev["ticker"].map(mk[src]).astype(float)
ev["result_yes"] = (ev["ticker"].map(mk["result"]) == "yes").astype(int)
ev["secs_left"] = ev["close_ts"] - ev["ts"]
ev = ev[(ev.ts >= ev.open_ts) & (ev.ts <= ev.close_ts) & (ev.secs_left >= 5)]
ev = ev[(ev.yes_price > 0) & (ev.yes_price < 1)]
lk = np.floor(ev["ts"].values).astype(np.int64) - 1
ev["spot"] = spot_ser.reindex(lk).values
ev["rvsec"] = rv60.reindex(lk).values
ev["spot10"] = spot_ser.reindex(lk - 10).values   # spot 10s earlier
ev["spot30"] = spot_ser.reindex(lk - 30).values
ev = ev.dropna(subset=["spot","rvsec","spot10","spot30"])
ev = ev[(ev.spot>0)&(ev.rvsec>0)&(ev.strike>0)]
sig = ev["rvsec"].values * np.sqrt(np.maximum(ev["secs_left"].values,1e-6))
ev["fair"] = ncdf(np.log(ev["spot"].values/ev["strike"].values)/np.maximum(sig,1e-9))
ev["p"] = ev["yes_price"].values
ev["gap"] = ev["fair"] - ev["p"]
ev["mom10"] = np.log(ev["spot"].values/ev["spot10"].values)
ev["mom30"] = np.log(ev["spot"].values/ev["spot30"].values)
print(f"evaluable trades: {len(ev)}")

def stats(pnl):
    if len(pnl)==0: return None
    m=pnl.mean(); sd=pnl.std(ddof=1) if len(pnl)>1 else 0
    t=m/(sd/math.sqrt(len(pnl))) if sd>0 else float('nan')
    return len(pnl),100*m,(pnl>0).mean(),t,pnl.sum()
def show(lbl,res):
    if not res: print(f"{lbl}: none"); return
    n,mc,wr,t,tot=res
    print(f"{lbl:<30} n={n:>7} mean={mc:+6.2f}c win={wr:.1%} t={t:+6.1f} tot=${tot:+8.0f}")

# (B2) GROSS settlement edge of fair-gap (no fee, no cushion) ----------------
print("\n(B2) GROSS settlement EV of underlying-fair gap (NO fees):")
for thr in (0.03,0.05,0.08,0.12):
    by = ev.gap>thr; no=ev.gap<-thr
    cost=np.where(by, ev.p, 1-ev.p); pay=np.where(by, ev.result_yes, 1-ev.result_yes)
    take=(by|no); pnl=(pay-cost)[take]
    show(f"  gross thr={thr:.2f}", stats(pnl))

# (C) underlying momentum -> settlement (does recent move predict result?) ---
print("\n(C) Momentum->settlement: buy YES if mom>thr else NO if mom<-thr, @ p")
for col in ("mom10","mom30"):
    for thr in (0.0003,0.0007,0.0015):
        by=ev[col]>thr; no=ev[col]<-thr
        cost=np.where(by, ev.p, 1-ev.p); pay=np.where(by, ev.result_yes, 1-ev.result_yes)
        take=(by|no)
        gross=(pay-cost)[take]
        net=gross-_feevec(cost[take])
        show(f"  {col}>{thr} GROSS", stats(gross))
        show(f"  {col}>{thr} NET ", stats(net))

# (D) Quote-capture scalp: enter at signal, exit H sec later at Kalshi price --
# Build per-market 1s forward-filled Kalshi price; signal = underlying mom over
# last 3s; pnl_gross = (price[t+H]-price[t]) for longs (sign by signal).
print("\n(D) Quote-capture scalp (buy/sell the Kalshi price as underlying leads):")
H=5
recs=[]
for tk,g in trades.groupby("ticker"):
    if tk not in mk.index: continue
    m=mk.loc[tk]; o,c=int(m.open_ts),int(m.close_ts)
    grid=np.arange(o,c)
    g=g.sort_values("ts")
    idx=np.searchsorted(g["ts"].values,grid,side="right")-1
    kp=pd.Series(np.nan,index=grid,dtype=float); v=idx>=0
    kp[grid[v]]=g["yes_price"].values[idx[v]]; kp=kp.ffill()
    u=spot_ser.reindex(grid).ffill()
    if kp.isna().all() or u.isna().any(): continue
    mom=np.log(u).diff(3).values
    kpv=kp.values
    future=np.r_[kpv[H:], [np.nan]*H]
    recs.append(pd.DataFrame({"mom":mom,"k":kpv,"kf":future}))
sc=pd.concat(recs).dropna()
sc=sc[(sc.k>0)&(sc.k<1)]
print(f"  scalp obs: {len(sc)}  (H={H}s)")
for thr in (0.0003,0.0007,0.0015):
    sel=sc[sc.mom.abs()>thr]
    sgn=np.sign(sel.mom.values)
    gross=sgn*(sel.kf.values-sel.k.values)           # quote catch-up captured
    # taker round trip: pay fee on entry cost and exit cost (~p(1-p) each)
    entry=np.where(sgn>0,sel.k.values,1-sel.k.values)
    exitp=np.where(sgn>0,sel.kf.values,1-sel.kf.values)
    taker=gross-_feevec(entry)-_feevec(exitp)
    show(f"  |mom3|>{thr} GROSS", stats(gross))
    show(f"  |mom3|>{thr} NET taker", stats(taker))
