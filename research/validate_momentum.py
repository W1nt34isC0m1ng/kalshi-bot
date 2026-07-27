"""Validate 'buy YES after sharp UP-jump, hold to settlement' (and NO/down).
Out-of-sample halves x entry-price variant x threshold. No look-ahead (spot[t-1]).
Entry variants:
  'ask'  : pay the last lift price (taker buy YES at ask / NO at 1-bid)
  'last' : pay the last trade price (yes_price), more conservative-neutral
"""
import glob, math
import numpy as np, pandas as pd
btc = pd.read_parquet("data/btc_1s_wide.parquet").drop_duplicates("ts").set_index("ts").sort_index()
spot = btc["close"]; mk = pd.read_csv("data/markets.csv").set_index("ticker")
order = mk["open_ts"].rank(method="first"); nmk=len(mk)
def tfee(p): return math.ceil(0.07*p*(1-p)*100)/100

# accumulate: key (half, variant, thr, side) -> list of pnl
acc = {}
def add(k,v): acc.setdefault(k,[]).append(v)

for s in sorted(glob.glob("data/trades_shards/shard_*.parquet")):
    df=pd.read_parquet(s)
    for tk,g in df.groupby("ticker"):
        if tk not in mk.index: continue
        m=mk.loc[tk]; o,c=int(m.open_ts),int(m.close_ts); res=1 if m.result=="yes" else 0
        half = 0 if order.get(tk,0) <= nmk/2 else 1
        g=g.sort_values("ts"); tts=g["ts"].values; typ=g["yes_price"].values; tside=g["taker_side"].values
        grid=np.arange(o,c); ai=np.searchsorted(tts,grid,side="right")-1
        ask=np.where(ai>=0,pd.Series(np.where(tside=="yes",typ,np.nan),index=tts).ffill().values[ai],np.nan)
        bid=np.where(ai>=0,pd.Series(np.where(tside=="no",typ,np.nan),index=tts).ffill().values[ai],np.nan)
        last=np.where(ai>=0,typ[ai],np.nan)
        u=spot.reindex(grid-1).ffill().values
        if np.isnan(u).any(): continue
        mom=np.r_[[np.nan]*3,np.log(u[3:]/u[:-3])]
        for i in range(len(grid)):
            ms=mom[i]
            if not np.isfinite(ms): continue
            am=abs(ms)
            for thr in (0.0007,0.0010,0.0015):
                if am<thr: continue
                if ms>0:  # long YES
                    for var,px in (("ask",ask[i]),("last",last[i])):
                        if np.isfinite(px) and 0<px<1:
                            add((half,var,thr,"YESup"),(res-px)-tfee(px))
                else:     # long NO
                    for var,px in (("ask",bid[i]),("last",last[i])):
                        if np.isfinite(px) and 0<px<1:
                            cost=1-px
                            add((half,var,thr,"NOdn"),((1-res)-cost)-tfee(cost))

def st(a):
    a=np.array(a)
    if len(a)==0: return "none"
    t=a.mean()/(a.std(ddof=1)/math.sqrt(len(a))) if a.std()>0 else float('nan')
    return f"n={len(a):>5} mean={100*a.mean():+5.2f}c win={(a>0).mean():.0%} t={t:+5.2f}"

for side in ("YESup","NOdn"):
    print(f"\n===== {side} =====")
    for var in ("ask","last"):
        for thr in (0.0007,0.0010,0.0015):
            h0=st(acc.get((0,var,thr,side),[])); h1=st(acc.get((1,var,thr,side),[]))
            print(f"  {var} thr={thr}:  H1[{h0}]   H2[{h1}]")
