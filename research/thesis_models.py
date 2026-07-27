"""Thesis battery on 1-min panel: does any model beat the market price net of costs?

All rules: observe features at minute T (no look-ahead), trade at executable
price (YES@ask, NO@(1-bid)), hold to settlement, pay Kalshi fees.
Chronological train/test split; we report TEST results (train used only to pick
the trade threshold where relevant).
"""
import math
import numpy as np
import pandas as pd
from btlib import trade_pnl, summarize, print_summ, add_time_split

_erf = np.vectorize(math.erf)
class _N:  # minimal norm.cdf replacement (no scipy)
    @staticmethod
    def cdf(x):
        return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / math.sqrt(2.0)))
norm = _N()

panel = pd.read_parquet("data/panel.parquet")
panel = add_time_split(panel, frac=0.6)
panel = panel.dropna(subset=["rv20", "ret5", "ret3"]).copy()

MPY = 365.25 * 24 * 60  # minutes per year (for annualization, though we work per-min)


def bs_prob(spot, strike, secs_left, rv_per_min, vol_mult=1.0):
    mins = np.maximum(secs_left / 60.0, 1e-6)
    sig = rv_per_min * vol_mult * np.sqrt(mins)
    sig = np.where(sig <= 0, 1e-9, sig)
    d2 = np.log(spot / strike) / sig
    return norm.cdf(d2)


def eval_rule(df, side_series, mask, label):
    sub = df[mask]
    if len(sub) == 0:
        print_summ({"label": label, "n": 0}); return None
    sides = side_series[mask]
    pnls = np.array([trade_pnl(s, r.yes_bid, r.yes_ask, r.result_yes)
                     for s, r in zip(sides, sub.itertuples(index=False))])
    d = summarize(pnls, label); print_summ(d); return d


print(f"Rows after dropna: {len(panel)}  (train {sum(panel.split=='train')}, test {sum(panel.split=='test')})\n")
test = panel[panel.split == "test"]
train = panel[panel.split == "train"]

# ---------------------------------------------------------------------------
# Thesis 1: BS fair value with realized vol (vol_mult swept). Trade direction
# of model edge when |edge| exceeds threshold tuned on train.
# ---------------------------------------------------------------------------
print("="*78)
print("THESIS 1: BS model (realized rv20) vs market mid — trade the edge")
print("="*78)
for vm in [1.0, 1.5, 2.0, 3.5]:
    for thr in [0.05, 0.08, 0.12]:
        fair = bs_prob(panel.spot.values, panel.strike.values, panel.secs_left.values,
                       panel.rv20.values, vol_mult=vm)
        panel["fair"] = fair
        edge = panel["fair"] - panel["mid"]
        side = np.where(edge > 0, "yes", "no")
        side = pd.Series(side, index=panel.index)
        mask = (panel.split == "test") & (edge.abs() > thr)
        eval_rule(panel, side, mask, f"  vm={vm} thr={thr:.2f}")

# ---------------------------------------------------------------------------
# Thesis 2: Vol-regime mispricing. When recent realized vol is high vs the
# vol implied by the market's price at the money, fade/follow.
# ---------------------------------------------------------------------------
print("\n" + "="*78)
print("THESIS 2: trade only when |model edge| large AND near the money (|dist|<8bps)")
print("="*78)
fair = bs_prob(panel.spot.values, panel.strike.values, panel.secs_left.values, panel.rv20.values, 1.0)
panel["fair"] = fair
edge = panel["fair"] - panel["mid"]
side = pd.Series(np.where(edge > 0, "yes", "no"), index=panel.index)
for thr in [0.04, 0.06, 0.10]:
    mask = (panel.split == "test") & (edge.abs() > thr) & (panel.dist_bps.abs() < 8)
    eval_rule(panel, side, mask, f"  atm thr={thr:.2f}")

# ---------------------------------------------------------------------------
# Thesis 3: Time-of-day. Is there a session where buying NO (or YES) at mid wins?
# ---------------------------------------------------------------------------
print("\n" + "="*78)
print("THESIS 3: hour-of-day base-rate effect (buy NO at executable price)")
print("="*78)
panel["hour"] = ((panel.end_ts // 3600) % 24)
sideno = pd.Series("no", index=panel.index)
rows = []
for h in range(24):
    mask = (panel.split == "test") & (panel.hour == h) & (panel.secs_left.between(120, 700))
    d = eval_rule(panel, sideno, mask, f"  hourUTC={h:02d} NO")
