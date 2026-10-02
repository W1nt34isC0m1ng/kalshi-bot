from __future__ import annotations

from types import SimpleNamespace

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kalshi_bot" / "src"))

from kalshi_bot.executor import ExecutionEngine
from kalshi_bot.polymarket import PolymarketHttpClient, PolymarketMarketDataService
from kalshi_bot.risk import RiskManager
from kalshi_bot.models import Signal


class FakePolymarketClient:
    def __init__(self):
        self.created_orders = []
        self.registered = {}

    def get_event_by_slug(self, slug: str):
        assert slug == "btc-updown-15m-1790973900"
        return {
            "id": "1115951",
            "slug": slug,
            "title": "Bitcoin Up or Down - October 2, 4:45PM-5:00PM ET",
            "endDate": "2026-10-02T21:00:00Z",
            "active": True,
            "closed": False,
            "markets": [
                {
                    "id": "5192816",
                    "slug": slug,
                    "question": "Bitcoin Up or Down - October 2, 4:45PM-5:00PM ET",
                    "conditionId": "0xcondition",
                    "outcomes": '["Up", "Down"]',
                    "clobTokenIds": '["up-token", "down-token"]',
                    "outcomePrices": '["0.47", "0.53"]',
                    "active": True,
                    "closed": False,
                    "acceptingOrders": True,
                    "enableOrderBook": True,
                    "endDate": "2026-10-02T21:00:00Z",
                    "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
                    "feeSchedule": {"rate": 0.07, "takerOnly": True},
                    "cryptoMarketConfig": {
                        "asset": "btc",
                        "duration": "15m",
                        "twapEnabled": True,
                        "twapLookbackSeconds": 60,
                    },
                    "volume24hr": 1234.5,
                    "liquidityNum": 9876.5,
                    "negRisk": False,
                    "orderPriceMinTickSize": 0.01,
                }
            ],
        }

    def get_orderbook(self, token_id: str):
        if token_id == "up-token":
            return {
                "bids": [{"price": "0.45", "size": "10"}, {"price": "0.46", "size": "2"}],
                "asks": [{"price": "0.49", "size": "4"}, {"price": "0.48", "size": "3"}],
                "last_trade_price": "0.47",
            }
        if token_id == "down-token":
            return {
                "bids": [{"price": "0.51", "size": "5"}],
                "asks": [{"price": "0.54", "size": "6"}, {"price": "0.53", "size": "1"}],
                "last_trade_price": "0.52",
            }
        raise AssertionError(f"unexpected token id {token_id}")

    def cache_market(self, ticker, metadata):
        self.registered[ticker] = metadata

    def create_order(self, **kwargs):
        self.created_orders.append(kwargs)
        raise AssertionError("dry-run execution must not place orders")


def test_polymarket_slug_uses_utc_15_minute_floor():
    assert (
        PolymarketMarketDataService.slug_for_window("btc", "15m", now_ts=1790974450)
        == "btc-updown-15m-1790973900"
    )


def test_polymarket_market_data_rejects_unsupported_timeframes():
    with pytest.raises(ValueError, match="Only 15m"):
        PolymarketMarketDataService(FakePolymarketClient(), assets=["btc"], timeframe="5m")


def test_polymarket_market_data_maps_gamma_event_and_clob_books():
    client = FakePolymarketClient()
    service = PolymarketMarketDataService(
        client,
        assets=["btc"],
        now_ts=1790974450,
    )

    markets = list(service.iter_open_markets())

    assert len(markets) == 1
    market = markets[0]
    assert market.exchange == "polymarket"
    assert market.ticker == "PMBTC15M-1790973900"
    assert market.title == "Bitcoin Up or Down - October 2, 4:45PM-5:00PM ET"
    assert market.yes_bid == 46
    assert market.yes_ask == 48
    assert market.no_bid == 51
    assert market.no_ask == 53
    assert market.last_price == 47
    assert market.volume_24h == 1234.5
    assert market.liquidity_cents == 987650
    assert market.secs_left is not None
    assert market.polymarket_slug == "btc-updown-15m-1790973900"
    assert market.polymarket_condition_id == "0xcondition"
    assert market.polymarket_yes_token_id == "up-token"
    assert market.polymarket_no_token_id == "down-token"
    assert market.polymarket_fee_rate == 0.07
    assert "chain.link" in (market.resolution_source or "")
    assert client.registered["PMBTC15M-1790973900"]["yes_token_id"] == "up-token"


