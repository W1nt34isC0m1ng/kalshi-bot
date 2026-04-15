from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.table import Table

from .auth import KalshiSigner
from .client import KalshiHttpClient
from .config import Settings
from .executor import ExecutionEngine
from .journal import TradeJournal
from .market_data import MarketDataService
from .models import Market
from .risk import RiskManager
from .crypto_strategy import CryptoProbStrategy
from .ws import KalshiWebSocket

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
console = Console()


def build_clients(settings: Settings):
    public_client = KalshiHttpClient(settings.base_url)
    signer = None
    private_client = None

    if settings.api_key_id and settings.private_key_path:
        signer = KalshiSigner(settings.private_key_path, settings.api_key_id)
        private_client = KalshiHttpClient(settings.base_url, signer=signer)

    return public_client, private_client, signer


def render_signals(signals):
    table = Table(title="Kalshi Bot Signals")
    table.add_column("Ticker")
    table.add_column("Side")
    table.add_column("Price")
    table.add_column("Edge")
    table.add_column("Spread")
    table.add_column("Score")
    table.add_column("Why")

    for sig in sorted(signals, key=lambda s: s.score, reverse=True)[:15]:
        table.add_row(
            sig.ticker,
            sig.side.upper(),
            str(sig.price),
            str(sig.edge_cents),
            str(sig.spread_cents),
            f"{sig.score:.1f}",
            sig.reason,
        )

    console.clear()
    console.print(table)


def _passes_signal_filters(signal, settings: Settings) -> bool:
    trading_now = datetime.now(ZoneInfo(settings.trading_timezone))
    trading_start = dt_time(
        hour=settings.trading_start_hour_local,
        minute=settings.trading_start_minute_local,
    )
    trading_end = dt_time(
        hour=settings.trading_end_hour_local,
        minute=settings.trading_end_minute_local,
    )
    now_local = trading_now.time()

    # Allowed window is [start, end). Outside that window, block new entries.
    if not (trading_start <= now_local < trading_end):
        logging.info(
            "filter: REJECT %s local_time=%s outside trading window %s-%s (%s)",
            signal.ticker,
            now_local.strftime("%H:%M:%S"),
            trading_start.strftime("%H:%M"),
            trading_end.strftime("%H:%M"),
            settings.trading_timezone,
        )
        return False

    if signal.momentum_boost <= settings.min_momentum_boost:
        logging.info(
            "filter: REJECT %s momentum_boost=%.2f <= min_momentum_boost=%.2f",
            signal.ticker,
            signal.momentum_boost,
            settings.min_momentum_boost,
        )
        return False
    return True


def _reconcile_positions(private_client: KalshiHttpClient, risk: RiskManager) -> None:
    """Fetch live positions from the exchange and rebuild risk state."""
    try:
        resp = private_client.get_positions()
        positions = resp.get("market_positions", []) or resp.get("positions", []) or []
        risk.reconcile_from_positions(positions)
    except Exception as exc:
        logging.warning("startup: could not reconcile positions (%s) — using persisted state", exc)


def _start_websocket(
    settings: Settings,
    signer: KalshiSigner,
    ws_market_cache: dict[str, dict],
    tickers_to_subscribe: list[str],
    shutdown_event: threading.Event,
) -> None:
    """Run the WebSocket in a daemon thread, updating ws_market_cache on tick."""

    def on_message(message: dict) -> None:
        msg_type = message.get("type") or message.get("msg_type", "")
        msg = message.get("msg", {})

        if msg_type == "ticker" and msg:
            ticker = msg.get("market_ticker") or msg.get("ticker")
            if ticker:
                entry = ws_market_cache.setdefault(ticker, {})
                entry.update({k: v for k, v in msg.items() if v is not None})
                logging.debug("ws: tick %s bid=%s ask=%s", ticker, msg.get("yes_bid"), msg.get("yes_ask"))

    def run():
        while not shutdown_event.is_set():
            try:
                ws = KalshiWebSocket(settings.ws_url, signer, on_message)
                ws.run(tickers_to_subscribe)
            except Exception as exc:
                if shutdown_event.is_set():
                    break
                logging.warning("ws: disconnected (%s), reconnecting in 5s", exc)
                time.sleep(5)

    threading.Thread(target=run, daemon=True, name="ws-consumer").start()


def _apply_ws_cache(market: Market, ws_market_cache: dict[str, dict]) -> Market:
    """Return a copy of market with bid/ask/last updated from WS tick cache."""
    cached = ws_market_cache.get(market.ticker)
    if not cached:
        return market

    yes_bid = cached.get("yes_bid")
    yes_ask = cached.get("yes_ask")
    last = cached.get("last_price")

    if yes_bid is not None:
        market.yes_bid = int(yes_bid)
    if yes_ask is not None:
        market.yes_ask = int(yes_ask)
    if last is not None:
        market.last_price = int(last)

    return market


def main() -> None:
    settings = Settings()
    public_client, private_client, signer = build_clients(settings)

    api_client = private_client or public_client
    market_data = MarketDataService(api_client, markets_per_event=settings.markets_per_event)
    risk = RiskManager(settings)
    executor = ExecutionEngine(api_client, settings, risk)
    strategy = CryptoProbStrategy(api_client)
    journal = TradeJournal()

    if private_client:
        _reconcile_positions(private_client, risk)

    ws_market_cache: dict[str, dict] = {}
    active_tickers: list[str] = []
    last_position_reconcile = time.monotonic()

    shutdown_event = threading.Event()

    def _handle_signal(signum, frame):
        logging.info("shutdown signal received, draining...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    ws_started = False

    while not shutdown_event.is_set():
        if (
            private_client
            and not settings.dry_run
            and (time.monotonic() - last_position_reconcile) >= settings.position_reconcile_interval_seconds
        ):
            _reconcile_positions(private_client, risk)
            last_position_reconcile = time.monotonic()

        signals = []
        markets = list(market_data.iter_open_markets(limit_per_page=200))

        market_tickers_this_loop = [market.ticker for market in markets]
        if signer and not ws_started and market_tickers_this_loop:
            active_tickers.extend(
                ticker for ticker in market_tickers_this_loop if ticker not in active_tickers
            )
            _start_websocket(settings, signer, ws_market_cache, active_tickers, shutdown_event)
            ws_started = True

        for market in markets:
            market = _apply_ws_cache(market, ws_market_cache)
            sig = strategy.evaluate(market)
            if sig and _passes_signal_filters(sig, settings):
                signals.append(sig)

        render_signals(signals)

        for sig in sorted(signals, key=lambda s: s.score, reverse=True)[: settings.max_signals_per_loop]:
            result = executor.maybe_send(sig)
            logging.info("trade result: %s", result["status"])

            response = result.get("response", {}) if isinstance(result, dict) else {}
            order = response.get("order", response) if isinstance(response, dict) else {}
            order_id = str(order.get("order_id", "") or "")
            filled_count = str(order.get("fill_count", "") or "")

            journal.log_signal(
                sig,
                status=result["status"],
                status_reason=result.get("reason", ""),
                order_id=order_id,
                filled_count=filled_count,
            )

        time.sleep(settings.poll_interval_seconds)

    logging.info("shutting down...")
    journal.shutdown()
    logging.info("done.")


if __name__ == "__main__":
    main()
