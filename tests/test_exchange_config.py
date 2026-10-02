from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kalshi_bot" / "src"))


def test_exchange_defaults_to_kalshi_and_dry_run_true(monkeypatch):
    monkeypatch.delenv("EXCHANGE", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)

    import kalshi_bot.config as config

    importlib.reload(config)
    settings = config.Settings()

    assert settings.exchange == "kalshi"
    assert settings.dry_run is True


def test_exchange_accepts_polymarket(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "polymarket")

    import kalshi_bot.config as config

    importlib.reload(config)
    settings = config.Settings()

    assert settings.exchange == "polymarket"


def test_polymarket_live_mode_fails_closed_without_reconciliation_opt_in(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "polymarket")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xabc")
    monkeypatch.delenv("POLYMARKET_ALLOW_LIVE_WITHOUT_POSITION_RECONCILIATION", raising=False)

    import pytest
    import kalshi_bot.config as config
    import kalshi_bot.main as main

    importlib.reload(config)
    importlib.reload(main)
    settings = config.Settings()

    with pytest.raises(RuntimeError, match="position reconciliation"):
        main.build_clients(settings)
