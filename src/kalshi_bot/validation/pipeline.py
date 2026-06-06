from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .backtest_agent import BacktestResult, BacktestWorker
from .guardrails import assert_on_main_branch, check_market_collision
from .report import ValidationReport
from .shadow_agent import ShadowResult, ShadowWorker
from .statistics_agent import StatisticsWorker

_WR_TOLERANCE = 0.05


class ValidationPipeline:
    def __init__(
        self,
        backtest_worker: BacktestWorker,
        shadow_worker: ShadowWorker,
        stats_worker: StatisticsWorker,
        report_path: str,
        env_path: str = ".env",
        check_branch: bool = True,
        collision_strategies: dict | None = None,
        collision_markets: list | None = None,
    ):
        self.backtest_worker = backtest_worker
        self.shadow_worker = shadow_worker
        self.stats_worker = stats_worker
        self.report_path = report_path
        self.env_path = env_path
        self.check_branch = check_branch
        self.collision_strategies = collision_strategies or {}
        self.collision_markets = collision_markets or []

    def run(self) -> ValidationReport:
        if self.check_branch:
            assert_on_main_branch()

        collision_warnings = check_market_collision(
            self.collision_strategies, self.collision_markets
        )
        for w in collision_warnings:
            logging.warning(w)

        backtest_result: BacktestResult | None = None
        shadow_result: ShadowResult | None = None

        with ThreadPoolExecutor(max_workers=2) as pool:
            bt_future = pool.submit(self.backtest_worker.run)
            sh_future = pool.submit(self.shadow_worker.run)
            for future in as_completed([bt_future, sh_future]):
                result = future.result()
                if future is bt_future:
                    backtest_result = result
                    logging.info("backtest: wr=%.3f n=%d", result.win_rate, result.n_resolved)
                else:
                    shadow_result = result
                    logging.info("shadow: wr=%.3f n=%d", result.win_rate, result.n_fills)

        stats_result = self.stats_worker.run(
            shadow_wins=shadow_result.n_wins,
            shadow_n=shadow_result.n_fills,
            backtest_wr=backtest_result.win_rate,
        )

        wr_delta = abs(shadow_result.win_rate - backtest_result.win_rate)
        wr_delta_passes = wr_delta <= _WR_TOLERANCE
        chi2_backtest_passes = stats_result.p_backtest > 0.05
        chi2_floor_passes = stats_result.p_floor < 0.05

        gates = [
            (
                shadow_result.n_fills >= self.shadow_worker.min_fills,
                f"shadow_n ({shadow_result.n_fills}) < min_fills ({self.shadow_worker.min_fills})",
            ),
            (
                wr_delta_passes,
                f"WR delta ({wr_delta:.3f}) > tolerance ({_WR_TOLERANCE})",
            ),
            (
                chi2_backtest_passes,
                f"shadow WR not consistent with backtest WR (p_backtest={stats_result.p_backtest:.4f} <= 0.05)",
            ),
            (
                chi2_floor_passes,
                f"shadow WR ({shadow_result.win_rate:.3f}) does not beat declared floor "
                f"({self.stats_worker.declared_floor_wr:.3f}) at p<0.05 "
                f"(p_floor={stats_result.p_floor:.4f})",
            ),
            (
                backtest_result.n_resolved >= 100,
                f"backtest_n ({backtest_result.n_resolved}) < 100",
            ),
        ]

        failed = [msg for passed, msg in gates if not passed]
        verdict = "PASS" if not failed else "FAIL"

        report = ValidationReport(
            backtest_wr=backtest_result.win_rate,
            backtest_n=backtest_result.n_resolved,
            shadow_wr=shadow_result.win_rate,
            shadow_n=shadow_result.n_fills,
            wr_delta=wr_delta,
            wr_delta_passes=wr_delta_passes,
            p_backtest=stats_result.p_backtest,
            chi2_backtest_passes=chi2_backtest_passes,
            declared_floor=self.stats_worker.declared_floor_wr,
            p_floor=stats_result.p_floor,
            chi2_floor_passes=chi2_floor_passes,
            overall_verdict=verdict,
            blocking_reason="; ".join(failed),
            market_collision_warnings=collision_warnings,
        )
        report.to_json(self.report_path)
        return report

    def maybe_promote(self, report: ValidationReport) -> bool:
        if report.overall_verdict != "PASS":
            return False
        env_path = Path(self.env_path)
        if env_path.exists():
            lines = env_path.read_text().splitlines(keepends=True)
            new_lines, found = [], False
            for line in lines:
                if line.startswith("DRY_RUN="):
                    new_lines.append("DRY_RUN=false\n")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append("DRY_RUN=false\n")
            env_path.write_text("".join(new_lines))
        else:
            env_path.write_text("DRY_RUN=false\n")
        return True
