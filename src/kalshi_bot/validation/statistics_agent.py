from __future__ import annotations

from dataclasses import dataclass

from scipy import stats


@dataclass
class StatisticsResult:
    chi2_vs_backtest: float
    p_backtest: float          # two-sided; PASS when > 0.05
    statistic_vs_floor: float  # binomtest proportion (shadow_wins / shadow_n)
    p_floor: float             # one-sided; PASS when < 0.05


class StatisticsWorker:
    def __init__(self, declared_floor_wr: float):
        self.declared_floor_wr = declared_floor_wr

    def run(
        self,
        shadow_wins: int,
        shadow_n: int,
        backtest_wr: float,
    ) -> StatisticsResult:
        shadow_losses = shadow_n - shadow_wins

        # Test 1: two-sided chi-square goodness-of-fit (shadow vs backtest distribution)
        f_exp_wins = backtest_wr * shadow_n
        f_exp_losses = (1.0 - backtest_wr) * shadow_n

        if f_exp_wins < 1e-10 or f_exp_losses < 1e-10:
            # Degenerate expected distribution — treat as maximally inconsistent
            chi2_stat = float("inf")
            p_backtest = 0.0
        else:
            chi2_result = stats.chisquare(
                f_obs=[shadow_wins, shadow_losses],
                f_exp=[f_exp_wins, f_exp_losses],
            )
            chi2_stat = float(chi2_result.statistic)
            p_backtest = float(chi2_result.pvalue)

        # Test 2: one-sided binomial test (shadow beats declared floor)
        # H₀: shadow_wr ≤ floor  H₁: shadow_wr > floor
        # p < 0.05 → evidence shadow beats floor → PASS
        binom_result = stats.binomtest(
            shadow_wins, shadow_n, self.declared_floor_wr, alternative="greater"
        )

        return StatisticsResult(
            chi2_vs_backtest=chi2_stat,
            p_backtest=p_backtest,
            statistic_vs_floor=float(binom_result.statistic),
            p_floor=float(binom_result.pvalue),
        )
