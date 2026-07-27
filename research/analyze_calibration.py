"""Is the Kalshi 15m BTC market miscalibrated? Where is the edge, if any?

We compute realized YES rate vs. the quoted price, then the net EV of
*buying at the executable price* (ask for YES, 1-bid for NO) including fees.
"""
import numpy as np
import pandas as pd
from btlib import kalshi_fee, trade_pnl

panel = pd.read_parquet("data/panel.parquet")
print(f"Panel: {len(panel)} rows, {panel['ticker'].nunique()} markets\n")

# ---------------------------------------------------------------------------
# 1) Calibration of the MID price: realized YES rate by mid bucket
# ---------------------------------------------------------------------------
print("="*78)
print("CALIBRATION: realized YES rate vs quoted mid (all minutes)")
print("="*78)
bins = [0,.05,.1,.2,.3,.4,.5,.6,.7,.8,.9,.95,1.0]
panel["mid_bin"] = pd.cut(panel["mid"], bins)
g = panel.groupby("mid_bin", observed=True).agg(
    n=("result_yes","size"), realized=("result_yes","mean"), mid=("mid","mean"))
g["edge_vs_mid"] = g["realized"] - g["mid"]
print(g.to_string(float_format=lambda x: f"{x:.4f}"))

# ---------------------------------------------------------------------------
# 2) Net EV of always buying YES (at ask) and always buying NO (at 1-bid),
#    bucketed by mid price. This is the *executable* edge after fees.
# ---------------------------------------------------------------------------
print("\n" + "="*78)
print("EXECUTABLE EV by price bucket (net of fees), cents/contract")
print("="*78)
panel["pnl_yes"] = panel.apply(lambda r: trade_pnl("yes", r.yes_bid, r.yes_ask, r.result_yes), axis=1)
panel["pnl_no"]  = panel.apply(lambda r: trade_pnl("no",  r.yes_bid, r.yes_ask, r.result_yes), axis=1)
g2 = panel.groupby("mid_bin", observed=True).agg(
    n=("result_yes","size"),
    yes_c=("pnl_yes", lambda x: 100*x.mean()),
    no_c =("pnl_no",  lambda x: 100*x.mean()),
)
print(g2.to_string(float_format=lambda x: f"{x:+.2f}"))

# ---------------------------------------------------------------------------
# 3) Same, split by time-to-expiry (does edge appear early vs late?)
# ---------------------------------------------------------------------------
print("\n" + "="*78)
print("EXECUTABLE EV by secs_left bucket (net of fees), cents/contract")
print("="*78)
panel["tbin"] = pd.cut(panel["secs_left"], [0,120,300,480,660,900])
g3 = panel.groupby("tbin", observed=True).agg(
    n=("result_yes","size"),
    yes_c=("pnl_yes", lambda x: 100*x.mean()),
    no_c =("pnl_no",  lambda x: 100*x.mean()),
)
print(g3.to_string(float_format=lambda x: f"{x:+.2f}"))

# ---------------------------------------------------------------------------
# 4) Distance-from-strike: does buying the favorite / longshot pay?
# ---------------------------------------------------------------------------
print("\n" + "="*78)
print("EV by distance-from-strike (dist_bps) buckets, net cents/contract")
print("="*78)
panel["dbin"] = pd.cut(panel["dist_bps"], [-300,-50,-20,-8,-3,3,8,20,50,300])
g4 = panel.groupby("dbin", observed=True).agg(
    n=("result_yes","size"),
    realized=("result_yes","mean"),
    mid=("mid","mean"),
    yes_c=("pnl_yes", lambda x: 100*x.mean()),
    no_c =("pnl_no",  lambda x: 100*x.mean()),
)
print(g4.to_string(float_format=lambda x: f"{x:+.3f}"))
