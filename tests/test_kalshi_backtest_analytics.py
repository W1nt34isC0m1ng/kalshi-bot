from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kalshi_bot"))

from backtest import calculate_trade_pnl, regime_labels_for_trade


def test_calculate_trade_pnl_subtracts_estimated_kalshi_fees_for_yes_win():
    pnl = calculate_trade_pnl(
        side="yes",
        yes_price_cents=50,
        yes_outcome=1,
        contract_count=2,
    )

    assert pnl["won"] is True
    assert pnl["pnl_cents_gross"] == 100
    assert pnl["fee_cents"] == 4
    assert pnl["pnl_cents_net"] == 96


def test_calculate_trade_pnl_subtracts_estimated_kalshi_fees_for_no_loss():
    pnl = calculate_trade_pnl(
        side="no",
        yes_price_cents=70,
        yes_outcome=1,
        contract_count=3,
    )

    assert pnl["won"] is False
    assert pnl["pnl_cents_gross"] == -90
    assert pnl["fee_cents"] == 5
    assert pnl["pnl_cents_net"] == -95


def test_regime_labels_parse_reason_and_timestamp_fields():
    labels = regime_labels_for_trade(
        {
            "ts_utc": "2026-01-01T15:00:00+00:00",
            "ticker": "KXBTC15M-26JAN011515-15",
            "side": "yes",
            "spread_cents": 7,
            "edge_cents": 8,
            "reason": (
                "asset=KXBTC15M, spot=43000.00, strike=42950.00, "
                "secs_left=612, sigma=0.80, d2=1.23, fair=66.2, "
                "market=58.0, ev=8.2, ev_roi=0.1414, momentum_boost=0.04"
            ),
        }
    )

    assert labels == {
        "asset": "KXBTC15M",
        "hour_et": 10,
        "hour_et_band": "cash_hours",
        "secs_left_bucket": "900-600",
        "d2_bucket": "1.0-1.5",
        "spread_bucket": "5-10",
        "side": "yes",
        "edge_bucket": "6-10",
    }


def test_regime_labels_leave_unparseable_reason_fields_empty():
    labels = regime_labels_for_trade(
        {
            "ts_utc": "not-a-timestamp",
            "ticker": "KXETH15M-26JAN011515-15",
            "side": "no",
            "spread_cents": "",
            "edge_cents": None,
            "reason": "cooldown_active_42s",
        }
    )

    assert labels["asset"] == "KXETH15M"
    assert labels["side"] == "no"
    assert labels["hour_et"] is None
    assert labels["hour_et_band"] is None
    assert labels["secs_left_bucket"] is None
    assert labels["d2_bucket"] is None
    assert labels["spread_bucket"] is None
    assert labels["edge_bucket"] is None
