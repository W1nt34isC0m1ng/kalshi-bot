from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.validation.statistics_agent import StatisticsResult, StatisticsWorker


def test_consistent_shadow_passes_chi2_backtest():
    # shadow WR matches backtest WR → p should be high (fail to reject H₀)
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=60, shadow_n=100, backtest_wr=0.60)

    assert isinstance(result, StatisticsResult)
    assert result.p_backtest > 0.05


def test_divergent_shadow_fails_chi2_backtest():
    # shadow WR=30% vs backtest WR=70% → clearly inconsistent
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=30, shadow_n=100, backtest_wr=0.70)

    assert result.p_backtest < 0.05


def test_strong_shadow_passes_floor():
    # shadow WR=70% clearly beats floor of 0.50
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=70, shadow_n=100, backtest_wr=0.60)

    assert result.p_floor < 0.05


def test_weak_shadow_fails_floor():
    # shadow WR=45% does not beat floor of 0.55
    worker = StatisticsWorker(declared_floor_wr=0.55)
    result = worker.run(shadow_wins=45, shadow_n=100, backtest_wr=0.60)

    assert result.p_floor >= 0.05


def test_degenerate_backtest_wr_zero_does_not_crash():
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=0, shadow_n=100, backtest_wr=0.0)
    # degenerate backtest → treated as maximally inconsistent
    assert result.p_backtest == 0.0
    assert math.isinf(result.chi2_vs_backtest)


def test_degenerate_backtest_wr_one_does_not_crash():
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=100, shadow_n=100, backtest_wr=1.0)
    assert result.p_backtest == 0.0


def test_result_fields_are_floats():
    worker = StatisticsWorker(declared_floor_wr=0.55)
    result = worker.run(shadow_wins=60, shadow_n=100, backtest_wr=0.60)
    assert isinstance(result.chi2_vs_backtest, float)
    assert isinstance(result.p_backtest, float)
    assert isinstance(result.statistic_vs_floor, float)
    assert isinstance(result.p_floor, float)
