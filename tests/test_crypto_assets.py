from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kalshi_bot" / "src"))

from kalshi_bot.assets import (
    ASSET_CONFIG,
    BLOCKED_CRYPTO_15M_SERIES,
    asset_prefix_from_ticker,
    crypto_15m_series_from_env,
    parse_crypto_15m_series_csv,
)


def test_crypto_15m_series_defaults_to_btc_only(monkeypatch):
    monkeypatch.delenv("CRYPTO_15M_SERIES", raising=False)

    assert crypto_15m_series_from_env() == ["KXBTC15M"]


def test_crypto_15m_series_csv_normalizes_dedupes_and_keeps_verified_series():
    assert parse_crypto_15m_series_csv(
        " kxbtc15m, KXETH15M, kxsol15m, KXBTC15M ,, kxdoge15m "
    ) == ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXDOGE15M"]


def test_crypto_15m_series_csv_skips_blocked_and_unknown_series(caplog):
    requested = "KXSILVER15M,KXWTI15M,KXMADEUP15M,KXXRP15M"

    assert parse_crypto_15m_series_csv(requested) == ["KXXRP15M"]
    assert "KXSILVER15M" in caplog.text
    assert "KXWTI15M" in caplog.text
    assert "KXMADEUP15M" in caplog.text


def test_asset_prefix_from_ticker_supports_all_live_configured_assets():
    for series in ASSET_CONFIG:
        ticker = f"{series}-26JAN011200-00"

        assert asset_prefix_from_ticker(ticker) == series


def test_gold_is_supported_only_with_coinbase_paxg_product_and_blocked_metals_are_documented():
    assert ASSET_CONFIG["KXGOLD15M"]["product"] == "PAXG-USD"
    assert "KXSILVER15M" in BLOCKED_CRYPTO_15M_SERIES
    assert "KXWTI15M" in BLOCKED_CRYPTO_15M_SERIES
