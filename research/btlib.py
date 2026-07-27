"""Backtest primitives: realistic fills, Kalshi fees, EV accounting, reporting."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd


def kalshi_fee(price: float) -> float:
    """Kalshi trading fee per contract, in dollars.

    Standard schedule: fee = ceil(0.07 * P * (1-P)) rounded up to the cent,
    where P is the execution price in dollars. ~1.75c max at P=0.5.
    Rounding per-contract is slightly conservative vs. per-order rounding.
    """
    raw = 0.07 * price * (1.0 - price)
    return math.ceil(raw * 100) / 100.0


def trade_pnl(side: str, yes_bid: float, yes_ask: float, result_yes: int,
              fee: bool = True) -> float:
    """Net P&L per contract for entering `side` and holding to settlement.

    YES filled at the ask; NO filled at (1 - yes_bid).
    """
    if side == "yes":
        cost = yes_ask
        gross = result_yes - cost
        f = kalshi_fee(cost) if fee else 0.0
        return gross - f
    else:  # no
        cost = 1.0 - yes_bid
        gross = (1 - result_yes) - cost
        f = kalshi_fee(cost) if fee else 0.0
        return gross - f


def summarize(pnls: np.ndarray, label: str = "") -> dict:
    pnls = np.asarray(pnls, dtype=float)
    n = len(pnls)
    if n == 0:
        return {"label": label, "n": 0}
    mean = pnls.mean()
    std = pnls.std(ddof=1) if n > 1 else 0.0
    t = mean / (std / math.sqrt(n)) if std > 0 else float("nan")
    return {
        "label": label,
        "n": n,
        "mean_c": 100 * mean,           # avg net cents per contract
        "total_$": pnls.sum(),
        "winrate": float((pnls > 0).mean()),
        "t_stat": t,
        "sharpe_trade": mean / std if std > 0 else float("nan"),
    }


def print_summ(d: dict):
    if d.get("n", 0) == 0:
        print(f"{d.get('label','')}: no trades")
        return
    print(f"{d['label']:<34} n={d['n']:>6}  mean={d['mean_c']:+6.2f}c  "
          f"win={d['winrate']:.1%}  t={d['t_stat']:+5.2f}  "
          f"tot=${d['total_$']:+8.1f}")


def add_time_split(panel: pd.DataFrame, frac=0.6):
    """Chronological train/test split by market open order (no leakage)."""
    order = panel.groupby("ticker")["end_ts"].min().sort_values()
    cutoff_idx = int(len(order) * frac)
    train_tickers = set(order.index[:cutoff_idx])
    panel = panel.copy()
    panel["split"] = np.where(panel["ticker"].isin(train_tickers), "train", "test")
    return panel
