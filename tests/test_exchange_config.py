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
