from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _load_report() -> dict:
    report_path = os.environ.get("VALIDATION_REPORT_PATH", "logs/validation_report.json")
    path = Path(report_path)
    if not path.exists():
        pytest.skip(f"No validation report at {report_path}")
    return json.loads(path.read_text())


# ── Gate 1 ──────────────────────────────────────────────────────────────────

def test_min_shadow_fills():
    r = _load_report()
    assert r["shadow_n"] >= 100, (
        f"Insufficient shadow fills: {r['shadow_n']} < 100"
    )


# ── Gate 2 ──────────────────────────────────────────────────────────────────

def test_wr_delta_within_tolerance():
    r = _load_report()
    delta = abs(r["shadow_wr"] - r["backtest_wr"])
    assert delta <= 0.05, (
        f"WR delta {delta:.3f} exceeds 5% tolerance "
        f"(shadow={r['shadow_wr']:.3f}, backtest={r['backtest_wr']:.3f})"
    )


# ── Gate 3 ──────────────────────────────────────────────────────────────────

def test_chi2_shadow_vs_backtest():
    r = _load_report()
    assert r["p_backtest"] > 0.05, (
        f"Shadow WR is statistically inconsistent with backtest WR "
        f"(p={r['p_backtest']:.4f} ≤ 0.05)"
    )


# ── Gate 4 ──────────────────────────────────────────────────────────────────

def test_chi2_shadow_vs_floor():
    r = _load_report()
    assert r["p_floor"] < 0.05, (
        f"Shadow WR does not beat declared floor {r['declared_floor']} "
        f"(p={r['p_floor']:.4f} ≥ 0.05)"
    )


# ── Gate 5 ──────────────────────────────────────────────────────────────────

def test_backtest_min_resolved():
    r = _load_report()
    assert r["backtest_n"] >= 100, (
        f"Backtest resolved fewer than 100 trades: {r['backtest_n']}"
    )
