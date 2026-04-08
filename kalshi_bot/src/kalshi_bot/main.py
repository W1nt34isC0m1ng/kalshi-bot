from __future__ import annotations

import logging
import threading
import time

from rich.console import Console
from rich.table import Table

from .auth import KalshiSigner
from .client import KalshiHttpClient
from .config import Settings
from .executor import ExecutionEngine
from .market_data import MarketDataService
from .risk import RiskManager
from .strategy import MeanReversionMaker
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
        table.add_row(sig.ticker, sig.side.upper(), str(sig.price), str(sig.edge_cents), str(sig.spread_cents), f"{sig.score:.1f}", sig.reason)
    console.clear()
    console.print(table)


def main() -> None:
    settings = Settings()
    public_client, private_client, signer = build_clients(settings)
    market_data = MarketDataService(public_client)
    strategy = MeanReversionMaker(settings)
    risk = RiskManager(settings)
    executor = ExecutionEngine(private_client or public_client, settings, risk)

    latest_ticks: set[str] = set()

    def ws_consumer(message: dict) -> None:
        msg_type = message.get("type") or message.get("msg_type")
        if msg_type:
            logging.info("ws: %s", msg_type)

    if signer:
        def ws_thread():
            ws = KalshiWebSocket(settings.ws_url, signer, ws_consumer)
            ws.run(list(latest_ticks)[:50])
        threading.Thread(target=ws_thread, daemon=True).start()

    while True:
        signals = []
        latest_ticks.clear()
        for market in market_data.iter_open_markets(limit_per_page=200):
            sig = strategy.evaluate(market)
            if sig:
                latest_ticks.add(sig.ticker)
                signals.append(sig)
        render_signals(signals)
        for sig in sorted(signals, key=lambda s: s.score, reverse=True)[:3]:
            result = executor.maybe_send(sig)
            logging.info("trade result: %s", result["status"])
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