def test_polymarket_market_data_skips_when_clob_book_fetch_fails():
    class FailingBookClient(FakePolymarketClient):
        def get_orderbook(self, token_id: str):
            raise TimeoutError("book timeout")

    service = PolymarketMarketDataService(
        FailingBookClient(),
        assets=["btc"],
        now_ts=1790974450,
    )

    assert list(service.iter_open_markets()) == []


def test_polymarket_create_order_uses_tokens_prices_and_gtd_expiration(monkeypatch):
    client = PolymarketHttpClient(private_key="0xabc")
    client.cache_market(
        "PMBTC15M-1790973900",
        {
            "yes_token_id": "up-token",
            "no_token_id": "down-token",
            "tick_size": "0.01",
            "neg_risk": False,
        },
    )
    captured = []

    class FakeClob:
        def create_and_post_order(self, order_args, options, order_type, post_only):
            captured.append(
                {
                    "token_id": order_args.token_id,
                    "price": order_args.price,
                    "size": order_args.size,
                    "expiration": order_args.expiration,
                    "tick_size": options.tick_size,
                    "neg_risk": options.neg_risk,
                    "order_type": order_type,
                    "post_only": post_only,
                }
            )
            return {"orderID": "order-1", "status": "live"}

    monkeypatch.setattr(client, "_authenticated_clob_client", lambda: FakeClob())

    yes_response = client.create_order(
        ticker="PMBTC15M-1790973900",
        side="yes",
        action="buy",
        count=3,
        price=47,
        expiration_ts=1790974810,
        post_only=True,
    )
    no_response = client.create_order(
        ticker="PMBTC15M-1790973900",
        side="no",
        action="buy",
        count=4,
        price=47,
        expiration_ts=1790974811,
        post_only=True,
    )

    assert yes_response["order"]["order_id"] == "order-1"
    assert no_response["order"]["order_id"] == "order-1"
    assert captured == [
        {
            "token_id": "up-token",
            "price": 0.47,
            "size": 3,
            "expiration": 1790974810,
            "tick_size": "0.01",
            "neg_risk": False,
            "order_type": "GTD",
            "post_only": True,
        },
        {
            "token_id": "down-token",
            "price": 0.53,
            "size": 4,
            "expiration": 1790974811,
            "tick_size": "0.01",
            "neg_risk": False,
            "order_type": "GTD",
            "post_only": True,
        },
    ]


def test_execution_engine_dry_run_never_places_polymarket_order(tmp_path):
    client = FakePolymarketClient()
    settings = SimpleNamespace(
        dry_run=True,
        auto_sizing=False,
        order_count=2,
        max_position_per_market=5,
        max_notional_cents_per_market=5000,
        max_total_notional_cents=10000,
        order_ttl_seconds=10,
        cooldown_seconds=60,
        risk_state_path=str(tmp_path / "risk.json"),
    )
    risk = RiskManager(settings)
    engine = ExecutionEngine(client, settings, risk)
    signal = Signal(
        ticker="PMBTC15M-1790973900",
        title="Bitcoin Up or Down",
        side="yes",
        price=48,
        edge_cents=8,
        spread_cents=2,
        score=7.5,
        reason="test",
        yes_bid=46,
        yes_ask=48,
        strategy="crypto_prob",
    )

    result = engine.maybe_send(signal)

    assert result["status"] == "dry_run"
    assert client.created_orders == []
