from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Iterable

from .client import KalshiHttpClient
from .models import Market


_TARGET_RE = re.compile(r'\$([\d,]+\.?\d*)\s*target', re.IGNORECASE)


# Series to scan.  ETH is now included but guarded by a minimum open-interest
# threshold — if a market has no open interest it almost certainly has a dead
# book and no fills are possible.
CRYPTO_15M_SERIES = [
    "KXBTC15M",
    "KXETH15M",
]

# Minimum open interest (number of contracts) required to consider a market
# tradeable.  BTC markets are generally liquid; ETH needs a stricter gate.
_MIN_OPEN_INTEREST: dict[str, float] = {
    "KXBTC15M": 0.0,
    "KXETH15M": 50.0,
}


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
            logging.warning("market_data: %s events_fetch_error=%s", series, e)
            return None

        rows = page.get("events", []) or page.get("data", []) or []

        if rows:
            logging.debug("market_data: %s first_event_row=%s", series, rows[0])

        logging.debug("market_data: %s events_fetched=%d", series, len(rows))

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
            logging.debug("market_data: %s no live events", series)
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
                else:
                    logging.warning(
                        "market_data: strike regex did not match title=%r for event %s "
                        "(will fall back to Coinbase open-spot estimate)",
                        title,
                        event_ticker,
                    )
                break

        logging.debug(
            "market_data: %s nearest_event=%s secs_left=%.0f kalshi_target=%s",
            series, event_ticker, secs_left, kalshi_target,
        )
        return event_ticker, secs_left, kalshi_target

    def iter_open_markets(self, limit_per_page: int = 200) -> Iterable[Market]:
        kept = 0
        skipped = 0

        for series in CRYPTO_15M_SERIES:
            picked = self._pick_nearest_event(series)
            if not picked:
                continue

            kept_for_series = 0
            min_oi = _MIN_OPEN_INTEREST.get(series, 0.0)

            event_ticker, event_secs_left, kalshi_target = picked

            try:
                page = self.client.get_markets(
                    limit=limit_per_page,
                    status="open",
                    event_ticker=event_ticker,
                    mve_filter="exclude",
                )
            except Exception as e:
                logging.warning("market_data: %s markets_fetch_error=%s", series, e)
                continue

            rows = page.get("markets", [])
            logging.debug("market_data: %s event_markets_fetched=%d", series, len(rows))

            for row in rows:
                market = Market.from_api(row)
                market.event_ticker = event_ticker
                market.secs_left = event_secs_left
                market.kalshi_strike = kalshi_target

                logging.debug(
                    "market_data: %s %s bid=%s ask=%s last=%s oi=%s secs_left=%.0f",
                    series, market.ticker,
                    market.yes_bid, market.yes_ask, market.last_price,
                    market.open_interest, event_secs_left,
                )

                if market.open_interest < min_oi:
                    skipped += 1
                    logging.debug(
                        "market_data: SKIP %s open_interest=%.0f < min_oi=%.0f",
                        market.ticker, market.open_interest, min_oi,
                    )
                    continue

                if market.yes_bid <= 0 and market.yes_ask <= 0 and market.last_price <= 0:
                    skipped += 1
                    logging.debug(
                        "market_data: %s %s has empty REST quotes; "
                        "yielding anyway for WS bootstrap",
                        series, market.ticker,
                    )

                kept += 1
                kept_for_series += 1
                logging.debug(
                    "market_data: KEPT %s bid=%s ask=%s last=%s oi=%s secs_left=%.0f",
                    market.ticker, market.yes_bid, market.yes_ask,
                    market.last_price, market.open_interest, market.secs_left,
                )
                yield market
                if kept_for_series >= self.markets_per_event:
                    break

        logging.debug("market_data: done kept=%d skipped=%d", kept, skipped)

    def get_top_of_book(self, ticker: str) -> dict:
        return self.client.get_orderbook(ticker)
