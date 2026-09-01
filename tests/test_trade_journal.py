from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kalshi_bot" / "src"))

from kalshi_bot.journal import TradeJournal


def test_journal_structured_signal_fields_are_appended_to_existing_schema():
    original_fields = [
        "ts_utc",
        "strategy",
        "ticker",
        "side",
        "price",
        "edge_cents",
        "ev_cents",
        "ev_roi",
        "spread_cents",
        "score",
        "reason",
        "status",
        "status_reason",
        "order_id",
        "filled_count",
        "requested_count",
        "premium_cents_per_contract",
        "notional_cents",
    ]
    structured_fields = [
        "spot",
        "strike",
        "sigma",
        "d2",
        "secs_left",
        "fair",
        "raw_edge",
        "momentum_boost",
    ]

    assert TradeJournal._FIELDNAMES[: len(original_fields)] == original_fields
    assert TradeJournal._FIELDNAMES[len(original_fields) :] == structured_fields
