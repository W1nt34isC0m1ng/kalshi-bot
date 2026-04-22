from __future__ import annotations

from dataclasses import dataclass


def dollars_str_to_cents(value) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return 0


def fp_str_to_float(value) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class Market:
    ticker: str
    title: str
    category: str
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int
    last_price: int
    volume_24h: float
    liquidity_cents: int
    open_interest: float
    event_ticker: str | None = None
    secs_left: float | None = None
    # Authoritative Kalshi target price in dollars, parsed from event/market title.
    # Use this as the strike instead of re-deriving from Coinbase candles.
    kalshi_strike: float | None = None
    series_ticker: str | None = None

    @classmethod
    def from_api(cls, row: dict) -> "Market":
        yes_bid = dollars_str_to_cents(row.get("yes_bid_dollars"))
        yes_ask = dollars_str_to_cents(row.get("yes_ask_dollars"))
        no_bid = dollars_str_to_cents(row.get("no_bid_dollars"))
        no_ask = dollars_str_to_cents(row.get("no_ask_dollars"))
        last_price = dollars_str_to_cents(row.get("last_price_dollars"))
        volume_24h = fp_str_to_float(row.get("volume_24h_fp"))
        liquidity_cents = dollars_str_to_cents(row.get("liquidity_dollars"))
        open_interest = fp_str_to_float(row.get("open_interest_fp"))

        category = (
            row.get("category")
            or row.get("series_ticker")
            or row.get("market_type")
            or "Unknown"
        )

        return cls(
            ticker=row.get("ticker", "UNKNOWN"),
            title=row.get("title", "Unknown Market"),
            category=category,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            last_price=last_price,
            volume_24h=volume_24h,
            liquidity_cents=liquidity_cents,
            open_interest=open_interest,
            event_ticker=row.get("event_ticker"),
            secs_left=None,
            series_ticker=row.get("series_ticker"),
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
    ev_cents: float = 0.0
    ev_roi: float = 0.0
    momentum_boost: float = 0.0
    yes_bid: int | None = None
    yes_ask: int | None = None
    position_size: int = 1
    strategy: str = "generic"


@dataclass
class OrderIntent:
    ticker: str
    side: str
    action: str = "buy"
    count: int = 1
    price: int = 0
    order_type: str = "limit"
    client_order_id: str | None = None
    expiration_ts: int | None = None
    reason: str | None = None
