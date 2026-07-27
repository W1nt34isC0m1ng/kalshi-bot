#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

# Ensure src/ is importable when running from project root
_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kalshi_bot.client import KalshiHttpClient
from kalshi_bot.config import Settings
from kalshi_bot.crypto_strategy import CryptoProbStrategy
from kalshi_bot.market_data import MarketDataService
from kalshi_bot.mean_reversion_strategy import MeanReversionStrategy
from kalshi_bot.validation.backtest_agent import BacktestWorker
from kalshi_bot.validation.guardrails import GuardrailError
from kalshi_bot.validation.pipeline import ValidationPipeline
from kalshi_bot.validation.shadow_agent import ShadowWorker
from kalshi_bot.validation.statistics_agent import StatisticsWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _build_strategy(name: str, settings: Settings, client: KalshiHttpClient):
    if name == "mean_reversion":
        return MeanReversionStrategy()
    if name == "crypto_prob":
        return CryptoProbStrategy(
            client,
            min_edge_cents=settings.crypto_min_edge_cents,
            max_spread_cents=settings.crypto_max_spread_cents,
            min_score=settings.crypto_min_score,
            momentum_scaling_factor=settings.momentum_scaling_factor,
        )
    raise ValueError(f"Unknown strategy: {name!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a trading strategy before going live."
    )
    parser.add_argument(
        "--strategy", required=True,
        choices=["mean_reversion", "crypto_prob"],
        help="Strategy to validate",
    )
    parser.add_argument(
        "--declared-floor", type=float, default=0.55,
        help="Declared win rate floor the strategy must beat (default: 0.55)",
    )
    parser.add_argument(
        "--min-fills", type=int, default=100,
        help="Minimum shadow fills required before gates run (default: 100)",
    )
    parser.add_argument(
        "--journal", default="logs/trade_journal.csv",
        help="Path to trade journal for backtest agent",
    )
    parser.add_argument(
        "--shadow-journal", default="logs/shadow_journal.csv",
        help="Path for shadow agent output journal",
    )
    parser.add_argument(
        "--report", default="logs/validation_report.json",
        help="Path to write validation report JSON",
    )
    parser.add_argument(
        "--env", default=".env",
        help="Path to .env file (patched on promotion)",
    )
    parser.add_argument(
        "--no-branch-check", action="store_true",
        help="Skip git branch guardrail (testing only)",
    )
    args = parser.parse_args()

    settings = Settings()
    public_client = KalshiHttpClient(settings.base_url)
    market_data = MarketDataService(public_client, markets_per_event=settings.markets_per_event)
    strategy = _build_strategy(args.strategy, settings, public_client)

    pipeline = ValidationPipeline(
        backtest_worker=BacktestWorker(journal_path=args.journal),
        shadow_worker=ShadowWorker(
            strategy=strategy,
            market_data=market_data,
            shadow_journal_path=args.shadow_journal,
            min_fills=args.min_fills,
        ),
        stats_worker=StatisticsWorker(declared_floor_wr=args.declared_floor),
        report_path=args.report,
        env_path=args.env,
        check_branch=not args.no_branch_check,
    )

    try:
        report = pipeline.run()
    except GuardrailError as exc:
        print(str(exc))
        return 1

    print("\n" + "=" * 60)
    print(f"  Backtest  WR: {report.backtest_wr:.1%}  (n={report.backtest_n})")
    print(f"  Shadow    WR: {report.shadow_wr:.1%}  (n={report.shadow_n})")
    print(f"  WR delta:     {report.wr_delta:.1%}  [≤5% required]")
    print(f"  p_backtest:   {report.p_backtest:.4f}  [>0.05 required]")
    print(f"  p_floor:      {report.p_floor:.4f}  [<0.05 required]")
    print(f"  Floor:        {report.declared_floor:.1%}")
    for w in report.market_collision_warnings:
        print(f"  {w}")
    print("=" * 60)

    report_path_abs = str(Path(args.report).resolve())
    pytest_result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_validation_gates.py", "-v", "--tb=short"],
        env={**os.environ, "VALIDATION_REPORT_PATH": report_path_abs},
        cwd=str(Path(__file__).parent),
    )

    if pytest_result.returncode == 0:
        pipeline.maybe_promote(report)
        print("\n✓  PROMOTED — DRY_RUN=false written to .env")
        return 0
    else:
        print(f"\n✗  BLOCKED — {report.blocking_reason}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
