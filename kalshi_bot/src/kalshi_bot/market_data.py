from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

from .client import KalshiHttpClient
from .models import Market


_TARGET_RE = re.compile(r'\$([\d,]+\.?\d*)\s*target', re.IGNORECASE)


CRYPTO_15M_SERIES = [
    "KXBTC15M",
    # KXETH15M excluded: markets are frequently illiquid (bid=0/ask=0) and
    # insufficient trade history to validate the vol_mult calibration.
]


class MarketDataService:
    def __init__(self, client: KalshiHttpClient, markets_per_event: int = 2):
        self.client = client
        self.markets_per_event = markets_per_event

    def _parse_dt(self, value: str | None):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None

    def _is_reasonably_tradeable(self, market: Market) -> bool:
        if market.yes_ask <= 0:
            return False
        if market.yes_bid < 0:
            return False
        if market.yes_bid >= market.yes_ask:
            return False

        if market.last_price <= 0 and market.yes_bid <= 0 and market.open_interest <= 0:
            return False

        return True

    def _seconds_until(self, dt_value: datetime | None) -> float | None:
        if dt_value is None:
            return None
        now = datetime.now(timezone.utc)
        return (dt_value - now).total_seconds()

    def _event_time_from_row(self, row: dict) -> datetime | None:
        dt_val = self._parse_dt(row.get("strike_date"))
        if dt_val:
            return dt_val

        for key in ("close_time", "expiration_time", "open_time"):
            dt_val = self._parse_dt(row.get(key))
            if dt_val:
                return dt_val

        return None

    def _pick_nearest_event(self, series: str) -> tuple[str, float] | None:
        try:
            page = self.client.get_events(
                series_ticker=series,
                limit=100,
                status="open",
                with_nested_markets=False,
            )
        except Exception as e:
            print(f"[market_data] {series} events_fetch_error={e}")
            return None

        rows = page.get("events", []) or page.get("data", []) or []

        if rows:
            print(f"[market_data] {series} first_event_row={rows[0]}")

        print(f"[market_data] {series} events_fetched={len(rows)}")

        candidates: list[tuple[float, str]] = []

        for row in rows:
            event_ticker = row.get("ticker") or row.get("event_ticker")
            if not event_ticker:
                continue

            event_time = self._event_time_from_row(row)
            secs_left = self._seconds_until(event_time)
            if secs_left is None:
                continue

            if secs_left <= 5:
                continue

            candidates.append((secs_left, event_ticker))

        if not candidates:
            print(f"[market_data] {series} no live events")
            return None

        candidates.sort(key=lambda x: x[0])
        secs_left, event_ticker = candidates[0]

        # Find the target price for this event from its title (e.g. "BTC 15 min · $71,650.00 target").
        # We store the title alongside the candidate so we can parse it after sorting.
        kalshi_target: float | None = None
        for row in rows:
            et = row.get("ticker") or row.get("event_ticker")
            if et == event_ticker:
                title = row.get("title", "")
                m = _TARGET_RE.search(title)
                if m:
                    try:
                        kalshi_target = float(m.group(1).replace(",", ""))
                    except ValueError:
                        pass
                break

        print(
            f"[market_data] {series} nearest_event={event_ticker} "
            f"secs_left={secs_left:.0f} kalshi_target={kalshi_target}"
        )
        return event_ticker, secs_left, kalshi_target

    def iter_open_markets(self, limit_per_page: int = 200) -> Iterable[Market]:
        kept = 0
        skipped = 0

        for series in CRYPTO_15M_SERIES:
            picked = self._pick_nearest_event(series)
            if not picked:
                continue

            event_ticker, event_secs_left, kalshi_target = picked

            try:
                page = self.client.get_markets(
                    limit=limit_per_page,
                    status="open",
                    event_ticker=event_ticker,
                    mve_filter="exclude",
                )
            except Exception as e:
                print(f"[market_data] {series} markets_fetch_error={e}")
                continue

            rows = page.get("markets", [])
            print(f"[market_data] {series} event_markets_fetched={len(rows)}")

            candidates: list[Market] = []

            for row in rows:
                market = Market.from_api(row)
                market.event_ticker = event_ticker
                market.secs_left = event_secs_left
                market.kalshi_strike = kalshi_target

                print(
                    f"[market_data] {series} {market.ticker} "
                    f"bid={market.yes_bid} ask={market.yes_ask} "
                    f"last={market.last_price} oi={market.open_interest} "
                    f"secs_left={event_secs_left:.0f}"
                )

                if not self._is_reasonably_tradeable(market):
                    skipped += 1
                    continue

                candidates.append(market)

            if not candidates:
                print(f"[market_data] {series} no tradeable markets in event {event_ticker}")
                continue

            for market in candidates[: self.markets_per_event]:
                kept += 1
                print(
                    f"[market_data] KEPT {market.ticker} "
                    f"bid={market.yes_bid} ask={market.yes_ask} "
                    f"last={market.last_price} oi={market.open_interest} "
                    f"secs_left={market.secs_left:.0f}"
                )
                yield market

        print(f"[market_data] done kept={kept} skipped={skipped}")

    def get_top_of_book(self, ticker: str) -> dict:
        return self.client.get_orderbook(ticker)