from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.models import Market, Signal
from kalshi_bot.validation.guardrails import (
    GuardrailError,
    assert_on_main_branch,
    check_market_collision,
)


def _market(ticker: str) -> Market:
    return Market(
        ticker=ticker, title="test", category="Crypto",
        yes_bid=45, yes_ask=55, no_bid=45, no_ask=55,
        last_price=50, volume_24h=1000.0, liquidity_cents=5000, open_interest=100.0,
    )


def _signal(ticker: str) -> Signal:
    return Signal(
        ticker=ticker, title="test", side="yes",
        price=45, edge_cents=10, spread_cents=5, score=8.0, reason="test",
    )


# --- branch guardrail ---

def test_assert_on_main_branch_passes_on_main():
    mock_proc = MagicMock()
    mock_proc.stdout = "main\n"
    with patch("kalshi_bot.validation.guardrails.subprocess.run", return_value=mock_proc):
        assert_on_main_branch()  # must not raise


def test_assert_on_main_branch_raises_on_feature_branch():
    mock_proc = MagicMock()
    mock_proc.stdout = "feature/nba-strategy\n"
    with patch("kalshi_bot.validation.guardrails.subprocess.run", return_value=mock_proc):
        with pytest.raises(GuardrailError) as exc_info:
            assert_on_main_branch()
    assert "feature/nba-strategy" in str(exc_info.value)
    assert "BLOCKED" in str(exc_info.value)


def test_assert_on_main_branch_raises_on_detached_head():
    mock_proc = MagicMock()
    mock_proc.stdout = "HEAD\n"
    with patch("kalshi_bot.validation.guardrails.subprocess.run", return_value=mock_proc):
        with pytest.raises(GuardrailError):
            assert_on_main_branch()


# --- market collision ---

def test_no_collision_when_strategies_pick_different_markets():
    m_a = _market("KXBTC15M-26JAN011200-00")
    m_b = _market("KXBTC15M-26JAN011215-15")

    strat1 = MagicMock()
    strat2 = MagicMock()
    strat1.evaluate.side_effect = lambda m: _signal(m.ticker) if m.ticker == m_a.ticker else None
    strat2.evaluate.side_effect = lambda m: _signal(m.ticker) if m.ticker == m_b.ticker else None

    warnings = check_market_collision({"strat1": strat1, "strat2": strat2}, [m_a, m_b])
    assert warnings == []


def test_collision_detected_when_two_strategies_share_ticker():
    m = _market("KXBTC15M-26JAN011200-00")

    strat1 = MagicMock()
    strat2 = MagicMock()
    strat1.evaluate.return_value = _signal(m.ticker)
    strat2.evaluate.return_value = _signal(m.ticker)

    warnings = check_market_collision({"strat1": strat1, "strat2": strat2}, [m])
    assert len(warnings) == 1
    assert "KXBTC15M-26JAN011200-00" in warnings[0]
    assert "strat1" in warnings[0]
    assert "strat2" in warnings[0]


def test_no_collision_when_strategies_return_none():
    m = _market("KXBTC15M-26JAN011200-00")
    strat1 = MagicMock()
    strat2 = MagicMock()
    strat1.evaluate.return_value = None
    strat2.evaluate.return_value = None

    warnings = check_market_collision({"strat1": strat1, "strat2": strat2}, [m])
    assert warnings == []
