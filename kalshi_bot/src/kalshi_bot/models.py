from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _to_int_cents(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    s = str(value)
    if "." in s:
        return round(float(s) * 100)
    return int(s)


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


@dataclass
class Market:
    ticker: str
    title: str
    category: str
    status: str
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int
    last_price: int
    volume: float
    volume_24h: float
    close_time: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, row: dict[str, Any]) -> "Market":
        return cls(
            ticker=row["ticker"],
            title=row.get("title", row["ticker"]),
            category=row.get("category", "Unknown"),
            status=row.get("status", "unknown"),
            yes_bid=_to_int_cents(row.get("yes_bid") or row.get("yes_bid_dollars", 0)),
            yes_ask=_to_int_cents(row.get("yes_ask") or row.get("yes_ask_dollars", 0)),
            no_bid=_to_int_cents(row.get("no_bid") or row.get("no_bid_dollars", 0)),
            no_ask=_to_int_cents(row.get("no_ask") or row.get("no_ask_dollars", 0)),
            last_price=_to_int_cents(row.get("last_price") or row.get("last_price_dollars", 0)),
            volume=_to_float(row.get("volume") or row.get("volume_fp", 0)),
            volume_24h=_to_float(row.get("volume_24h") or row.get("volume_24h_fp", 0)),
            close_time=row.get("close_time"),
            raw=row,
        )


@dataclass
class Signal:
    ticker: str
    title: str
    side: str
    price: int
    edge_cents: int
    spread_cents: int
    score: float
    reason: str


@dataclass
class OrderIntent:
    ticker: str
    side: str
    action: str
    count: int
    price: int
    client_order_id: str
    expiration_ts: int
    reason: str
