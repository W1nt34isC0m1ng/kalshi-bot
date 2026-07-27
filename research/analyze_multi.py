"""Per-coin efficiency test for the less-liquid 15-min markets.
Calibration (realized vs mid) + executable EV (net of fees) by distance bucket
+ BS-model-vs-market edge. No look-ahead (spot lagged to last closed minute).
"""
import math
import numpy as np, pandas as pd

mkts = pd.read_csv("data/multi_markets.csv")
candles = pd.read_parquet("data/multi_candles.parquet")
cb = pd.read_parquet("data/multi_cb.parquet")
def tfee(p): return math.ceil(0.07*p*(1-p)*100)/100
_erf = np.vectorize(math.erf)
def ncdf(x): return 0.5*(1.0+_erf(np.asarray(x,float)/math.sqrt(2.0)))

m = mkts.set_index("ticker")
cb_by = {pr: g.drop_duplicates("ts").set_index("ts")["close"] for pr, g in cb.groupby("product")}
SP = {"KXETH15M":"ETH-USD","KXSOL15M":"SOL-USD","KXDOGE15M":"DOGE-USD","KXXRP15M":"XRP-USD"}

def spot_at(series, ts):
    s = cb_by.get(SP[series])
    if s is None: return np.nan
    ts = int(round(ts/60)*60) - 60   # last fully-closed minute
    return float(s.get(ts, np.nan))

rows = []
for c in candles.itertuples(index=False):
    if c.ticker not in m.index: continue
    mk = m.loc[c.ticker]
    secs_left = int(mk.close_ts) - int(c.end_ts)
    if secs_left < 0 or secs_left > 900 or int(c.end_ts) < int(mk.open_ts): continue
    if c.yes_bid is None or c.yes_ask is None: continue
    if not (0 <= c.yes_bid <= 1 and 0 <= c.yes_ask <= 1 and c.yes_ask >= c.yes_bid): continue
    sp = spot_at(mk.series, int(c.end_ts)); K = float(mk.floor_strike)
    if not (sp > 0 and K > 0): continue
    rows.append({"series": mk.series, "secs_left": secs_left, "yes_bid": c.yes_bid,
                 "yes_ask": c.yes_ask, "mid": (c.yes_bid+c.yes_ask)/2, "spread": c.yes_ask-c.yes_bid,
                 "dist_bps": 1e4*math.log(sp/K), "result_yes": 1 if mk.result=="yes" else 0,
                 "vol": float(mk.volume)})
p = pd.DataFrame(rows)
print(f"panel rows: {len(p)}")
print(p.groupby("series").agg(n=("mid","size"), med_spread=("spread","median"),
      med_vol=("vol","median"), base_yes=("result_yes","mean")).to_string(float_format=lambda x:f"{x:.4f}"))

p["pnl_yes"] = p.apply(lambda r:(r.result_yes-r.yes_ask)-tfee(r.yes_ask), axis=1)
p["pnl_no"]  = p.apply(lambda r:((1-r.result_yes)-(1-r.yes_bid))-tfee(1-r.yes_bid), axis=1)
p["dbin"] = pd.cut(p.dist_bps, [-500,-50,-20,-8,-3,3,8,20,50,500])

for s in SP:
    sub = p[p.series==s]
    if len(sub)==0: continue
    print(f"\n===== {s}  (n={len(sub)}) executable EV cents/contract, net of fees =====")
    g = sub.groupby("dbin", observed=True).agg(n=("mid","size"), realized=("result_yes","mean"),
        mid=("mid","mean"), yes_c=("pnl_yes", lambda x:100*x.mean()), no_c=("pnl_no", lambda x:100*x.mean()))
    print(g.to_string(float_format=lambda x:f"{x:+.2f}"))
    # best single executable rule per coin
    best = max([("buy YES @ask", sub.pnl_yes.mean()), ("buy NO @1-bid", sub.pnl_no.mean())], key=lambda t:t[1])
    print(f"  unconditional best: {best[0]} = {100*best[1]:+.2f}c/contract")
