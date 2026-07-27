from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# backtest.py lives at project root (not inside src/)
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest import backtest_journal  # noqa: E402


@dataclass
class BacktestResult:
    win_rate: float
    n_resolved: int
    n_wins: int
    by_asset: dict[str, dict] = field(default_factory=dict)


class BacktestWorker:
    def __init__(self, journal_path: str):
        self.journal_path = journal_path

    def run(self) -> BacktestResult:
        df = backtest_journal(self.journal_path)
        resolved = df[df["status_bt"] == "resolved"].copy()
        n_resolved = len(resolved)

        if n_resolved == 0:
            return BacktestResult(win_rate=0.0, n_resolved=0, n_wins=0)

        n_wins = int((resolved["pnl_cents"] > 0).sum())
        win_rate = n_wins / n_resolved

        resolved["asset"] = resolved["ticker"].str.extract(r"^(KX[A-Z0-9]+15M)")
        by_asset: dict[str, dict] = {}
        for asset, group in resolved.dropna(subset=["asset"]).groupby("asset"):
            wins = int((group["pnl_cents"] > 0).sum())
            by_asset[str(asset)] = {
                "count": len(group),
                "wins": wins,
                "win_rate": wins / len(group),
            }

        return BacktestResult(
            win_rate=win_rate,
            n_resolved=n_resolved,
            n_wins=n_wins,
            by_asset=by_asset,
        )
