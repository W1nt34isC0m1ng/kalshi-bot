from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from src.kalshi_bot.coinbase import asset_prefix_from_ticker
from src.kalshi_bot.config import Settings
from src.kalshi_bot.main import build_clients
from src.kalshi_bot.tickers import parse_market_ticker

ZERO = Decimal("0")
ONE = Decimal("1")
ONE_HUNDRED = Decimal("100")


def _decimal(value: object, default: str = "0") -> Decimal:
    raw = default if value in (None, "") else str(value)
    return Decimal(raw)


def _parse_iso8601(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    if "." not in normalized:
        return datetime.fromisoformat(normalized)

    main, frac_and_tz = normalized.split(".", 1)
    tz_sign = "+" if "+" in frac_and_tz else "-"
    frac, tz = frac_and_tz.split(tz_sign, 1)
    frac = (frac + "000000")[:6]
    return datetime.fromisoformat(f"{main}.{frac}{tz_sign}{tz}")


def _dollars_from_cents(raw_cents: object) -> Decimal:
    return _decimal(raw_cents) / ONE_HUNDRED


def _fmt_dollars(amount: Decimal) -> str:
    return f"${amount.quantize(Decimal('0.01'))}"


def _fmt_pct(value: Decimal) -> str:
    return f"{(value * ONE_HUNDRED).quantize(Decimal('0.01'))}%"


def _fetch_all_orders(client, *, status: str | None = None, limit: int = 100) -> list[dict]:
    orders: list[dict] = []
    seen_ids: set[str] = set()
    cursor: str | None = None

    while True:
        resp = client.get_orders(status=status, cursor=cursor, limit=limit)
        page_orders = resp.get("orders", []) or []
        for order in page_orders:
            order_id = str(order.get("order_id", "") or "")
            if order_id and order_id in seen_ids:
                continue
            if order_id:
                seen_ids.add(order_id)
            orders.append(order)

        next_cursor = str(resp.get("cursor", "") or "")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    return orders


@dataclass
class FilledOrder:
    order_id: str
    ticker: str
    side: str
    created_time: datetime
    expiry_time: datetime
    fill_count: Decimal
    fill_cost_dollars: Decimal
    fees_dollars: Decimal
    total_spent_dollars: Decimal
    yes_price_dollars: Decimal
    no_price_dollars: Decimal
    status: str


def _filled_orders_from_api(orders: Iterable[dict], *, series_prefix: str | None = None) -> list[FilledOrder]:
    filled: list[FilledOrder] = []
    for order in orders:
        ticker = str(order.get("ticker", "") or "")
        if not ticker:
            continue
        if series_prefix and not ticker.startswith(series_prefix):
            continue
        if asset_prefix_from_ticker(ticker) is None:
            continue

        fill_count = _decimal(order.get("fill_count_fp"))
        if fill_count <= 0:
            continue

        parsed = parse_market_ticker(ticker)
        if parsed is None:
            continue
        _, expiry_time, _ = parsed
        fill_cost_dollars = _decimal(order.get("maker_fill_cost_dollars")) + _decimal(
            order.get("taker_fill_cost_dollars")
        )
        fees_dollars = _decimal(order.get("maker_fees_dollars")) + _decimal(
            order.get("taker_fees_dollars")
        )

        filled.append(
            FilledOrder(
                order_id=str(order.get("order_id", "") or ""),
                ticker=ticker,
                side=str(order.get("side", "") or "").lower(),
                created_time=_parse_iso8601(str(order.get("created_time"))),
                expiry_time=expiry_time,
                fill_count=fill_count,
                fill_cost_dollars=fill_cost_dollars,
                fees_dollars=fees_dollars,
                total_spent_dollars=fill_cost_dollars + fees_dollars,
                yes_price_dollars=_decimal(order.get("yes_price_dollars")),
                no_price_dollars=_decimal(order.get("no_price_dollars")),
                status=str(order.get("status", "") or ""),
            )
        )

    return sorted(filled, key=lambda order: order.created_time)


def _market_result_for_ticker(client, ticker: str) -> str:
    response = client.get_market(ticker)
    market = response.get("market", response) if isinstance(response, dict) else {}
    return str(market.get("result", "") or "").lower()


def _report(
    starting_bankroll: Decimal | None,
    series_prefix: str | None,
    since: datetime | None,
) -> int:
    settings = Settings()
    _, private_client, _ = build_clients(settings)
    if private_client is None:
        raise RuntimeError("Authenticated Kalshi client unavailable; check API credentials.")

    balance = private_client.get_balance()
    positions = private_client.get_positions()
    raw_orders = _fetch_all_orders(private_client, status=None, limit=100)
    filled_orders = _filled_orders_from_api(raw_orders, series_prefix=series_prefix)
    if since is not None:
        filled_orders = [order for order in filled_orders if order.created_time >= since]

    now = datetime.now(timezone.utc)
    result_cache: dict[str, str] = {}
    resolved_orders: list[dict] = []
    unresolved_orders: list[FilledOrder] = []

    for order in filled_orders:
        market_result = result_cache.get(order.ticker)
        if market_result is None:
            market_result = _market_result_for_ticker(private_client, order.ticker)
            result_cache[order.ticker] = market_result

        if market_result not in {"yes", "no"}:
            unresolved_orders.append(order)
            continue

        won = market_result == order.side
        payout_dollars = order.fill_count if won else ZERO
        pnl_dollars = payout_dollars - order.total_spent_dollars
        resolved_orders.append(
            {
                "order": order,
                "won": won,
                "payout_dollars": payout_dollars,
                "pnl_dollars": pnl_dollars,
            }
        )

    current_balance = _dollars_from_cents(balance.get("balance", 0))
    portfolio_value = _dollars_from_cents(balance.get("portfolio_value", 0))
    total_equity = current_balance + portfolio_value

    resolved_pnl = sum((row["pnl_dollars"] for row in resolved_orders), ZERO)
    resolved_fees = sum((row["order"].fees_dollars for row in resolved_orders), ZERO)
    unresolved_cost = sum((order.fill_cost_dollars for order in unresolved_orders), ZERO)
    unresolved_fees = sum((order.fees_dollars for order in unresolved_orders), ZERO)
    total_fees = sum((order.fees_dollars for order in filled_orders), ZERO)
    total_fill_cost = sum((order.fill_cost_dollars for order in filled_orders), ZERO)
    total_contracts = sum((order.fill_count for order in filled_orders), ZERO)

    wins = sum(1 for row in resolved_orders if row["won"])
    losses = len(resolved_orders) - wins
    avg_pnl = (resolved_pnl / Decimal(len(resolved_orders))) if resolved_orders else ZERO
    avg_contracts = (total_contracts / Decimal(len(filled_orders))) if filled_orders else ZERO
    resolved_spent = sum((row["order"].total_spent_dollars for row in resolved_orders), ZERO)
    return_on_spent = (resolved_pnl / resolved_spent) if resolved_spent else ZERO
    mark_to_market = total_equity - starting_bankroll if starting_bankroll is not None else None
    inferred_open_pnl = portfolio_value - unresolved_cost - unresolved_fees

    print("ACTUAL_ACCOUNT_REPORT")
    print(f"series_prefix={series_prefix or 'ALL_SUPPORTED_CRYPTO'}")
    print(f"generated_utc={datetime.now(timezone.utc).isoformat()}")
    print()
    print("ACCOUNT")
    print(f"cash_balance={_fmt_dollars(current_balance)}")
    print(f"portfolio_value={_fmt_dollars(portfolio_value)}")
    print(f"total_equity={_fmt_dollars(total_equity)}")
    if starting_bankroll is not None:
        print(f"starting_bankroll={_fmt_dollars(starting_bankroll)}")
        print(f"equity_delta={_fmt_dollars(mark_to_market)}")
    print()
    print("FILLS")
    print(f"filled_orders={len(filled_orders)}")
    print(f"resolved_orders={len(resolved_orders)}")
    print(f"unresolved_orders={len(unresolved_orders)}")
    print(f"filled_contracts={total_contracts}")
    print(f"avg_contracts_per_filled_order={avg_contracts.quantize(Decimal('0.01')) if filled_orders else Decimal('0.00')}")
    print(f"status_counts={dict(Counter(order.status for order in filled_orders))}")
    if filled_orders:
        print(f"first_fill_utc={filled_orders[0].created_time.isoformat()}")
        print(f"last_fill_utc={filled_orders[-1].created_time.isoformat()}")
    print()
    print("RESOLVED_PNL")
    print(f"wins={wins}")
    print(f"losses={losses}")
    print(f"win_rate={_fmt_pct(Decimal(wins) / Decimal(len(resolved_orders))) if resolved_orders else '0.00%'}")
    print(f"resolved_fill_cost={_fmt_dollars(sum((row['order'].fill_cost_dollars for row in resolved_orders), ZERO))}")
    print(f"resolved_fees={_fmt_dollars(resolved_fees)}")
    print(f"resolved_total_spent={_fmt_dollars(resolved_spent)}")
    print(f"resolved_pnl={_fmt_dollars(resolved_pnl)}")
    print(f"avg_pnl_per_resolved_order={_fmt_dollars(avg_pnl)}")
    print(f"return_on_resolved_spent={_fmt_pct(return_on_spent)}")
    print()
    print("OPEN_EXPOSURE")
    print(f"open_fill_cost={_fmt_dollars(unresolved_cost)}")
    print(f"open_fees={_fmt_dollars(unresolved_fees)}")
    print(f"inferred_open_mark_to_market={_fmt_dollars(inferred_open_pnl)}")
    print(f"market_positions={len(positions.get('market_positions', []) or [])}")
    print(f"event_positions={len(positions.get('event_positions', []) or [])}")
    print()
    print("TOTALS")
    print(f"all_fill_cost={_fmt_dollars(total_fill_cost)}")
    print(f"all_fees={_fmt_dollars(total_fees)}")

    recent = resolved_orders[-5:]
    if recent:
        print()
        print("RECENT_RESOLVED")
        for row in recent:
            order = row["order"]
            print(
                f"{order.created_time.isoformat()} {order.ticker} {order.side} "
                f"count={order.fill_count} spent={_fmt_dollars(order.total_spent_dollars)} "
                f"pnl={_fmt_dollars(row['pnl_dollars'])}"
            )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report actual Kalshi account P&L from filled orders and current account equity."
    )
    parser.add_argument(
        "--starting-bankroll",
        type=Decimal,
        default=None,
        help="Starting bankroll in dollars, e.g. 245.00",
    )
    parser.add_argument(
        "--series-prefix",
        default="KXBTC15M",
        help="Optional series prefix filter, e.g. KXBTC15M. Use ALL for every supported crypto series.",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Only include orders created at or after this ISO8601 timestamp.",
    )
    args = parser.parse_args()

    series_prefix = None if args.series_prefix.upper() == "ALL" else args.series_prefix.upper()
    since = _parse_iso8601(args.since) if args.since else None
    return _report(args.starting_bankroll, series_prefix, since)


if __name__ == "__main__":
    raise SystemExit(main())
