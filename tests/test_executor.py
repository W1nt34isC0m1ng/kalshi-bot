"""Unit tests for ExecutionEngine (executor.py).

All external dependencies (Kalshi API, risk state file) are mocked so the
tests run offline without credentials.
"""
from __future__ import annotations

import json
import sys
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kalshi_bot.executor import ExecutionEngine
from kalshi_bot.models import Signal


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _make_settings(tmp_path: Path, cooldown_seconds: int = 60, dry_run: bool = True) -> MagicMock:
    settings = MagicMock()
    settings.cooldown_seconds = cooldown_seconds
    settings.dry_run = dry_run
    settings.risk_state_path = str(tmp_path / "risk_state.json")
    settings.order_ttl_seconds = 10
    settings.auto_sizing = False
    settings.order_count = 2
    settings.max_position_per_market = 5
    return settings


def _make_signal(ticker: str = "KXBTC15M-26APR111700-00", side: str = "yes") -> Signal:
    return Signal(
        ticker=ticker,
        title="Test Market",
        side=side,
        price=45,
        edge_cents=8,
        spread_cents=4,
        score=7.5,
        reason="test",
        ev_cents=8.0,
        ev_roi=0.18,
        yes_bid=43,
        yes_ask=47,
        position_size=2,
        strategy="crypto_prob",
    )


def _make_engine(tmp_path: Path, **kwargs) -> ExecutionEngine:
    settings = _make_settings(tmp_path, **kwargs)
    client = MagicMock()
    risk = MagicMock()
    risk.approve.return_value = (True, "approved")
    return ExecutionEngine(client, settings, risk)


# ------------------------------------------------------------------ #
# intent_from_signal                                                   #
# ------------------------------------------------------------------ #

class TestIntentFromSignal:
    def test_yes_side_uses_yes_bid(self, tmp_path):
        engine = _make_engine(tmp_path)
        sig = _make_signal(side="yes")
        intent = engine.intent_from_signal(sig)
        assert intent.ticker == sig.ticker
        assert intent.side == "yes"
        assert intent.price == sig.yes_bid  # maker price = yes_bid for YES buys
        assert intent.count == 2  # position_size > 1 → uses position_size

    def test_no_side_uses_yes_ask(self, tmp_path):
        engine = _make_engine(tmp_path)
        sig = _make_signal(side="no")
        intent = engine.intent_from_signal(sig)
        assert intent.side == "no"
        assert intent.price == sig.yes_ask  # maker price = yes_ask for NO buys

    def test_action_is_buy(self, tmp_path):
        engine = _make_engine(tmp_path)
        intent = engine.intent_from_signal(_make_signal())
        assert intent.action == "buy"

    def test_count_capped_at_max_position(self, tmp_path):
        settings = _make_settings(tmp_path)
        settings.max_position_per_market = 1  # very tight cap
        engine = ExecutionEngine(MagicMock(), settings, MagicMock())
        sig = _make_signal()
        sig.position_size = 99  # try to request more than cap
        intent = engine.intent_from_signal(sig)
        assert intent.count == 1


# ------------------------------------------------------------------ #
# Cooldown logic                                                       #
# ------------------------------------------------------------------ #

class TestCooldown:
    def test_no_cooldown_initially(self, tmp_path):
        engine = _make_engine(tmp_path)
        active, remaining = engine._cooldown_active(_make_signal())
        assert active is False
        assert remaining == 0

    def test_cooldown_active_after_mark_sent(self, tmp_path):
        engine = _make_engine(tmp_path, cooldown_seconds=60)
        sig = _make_signal()
        engine._mark_sent(sig)
        active, remaining = engine._cooldown_active(sig)
        assert active is True
        assert remaining > 55

    def test_cooldown_expires(self, tmp_path):
        engine = _make_engine(tmp_path, cooldown_seconds=1)
        sig = _make_signal()
        engine._mark_sent(sig)
        time.sleep(1.1)
        active, remaining = engine._cooldown_active(sig)
        assert active is False
        assert remaining == 0

    def test_different_side_has_separate_cooldown(self, tmp_path):
        engine = _make_engine(tmp_path)
        sig_yes = _make_signal(side="yes")
        sig_no = _make_signal(side="no")
        engine._mark_sent(sig_yes)
        active_no, _ = engine._cooldown_active(sig_no)
        assert active_no is False

    def test_cooldown_persisted_to_disk(self, tmp_path):
        engine = _make_engine(tmp_path, cooldown_seconds=120)
        sig = _make_signal()
        engine._mark_sent(sig)
        cooldown_file = Path(engine.settings.risk_state_path).parent / "cooldown_state.json"
        assert cooldown_file.exists()
        data = json.loads(cooldown_file.read_text())
        key = f"{sig.ticker}|{sig.side}"
        assert key in data

    def test_cooldown_loaded_on_new_instance(self, tmp_path):
        """A new ExecutionEngine instance should load persisted cooldowns."""
        engine1 = _make_engine(tmp_path, cooldown_seconds=120)
        sig = _make_signal()
        engine1._mark_sent(sig)

        engine2 = _make_engine(tmp_path, cooldown_seconds=120)
        active, remaining = engine2._cooldown_active(sig)
        assert active is True

    def test_expired_cooldown_not_loaded(self, tmp_path):
        """Cooldowns that already expired are not restored from disk."""
        engine1 = _make_engine(tmp_path, cooldown_seconds=1)
        sig = _make_signal()
        engine1._mark_sent(sig)

        time.sleep(1.1)

        engine2 = _make_engine(tmp_path, cooldown_seconds=1)
        active, _ = engine2._cooldown_active(sig)
        assert active is False


# ------------------------------------------------------------------ #
# maybe_send                                                           #
# ------------------------------------------------------------------ #

class TestMaybeSend:
    def test_dry_run_returns_dry_run_status(self, tmp_path):
        engine = _make_engine(tmp_path, dry_run=True)
        result = engine.maybe_send(_make_signal())
        assert result["status"] == "dry_run"

    def test_blocked_by_cooldown(self, tmp_path):
        engine = _make_engine(tmp_path, cooldown_seconds=120)
        sig = _make_signal()
        engine._mark_sent(sig)
        result = engine.maybe_send(sig)
        assert result["status"] == "cooldown"

    def test_blocked_by_risk(self, tmp_path):
        settings = _make_settings(tmp_path, dry_run=True)
        client = MagicMock()
        risk = MagicMock()
        risk.approve.return_value = (False, "portfolio notional cap breached")
        engine = ExecutionEngine(client, settings, risk)
        result = engine.maybe_send(_make_signal())
        assert result["status"] == "blocked"
        assert "notional" in result["reason"]
