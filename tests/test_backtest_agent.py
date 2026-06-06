from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.validation.backtest_agent import BacktestResult, BacktestWorker


def _resolved_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal DataFrame as backtest_journal() would return."""
    defaults = {"status_bt": "resolved", "pnl_cents": 55, "ticker": "KXBTC15M-26JAN011200-00"}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def test_backtest_worker_win_rate(tmp_path):
    journal = tmp_path / "journal.csv"
    journal.write_text("ts_utc,ticker\n")  # content doesn't matter; backtest_journal is mocked

    df = _resolved_df([
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011200-00"},   # win
        {"pnl_cents": -45, "ticker": "KXBTC15M-26JAN011215-15"},  # loss
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011230-30"},   # win
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011245-45"},   # win
    ])

    with patch("kalshi_bot.validation.backtest_agent.backtest_journal", return_value=df):
        result = BacktestWorker(journal_path=str(journal)).run()

    assert isinstance(result, BacktestResult)
    assert result.n_resolved == 4
    assert result.n_wins == 3
    assert result.win_rate == pytest.approx(0.75)


def test_backtest_worker_empty_resolved(tmp_path):
    journal = tmp_path / "journal.csv"
    journal.write_text("ts_utc,ticker\n")

    empty_df = pd.DataFrame(columns=["status_bt", "pnl_cents", "ticker"])

    with patch("kalshi_bot.validation.backtest_agent.backtest_journal", return_value=empty_df):
        result = BacktestWorker(journal_path=str(journal)).run()

    assert result.n_resolved == 0
    assert result.win_rate == 0.0


def test_backtest_worker_skips_unresolved(tmp_path):
    journal = tmp_path / "journal.csv"
    journal.write_text("ts_utc,ticker\n")

    df = pd.DataFrame([
        {"status_bt": "resolved", "pnl_cents": 55, "ticker": "KXBTC15M-26JAN011200-00"},
        {"status_bt": "skipped_future", "pnl_cents": None, "ticker": "KXBTC15M-26JAN021200-00"},
        {"status_bt": "error", "pnl_cents": None, "ticker": "KXBTC15M-26JAN031200-00"},
    ])

    with patch("kalshi_bot.validation.backtest_agent.backtest_journal", return_value=df):
        result = BacktestWorker(journal_path=str(journal)).run()

    assert result.n_resolved == 1


def test_backtest_worker_by_asset_grouping(tmp_path):
    journal = tmp_path / "journal.csv"
    journal.write_text("ts_utc,ticker\n")

    df = _resolved_df([
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011200-00"},
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011215-15"},
        {"pnl_cents": -45, "ticker": "KXETH15M-26JAN011200-00"},
    ])

    with patch("kalshi_bot.validation.backtest_agent.backtest_journal", return_value=df):
        result = BacktestWorker(journal_path=str(journal)).run()

    assert "KXBTC15M" in result.by_asset
    assert result.by_asset["KXBTC15M"]["count"] == 2
    assert result.by_asset["KXBTC15M"]["wins"] == 2
    assert result.by_asset["KXETH15M"]["wins"] == 0
