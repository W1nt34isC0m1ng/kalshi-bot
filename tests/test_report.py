from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.validation.report import ValidationReport


def _pass_report() -> ValidationReport:
    return ValidationReport(
        backtest_wr=0.61,
        backtest_n=142,
        shadow_wr=0.60,
        shadow_n=105,
        wr_delta=0.01,
        wr_delta_passes=True,
        p_backtest=0.80,
        chi2_backtest_passes=True,
        declared_floor=0.55,
        p_floor=0.02,
        chi2_floor_passes=True,
        overall_verdict="PASS",
        blocking_reason="",
        market_collision_warnings=[],
    )


def test_round_trip_json(tmp_path):
    report = _pass_report()
    path = str(tmp_path / "report.json")
    report.to_json(path)
    loaded = ValidationReport.from_json(path)
    assert loaded == report


def test_to_json_creates_parent_dirs(tmp_path):
    report = _pass_report()
    nested = str(tmp_path / "a" / "b" / "report.json")
    report.to_json(nested)
    assert Path(nested).exists()


def test_json_contains_expected_keys(tmp_path):
    report = _pass_report()
    path = str(tmp_path / "report.json")
    report.to_json(path)
    data = json.loads(Path(path).read_text())
    for key in [
        "backtest_wr", "backtest_n", "shadow_wr", "shadow_n",
        "wr_delta", "wr_delta_passes", "p_backtest", "chi2_backtest_passes",
        "declared_floor", "p_floor", "chi2_floor_passes",
        "overall_verdict", "blocking_reason", "market_collision_warnings",
    ]:
        assert key in data, f"Missing key: {key}"
