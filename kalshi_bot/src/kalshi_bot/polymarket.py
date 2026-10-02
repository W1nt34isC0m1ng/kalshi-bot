from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from .models import Market


POLYMARKET_ASSET_PREFIX = {
    "btc": "PMBTC15M",
    "eth": "PMETH15M",
    "sol": "PMSOL15M",
    "xrp": "PMXRP15M",
}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _price_to_cents(value: Any) -> int:
    return int(round(_float(value) * 100))


def _best_bid(book: dict[str, Any]) -> int:
    bids = _json_list(book.get("bids"))
    return max((_price_to_cents(level.get("price")) for level in bids), default=0)


def _best_ask(book: dict[str, Any]) -> int:
    asks = _json_list(book.get("asks"))
    return min((_price_to_cents(level.get("price")) for level in asks), default=0)


def _last_price(book: dict[str, Any], fallback_price: Any = None) -> int:
    price = _price_to_cents(book.get("last_trade_price"))
    if price > 0:
        return price
    return _price_to_cents(fallback_price)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class PolymarketHttpClient:
    """Small wrapper over Polymarket Gamma and CLOB APIs.

    Public discovery and order-book reads use requests directly. Live order
    placement is delegated to py-clob-client-v2 so signing/auth stays in the
    official client instead of being reimplemented here.
    """

    def __init__(
        self,
        *,
        gamma_base_url: str = "https://gamma-api.polymarket.com",
        clob_base_url: str = "https://clob.polymarket.com",
        timeout: int = 15,
        chain_id: int = 137,
        private_key: str = "",
        api_key: str = "",
        api_secret: str = "",
        api_passphrase: str = "",
        signature_type: int | None = None,
        funder: str = "",
    ):
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.clob_base_url = clob_base_url.rstrip("/")
        self.timeout = timeout
        self.chain_id = chain_id
        self.private_key = private_key
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.signature_type = signature_type
        self.funder = funder or None
        self.session = requests.Session()
        self._markets_by_ticker: dict[str, dict[str, Any]] = {}
        self._clob_client = None

    def _get(self, base_url: str, path: str, params: dict[str, Any] | None = None):
        response = self.session.get(
            f"{base_url}{path}",
            params=params,
            timeout=self.timeout,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    def get_event_by_slug(self, slug: str) -> dict[str, Any]:
        return self._get(self.gamma_base_url, f"/events/slug/{slug}")

    def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        return self._get(self.gamma_base_url, f"/markets/slug/{slug}")

    def get_orderbook(self, token_id: str) -> dict[str, Any]:
        return self._get(self.clob_base_url, "/book", params={"token_id": token_id})

    def cache_market(self, ticker: str, metadata: dict[str, Any]) -> None:
        self._markets_by_ticker[ticker] = metadata

    def _authenticated_clob_client(self):
        if self._clob_client is not None:
            return self._clob_client
        if not self.private_key:
            raise RuntimeError("POLYMARKET_PRIVATE_KEY is required for live order placement")

        from py_clob_client_v2 import ApiCreds, ClobClient

        creds = None
        if self.api_key and self.api_secret and self.api_passphrase:
            creds = ApiCreds(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_passphrase=self.api_passphrase,
            )

        client = ClobClient(
            host=self.clob_base_url,
            chain_id=self.chain_id,
            key=self.private_key,
            creds=creds,
            signature_type=self.signature_type,
            funder=self.funder,
        )
        if creds is None:
            client.set_api_creds(client.create_or_derive_api_key())
        self._clob_client = client
        return client

    def create_order(
        self,
        *,
        ticker: str,
        side: str,
        action: str,
        count: int,
        price: int,
        expiration_ts: int | None = None,
        post_only: bool = True,
    ) -> dict[str, Any]:
        if action.lower() != "buy":
            raise ValueError("Polymarket adapter currently supports buy orders only")

        metadata = self._markets_by_ticker.get(ticker)
        if not metadata:
            raise RuntimeError(f"No cached Polymarket token metadata for {ticker}")

        normalized_side = side.lower()
        token_id = metadata["yes_token_id"] if normalized_side == "yes" else metadata["no_token_id"]
        token_price_cents = price if normalized_side == "yes" else 100 - price
        token_price = max(0.01, min(0.99, token_price_cents / 100.0))

        from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client_v2.order_builder.constants import BUY

        clob = self._authenticated_clob_client()
        response = clob.create_and_post_order(
            OrderArgs(
                token_id=token_id,
                price=token_price,
                size=count,
                side=BUY,
                expiration=expiration_ts or 0,
            ),
            options=PartialCreateOrderOptions(
                tick_size=str(metadata.get("tick_size") or "0.01"),
                neg_risk=bool(metadata.get("neg_risk") or False),
            ),
            order_type=OrderType.GTD if expiration_ts else OrderType.GTC,
            post_only=post_only,
        )
        return {
            "order": {
                "order_id": response.get("orderID") or response.get("order_id") or "",
                "status": response.get("status", ""),
                "raw": response,
            }
        }

    def get_positions(self) -> dict[str, list]:
        # Position reconciliation is left conservative until account endpoint
        # mapping is needed for live Polymarket trading.
        return {"positions": []}


class PolymarketMarketDataService:
    def __init__(
        self,
        client: PolymarketHttpClient,
        *,
        assets: Iterable[str] | None = None,
        timeframe: str = "15m",
        now_ts: int | None = None,
        fee_liquidity_role: str = "maker",
    ):
        if timeframe != "15m":
            raise ValueError("Only 15m Polymarket Up/Down markets are supported")
        self.client = client
        self.assets = [asset.strip().lower() for asset in (assets or ["btc"]) if asset.strip()]
        self.timeframe = timeframe
        self.now_ts = now_ts
        self.fee_liquidity_role = fee_liquidity_role
        print(f"[polymarket] assets={','.join(self.assets) or 'NONE'} timeframe={self.timeframe}")

    @staticmethod
    def slug_for_window(asset: str, timeframe: str = "15m", now_ts: int | None = None) -> str:
        durations = {"15m": 900, "5m": 300}
        if timeframe not in durations:
            raise ValueError(f"Unsupported Polymarket up/down timeframe: {timeframe}")
        ts = int(time.time() if now_ts is None else now_ts)
        start = (ts // durations[timeframe]) * durations[timeframe]
        return f"{asset.lower()}-updown-{timeframe}-{start}"

    def _ticker_for_slug(self, asset: str, slug: str) -> str | None:
        prefix = POLYMARKET_ASSET_PREFIX.get(asset)
        if not prefix:
            return None
        start_ts = slug.rsplit("-", 1)[-1]
        return f"{prefix}-{start_ts}"

    def _market_from_event(self, asset: str, event: dict[str, Any]) -> Market | None:
        markets = event.get("markets") or []
        if not markets:
            return None
        row = markets[0]
        if not row.get("active") or row.get("closed"):
            return None
        if row.get("acceptingOrders") is False or row.get("enableOrderBook") is False:
            return None

        outcomes = [str(value).lower() for value in _json_list(row.get("outcomes"))]
        token_ids = [str(value) for value in _json_list(row.get("clobTokenIds"))]
        outcome_prices = _json_list(row.get("outcomePrices"))
        if len(outcomes) != len(token_ids):
            return None

        try:
            up_idx = outcomes.index("up")
            down_idx = outcomes.index("down")
        except ValueError:
            logging.warning("polymarket: %s missing Up/Down outcomes", row.get("slug"))
            return None

        yes_token_id = token_ids[up_idx]
        no_token_id = token_ids[down_idx]
        try:
            yes_book = self.client.get_orderbook(yes_token_id)
            no_book = self.client.get_orderbook(no_token_id)
        except Exception as exc:
            logging.warning(
                "polymarket: %s orderbook_fetch_error yes_token=%s no_token=%s error=%s",
                row.get("slug"),
                yes_token_id,
                no_token_id,
                exc,
            )
            return None

        end_dt = _parse_dt(row.get("endDate") or event.get("endDate"))
        secs_left = None
        if end_dt is not None:
            now_dt = datetime.fromtimestamp(
                self.now_ts if self.now_ts is not None else time.time(),
                tz=timezone.utc,
            )
            secs_left = (end_dt - now_dt).total_seconds()
            if secs_left <= 5:
                return None

        slug = row.get("slug") or event.get("slug") or ""
        ticker = self._ticker_for_slug(asset, slug)
        if ticker is None:
            return None

        fee_schedule = row.get("feeSchedule") or {}
        fee_rate = fee_schedule.get("rate")
        if fee_rate is None and row.get("feesEnabled"):
            fee_rate = 0.07

        market = Market(
            ticker=ticker,
            title=row.get("question") or event.get("title") or slug,
            category="Crypto",
            yes_bid=_best_bid(yes_book),
            yes_ask=_best_ask(yes_book),
            no_bid=_best_bid(no_book),
            no_ask=_best_ask(no_book),
            last_price=_last_price(yes_book, outcome_prices[up_idx] if up_idx < len(outcome_prices) else None),
            volume_24h=_float(row.get("volume24hr") or row.get("volume24hrClob")),
            liquidity_cents=int(round(_float(row.get("liquidityNum") or row.get("liquidityClob")) * 100)),
            open_interest=0.0,
            event_ticker=event.get("slug"),
            secs_left=secs_left,
            series_ticker=POLYMARKET_ASSET_PREFIX.get(asset),
            exchange="polymarket",
            polymarket_slug=slug,
            polymarket_condition_id=row.get("conditionId"),
            polymarket_yes_token_id=yes_token_id,
            polymarket_no_token_id=no_token_id,
            polymarket_fee_rate=float(fee_rate) if fee_rate is not None else None,
            polymarket_fee_liquidity_role=self.fee_liquidity_role,
            resolution_source=row.get("resolutionSource"),
            tick_size=str(row.get("orderPriceMinTickSize") or yes_book.get("tick_size") or "0.01"),
            neg_risk=bool(row.get("negRisk") or yes_book.get("neg_risk") or False),
        )
        self.client.cache_market(
            ticker,
            {
                "slug": slug,
                "condition_id": market.polymarket_condition_id,
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
                "tick_size": market.tick_size,
                "neg_risk": market.neg_risk,
            },
        )
        return market

    def iter_open_markets(self, limit_per_page: int = 200):
        kept = 0
        for asset in self.assets:
            slug = self.slug_for_window(asset, self.timeframe, now_ts=self.now_ts)
            try:
                event = self.client.get_event_by_slug(slug)
            except Exception as exc:
                print(f"[polymarket] {slug} event_fetch_error={exc}")
                continue

            market = self._market_from_event(asset, event)
            if market is None:
                print(f"[polymarket] {slug} no live order-book market")
                continue

            kept += 1
            print(
                f"[polymarket] KEPT {market.ticker} slug={market.polymarket_slug} "
                f"bid={market.yes_bid} ask={market.yes_ask} last={market.last_price} "
                f"secs_left={(market.secs_left or 0):.0f}"
            )
            yield market

        print(f"[polymarket] done kept={kept}")

    def get_top_of_book(self, ticker: str) -> dict:
        metadata = self.client._markets_by_ticker.get(ticker)
        if not metadata:
            return {}
        return self.client.get_orderbook(metadata["yes_token_id"])
