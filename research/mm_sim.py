"""Passive market-making simulation against real trade flow.

For each market, on a 1s grid we post a two-sided quote around an underlying-
derived fair value (no look-ahead: spot[t-1]):
    bid = fair - h - skew ;  ask = fair + h - skew
where `skew = K * mom` shifts quotes WITH the underlying move (defensive against
the lead-lag: when BTC ticks up we raise both quotes so we don't sell cheap).

Fills: each actual trade in (t, t+1] that crosses our quote fills us (price
priority approximation, 1 contract/trade), subject to an inventory cap. Maker fee
per fill. Residual inventory settles at the market result.

Reports net P&L per market and per contract traded, swept over (h, K, inv cap).
"""
import glob, math, sys
import numpy as np, pandas as pd

btc = pd.read_parquet("data/btc_1s_wide.parquet").drop_duplicates("ts").set_index("ts").sort_index()
spot = btc["close"]
rvsec = np.log(btc["close"]).diff().rolling(60).std()
mk = pd.read_csv("data/markets.csv").set_index("ticker")
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
def maker_fee(p): return 0.0175 * p * (1 - p)
_erf = np.vectorize(math.erf)
def ncdf(x): return 0.5 * (1.0 + _erf(np.asarray(x, float) / math.sqrt(2.0)))


def sim_market(tk, g, h, K, invcap):
    m = mk.loc[tk]; o, c = int(m.open_ts), int(m.close_ts); res = 1 if m.result == "yes" else 0
    strike = float(m.floor_strike)
    if strike <= 0: return None
    grid = np.arange(o, c)
    s = spot.reindex(grid - 1).ffill().values            # no look-ahead
    v = rvsec.reindex(grid - 1).ffill().values
    if np.isnan(s).any() or np.isnan(v).any(): return None
    secs_left = (c - grid).astype(float)
    sig = np.maximum(v, 1e-6) * np.sqrt(np.maximum(secs_left, 1e-6))
    fair = ncdf(np.log(s / strike) / np.maximum(sig, 1e-9))
    mom = np.r_[[0.0]*3, np.log(s[3:] / s[:-3])]
    skew = K * mom
    bid = np.clip(fair - h - skew, 0.01, 0.99)
    ask = np.clip(fair + h - skew, 0.01, 0.99)
    sec_of = {t: i for i, t in enumerate(grid)}

    g = g.sort_values("ts")
    tsec = np.floor(g["ts"].values).astype(np.int64)
    tp = g["yes_price"].values
    tside = g["taker_side"].values

    inv = 0; cash = 0.0; ncontracts = 0
    for k in range(len(g)):
        i = sec_of.get(int(tsec[k]))
        if i is None: continue
        if tside[k] == "yes" and tp[k] >= ask[i] - 1e-9:    # taker lifts -> we SELL yes at our ask
            if inv > -invcap:
                cash += ask[i]; inv -= 1; cash -= maker_fee(ask[i]); ncontracts += 1
        elif tside[k] == "no" and tp[k] <= bid[i] + 1e-9:   # taker hits -> we BUY yes at our bid
            if inv < invcap:
                cash -= bid[i]; inv += 1; cash -= maker_fee(bid[i]); ncontracts += 1
    cash += inv * res   # settle residual inventory
    return cash, ncontracts


def run(h, K, invcap, limit):
    tot_pnl = 0.0; tot_contracts = 0; nmkts = 0
    for sfile in sorted(glob.glob("data/trades_shards/shard_*.parquet")):
        df = pd.read_parquet(sfile)
        for tk, g in df.groupby("ticker"):
            if tk not in mk.index: continue
            r = sim_market(tk, g, h, K, invcap)
            if r is None: continue
            pnl, nc = r
            tot_pnl += pnl; tot_contracts += nc; nmkts += 1
            if nmkts >= limit: break
        if nmkts >= limit: break
    perc = 100 * tot_pnl / tot_contracts if tot_contracts else float("nan")
    print(f"  h={h*100:.1f}c K={K:>4} invcap={invcap:>3}: "
          f"mkts={nmkts} contracts={tot_contracts} pnl=${tot_pnl:+8.1f} "
          f"per_contract={perc:+.2f}c per_mkt=${tot_pnl/max(nmkts,1):+.2f}")
    return tot_pnl


if __name__ == "__main__":
    print(f"MM sim over up to {LIMIT} markets")
    print("Baseline (no skew):")
    for h in (0.005, 0.01, 0.02):
        run(h, 0, 20, LIMIT)
    print("With defensive skew K (quotes shift with underlying):")
    for K in (5, 15, 40):
        run(0.01, K, 20, LIMIT)
    print("Tighter inventory caps (h=1c, K=15):")
    for invcap in (5, 10, 50):
        run(0.01, 15, invcap, LIMIT)
