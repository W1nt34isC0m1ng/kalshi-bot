from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from dotenv import load_dotenv
from typing import Optional

load_dotenv()


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    env: str = os.getenv("KALSHI_ENV", "demo")
    api_key_id: str = os.getenv("KALSHI_API_KEY_ID", "")
    # No hardcoded path — must be set via KALSHI_PRIVATE_KEY_PATH in .env
    private_key_path: str = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
    base_url: str = os.getenv("KALSHI_BASE_URL", "https://demo-api.kalshi.co/trade-api/v2")
    ws_url: str = os.getenv("KALSHI_WS_URL", "wss://demo-api.kalshi.co/trade-api/ws/v2")
    category_filter: Optional[list[str]] = None
    min_daily_volume: int = int(os.getenv("MIN_DAILY_VOLUME", "200"))
    max_position_per_market: int = int(os.getenv("MAX_POSITION_PER_MARKET", "5"))
    max_notional_cents_per_market: int = int(os.getenv("MAX_NOTIONAL_CENTS_PER_MARKET", "2500"))
    max_total_notional_cents: int = int(os.getenv("MAX_TOTAL_NOTIONAL_CENTS", "10000"))
    edge_threshold_cents: int = int(os.getenv("EDGE_THRESHOLD_CENTS", "1"))
    order_ttl_seconds: int = int(os.getenv("ORDER_TTL_SECONDS", "10"))
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "2"))
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    # Execution tuning — all tunable via env without code changes
    cooldown_seconds: int = int(os.getenv("COOLDOWN_SECONDS", "60"))
    markets_per_event: int = int(os.getenv("MARKETS_PER_EVENT", "2"))
    max_signals_per_loop: int = int(os.getenv("MAX_SIGNALS_PER_LOOP", "3"))
    order_count: int = int(os.getenv("ORDER_COUNT", "2"))
    position_reconcile_interval_seconds: int = int(
        os.getenv("POSITION_RECONCILE_INTERVAL_SECONDS", "30")
    )
    risk_state_path: str = os.getenv("RISK_STATE_PATH", "logs/risk_state.json")
    # Strategy tuning
    momentum_scaling_factor: float = float(os.getenv("MOMENTUM_SCALING_FACTOR", "0.15"))

    def __post_init__(self) -> None:
        if self.category_filter is None:
            self.category_filter = _csv("CATEGORY_FILTER", "Crypto")

        if not self.api_key_id or not self.private_key_path:
            logging.warning(
                "KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH not set — "
                "running without authenticated client (dry_run only)"
            )
