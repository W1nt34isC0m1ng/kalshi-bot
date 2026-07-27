"""Final test: extreme-jump TAKER strategies on the full 15.9-day window.
When the underlying moves hard, the Kalshi quote is unambiguously stale -> take it.
 (1) taker scalp: enter at touch, exit taker H s later.
 (2) taker hold-to-settlement: enter at touch, hold to expiry (one fee).
"""
import glob, math
import numpy as np, pandas as pd

btc = pd.read_parquet("data/btc_1s_wide.parquet").drop_duplicates("ts").set_index("ts").sort_index()
spot = btc["close"]
mk = pd.read_csv("data/markets.csv").set_index("ticker")
def tfee(p): return math.ceil(0.07*p*(1-p)*100)/100
H = 5
THRS = [0.0010, 0.0015, 0.0020, 0.0030]
scalp = {th: [] for th in THRS}
settle = {th: [] for th in THRS}

for s in sorted(glob.glob("data/trades_shards/shard_*.parquet")):
    df = pd.read_parquet(s)
    for tk, g in df.groupby("ticker"):
        if tk not in mk.index: continue
        m = mk.loc[tk]; o, c = int(m.open_ts), int(m.close_ts); res = 1 if m.result == "yes" else 0
        g = g.sort_values("ts")
        tts = g["ts"].values; typ = g["yes_price"].values; tside = g["taker_side"].values
        grid = np.arange(o, c)
        ai = np.searchsorted(tts, grid, side="right") - 1
        ask = np.where(ai >= 0, pd.Series(np.where(tside=="yes",typ,np.nan),index=tts).ffill().values[ai], np.nan)
        bid = np.where(ai >= 0, pd.Series(np.where(tside=="no", typ,np.nan),index=tts).ffill().values[ai], np.nan)
        # NO LOOK-AHEAD: spot known at second t = close of bucket [t-1,t) = spot[t-1]
        u = spot.reindex(grid - 1).ffill().values
        if np.isnan(u).any(): continue
        mom = np.r_[[np.nan]*3, np.log(u[3:]/u[:-3])]
        for i, t in enumerate(grid):
            ms = mom[i]
            if not np.isfinite(ms): continue
            am = abs(ms)
            if c - t < H + 2: continue
            for th in THRS:
                if am < th: continue
                if ms > 0:  # buy YES taker at ask
                    e = ask[i]
                    if not np.isfinite(e) or e <= 0 or e >= 1: continue
                    ei = i + H
                    xb = bid[ei] if ei < len(grid) else np.nan  # exit: sell to bid
                    if np.isfinite(xb):
                        scalp[th].append((xb - e) - tfee(e) - tfee(xb))
                    settle[th].append((res - e) - tfee(e))
                else:       # buy NO taker at (1-bid); short YES
                    b = bid[i]
                    if not np.isfinite(b) or b <= 0 or b >= 1: continue
                    cost = 1 - b
                    ei = i + H
                    xa = ask[ei] if ei < len(grid) else np.nan  # exit: buy back at ask
                    if np.isfinite(xa):
                        scalp[th].append((b - xa) - tfee(cost) - tfee(xa))
                    settle[th].append(((1-res) - cost) - tfee(cost))

def rep(d, name):
    print(f"\n{name}:")
    for th in THRS:
        a = np.array(d[th])
        if len(a) == 0: print(f"  thr={th}: none"); continue
        t = a.mean()/(a.std(ddof=1)/math.sqrt(len(a))) if a.std()>0 else float('nan')
        print(f"  thr={th}  n={len(a):>5}  mean={100*a.mean():+5.2f}c  win={(a>0).mean():.1%}  t={t:+5.2f}  tot=${a.sum():+7.1f}")
rep(scalp, "Extreme-jump TAKER scalp (exit +5s)")
rep(settle, "Extreme-jump TAKER hold-to-settlement (one fee)")
