from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.models import Market, Signal
from kalshi_bot.validation.shadow_agent import PendingFill, ShadowResult, ShadowWorker


def _market(ticker: str = "KXBTC15M-26JAN011200-00") -> Market:
    return Market(
        ticker=ticker, title="BTC 15m", category="Crypto",
        yes_bid=45, yes_ask=55, no_bid=45, no_ask=55,
        last_price=50, volume_24h=1000.0, liquidity_cents=5000, open_interest=100.0,
    )


def _signal(ticker: str = "KXBTC15M-26JAN011200-00") -> Signal:
    return Signal(
        ticker=ticker, title="BTC 15m", side="yes",
        price=45, edge_cents=10, spread_cents=5, score=8.0, reason="test",
    )


def _make_worker(tmp_path, strategy, market_data, min_fills=1):
    return ShadowWorker(
        strategy=strategy,
        market_data=market_data,
        shadow_journal_path=str(tmp_path / "shadow.csv"),
        min_fills=min_fills,
        poll_interval_seconds=0,
    )


def test_shadow_worker_exits_at_min_fills(tmp_path):
    market = _market()
    strategy = MagicMock()
    strategy.evaluate.return_value = _signal()
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market]

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)

    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(95000.0, 96000.0, 1)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        result = worker.run()

    assert isinstance(result, ShadowResult)
    assert result.n_fills == 1
    assert result.n_wins == 1
    assert result.win_rate == pytest.approx(1.0)


def test_shadow_worker_counts_losses(tmp_path):
    market = _market()
    strategy = MagicMock()
    strategy.evaluate.return_value = _signal()
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market]

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)

    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(96000.0, 95000.0, 0)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        result = worker.run()

    assert result.n_fills == 1
    assert result.n_wins == 0
    assert result.win_rate == pytest.approx(0.0)


def test_shadow_worker_skips_none_signals(tmp_path):
    market_a = _market("KXBTC15M-26JAN011200-00")
    market_b = _market("KXBTC15M-26JAN011215-15")

    strategy = MagicMock()
    strategy.evaluate.side_effect = lambda m: (
        _signal(m.ticker) if m.ticker == market_a.ticker else None
    )
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market_a, market_b]

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)
    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(95000.0, 96000.0, 1)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        result = worker.run()

    # Only market_a generated a signal → only 1 fill
    assert result.n_fills == 1


def test_shadow_worker_does_not_double_count_same_ticker(tmp_path):
    market = _market()
    strategy = MagicMock()
    strategy.evaluate.return_value = _signal()
    # Return same market twice in first call, then empty to stop accumulation
    # (won't actually get a second call because min_fills=1 exits after first resolve)
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market, market]  # duplicate

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)
    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(95000.0, 96000.0, 1)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        result = worker.run()

    assert result.n_fills == 1  # not 2


def test_shadow_worker_writes_journal_header(tmp_path):
    market = _market()
    strategy = MagicMock()
    strategy.evaluate.return_value = _signal()
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market]

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)
    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(95000.0, 96000.0, 1)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        worker.run()

    journal_path = tmp_path / "shadow.csv"
    assert journal_path.exists()
    with journal_path.open() as f:
        reader = csv.DictReader(f)
        assert "ticker" in (reader.fieldnames or [])
        assert "status" in (reader.fieldnames or [])
