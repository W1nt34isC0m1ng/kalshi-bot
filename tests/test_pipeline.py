from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.validation.backtest_agent import BacktestResult
from kalshi_bot.validation.guardrails import GuardrailError
from kalshi_bot.validation.pipeline import ValidationPipeline
from kalshi_bot.validation.report import ValidationReport
from kalshi_bot.validation.shadow_agent import ShadowResult
from kalshi_bot.validation.statistics_agent import StatisticsResult


def _make_pipeline(tmp_path, backtest_wr=0.61, shadow_wr=0.60,
                   shadow_n=105, backtest_n=142,
                   p_backtest=0.80, p_floor=0.02, declared_floor=0.55):
    bt_worker = MagicMock()
    bt_worker.run.return_value = BacktestResult(
        win_rate=backtest_wr, n_resolved=backtest_n,
        n_wins=int(backtest_wr * backtest_n),
    )
    sh_worker = MagicMock()
    sh_worker.run.return_value = ShadowResult(
        win_rate=shadow_wr, n_fills=shadow_n,
        n_wins=int(shadow_wr * shadow_n),
    )
    sh_worker.min_fills = 100

    stats_worker = MagicMock()
    stats_worker.declared_floor_wr = declared_floor
    stats_worker.run.return_value = StatisticsResult(
        chi2_vs_backtest=0.5, p_backtest=p_backtest,
        statistic_vs_floor=shadow_wr, p_floor=p_floor,
    )

    return ValidationPipeline(
        backtest_worker=bt_worker,
        shadow_worker=sh_worker,
        stats_worker=stats_worker,
        report_path=str(tmp_path / "report.json"),
        env_path=str(tmp_path / ".env"),
        check_branch=False,
    )


def test_pipeline_produces_pass_verdict(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    report = pipeline.run()
    assert report.overall_verdict == "PASS"
    assert report.blocking_reason == ""


def test_pipeline_fails_when_wr_delta_exceeds_tolerance(tmp_path):
    pipeline = _make_pipeline(tmp_path, backtest_wr=0.70, shadow_wr=0.60)
    report = pipeline.run()
    assert report.overall_verdict == "FAIL"
    assert "WR delta" in report.blocking_reason


def test_pipeline_fails_when_shadow_n_below_min(tmp_path):
    pipeline = _make_pipeline(tmp_path, shadow_n=50)
    report = pipeline.run()
    assert report.overall_verdict == "FAIL"
    assert "50" in report.blocking_reason


def test_pipeline_fails_when_chi2_backtest_fails(tmp_path):
    pipeline = _make_pipeline(tmp_path, p_backtest=0.01)
    report = pipeline.run()
    assert report.overall_verdict == "FAIL"
    assert "p_backtest" in report.blocking_reason or "consistent" in report.blocking_reason


def test_pipeline_fails_when_chi2_floor_fails(tmp_path):
    pipeline = _make_pipeline(tmp_path, p_floor=0.20)
    report = pipeline.run()
    assert report.overall_verdict == "FAIL"
    assert "floor" in report.blocking_reason


def test_pipeline_writes_report_json(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    pipeline.run()
    report_path = tmp_path / "report.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert data["overall_verdict"] == "PASS"


def test_maybe_promote_patches_dry_run(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("KALSHI_ENV=demo\nDRY_RUN=true\n")

    pipeline = _make_pipeline(tmp_path)
    pipeline.env_path = str(env_path)
    report = pipeline.run()

    promoted = pipeline.maybe_promote(report)
    assert promoted is True
    assert "DRY_RUN=false" in env_path.read_text()
    assert "DRY_RUN=true" not in env_path.read_text()


def test_maybe_promote_does_not_touch_env_on_fail(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("DRY_RUN=true\n")

    pipeline = _make_pipeline(tmp_path, shadow_n=50)  # forces FAIL
    pipeline.env_path = str(env_path)
    report = pipeline.run()

    promoted = pipeline.maybe_promote(report)
    assert promoted is False
    assert "DRY_RUN=true" in env_path.read_text()


def test_pipeline_raises_guardrail_error_on_wrong_branch(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    pipeline.check_branch = True

    mock_proc = MagicMock()
    mock_proc.stdout = "feature/test\n"
    with patch("kalshi_bot.validation.guardrails.subprocess.run", return_value=mock_proc):
        with pytest.raises(GuardrailError):
            pipeline.run()
