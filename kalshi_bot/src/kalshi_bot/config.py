from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

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
    max_premium_cents_per_contract: int = int(os.getenv("MAX_PREMIUM_CENTS_PER_CONTRACT", "77"))
    order_ttl_seconds: int = int(os.getenv("ORDER_TTL_SECONDS", "10"))
    poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "2"))
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    # Execution tuning — all tunable via env without code changes
    cooldown_seconds: int = int(os.getenv("COOLDOWN_SECONDS", "60"))
    markets_per_event: int = int(os.getenv("MARKETS_PER_EVENT", "2"))
    max_signals_per_loop: int = int(os.getenv("MAX_SIGNALS_PER_LOOP", "3"))
    order_count: int = int(os.getenv("ORDER_COUNT", "2"))
    auto_sizing: bool = os.getenv("AUTO_SIZING", "false").lower() == "true"
    bankroll_cents: int = int(os.getenv("BANKROLL_CENTS", "0"))
    risk_fraction_per_trade: float = float(os.getenv("RISK_FRACTION_PER_TRADE", "0.03"))
    min_order_count: int = int(os.getenv("MIN_ORDER_COUNT", "1"))
    max_order_count: int = int(os.getenv("MAX_ORDER_COUNT", "100"))
    position_reconcile_interval_seconds: int = int(
        os.getenv("POSITION_RECONCILE_INTERVAL_SECONDS", "30")
    )
    trade_journal_path: str = os.getenv("TRADE_JOURNAL_PATH", "logs/trade_journal.csv")
    risk_state_path: str = os.getenv("RISK_STATE_PATH", "logs/risk_state.json")
    # Strategy tuning
    enable_signal_filters: bool = (
        os.getenv("ENABLE_SIGNAL_FILTERS", "true").lower() == "true"
    )
    crypto_min_edge_cents: int = int(os.getenv("CRYPTO_MIN_EDGE_CENTS", "6"))
    crypto_max_spread_cents: int = int(os.getenv("CRYPTO_MAX_SPREAD_CENTS", "10"))
    crypto_min_score: float = float(os.getenv("CRYPTO_MIN_SCORE", "6.0"))
    momentum_scaling_factor: float = float(os.getenv("MOMENTUM_SCALING_FACTOR", "0.15"))
    min_momentum_boost: float = float(os.getenv("MIN_MOMENTUM_BOOST", "0.01"))
    live_side_mode: str = os.getenv("LIVE_SIDE_MODE", "both").lower()
    trading_timezone: str = os.getenv("TRADING_TIMEZONE", "America/New_York")
    trading_start_hour_local: int = int(os.getenv("TRADING_START_HOUR_LOCAL", "7"))
    trading_start_minute_local: int = int(os.getenv("TRADING_START_MINUTE_LOCAL", "0"))
    trading_end_hour_local: int = int(os.getenv("TRADING_END_HOUR_LOCAL", "23"))
    trading_end_minute_local: int = int(os.getenv("TRADING_END_MINUTE_LOCAL", "58"))
    # Golf strategy layer (disabled by default; snapshot-driven)
    enable_golf_strategy: bool = os.getenv("ENABLE_GOLF_STRATEGY", "false").lower() == "true"
    golf_shadow_mode: bool = os.getenv("GOLF_SHADOW_MODE", "true").lower() == "true"
    golf_snapshot_path: str = os.getenv("GOLF_SNAPSHOT_PATH", "config/golf_snapshot.csv")
    golf_manual_overlay_path: str = os.getenv(
        "GOLF_MANUAL_OVERLAY_PATH",
        "config/golf_manual_overlay.csv",
    )
    golf_market_refresh_seconds: int = int(os.getenv("GOLF_MARKET_REFRESH_SECONDS", "60"))
    golf_auto_refresh_snapshot: bool = (
        os.getenv("GOLF_AUTO_REFRESH_SNAPSHOT", "false").lower() == "true"
    )
    golf_snapshot_max_age_seconds: int = int(
        os.getenv("GOLF_SNAPSHOT_MAX_AGE_SECONDS", "21600")
    )
    datagolf_api_key: str = os.getenv("DATAGOLF_API_KEY", "")
    datagolf_tour: str = os.getenv("DATAGOLF_TOUR", "pga")
    golf_use_owgr_filter: bool = os.getenv("GOLF_USE_OWGR_FILTER", "false").lower() == "true"
    golf_world_rank_csv_path: str = os.getenv("GOLF_WORLD_RANK_CSV_PATH", "")
    golf_enabled_market_types: list[str] | None = None
    golf_outright_series: list[str] | None = None
    golf_top10_series: list[str] | None = None
    golf_make_cut_series: list[str] | None = None
    golf_sg_t2g_last4_min: float = float(os.getenv("GOLF_SG_T2G_LAST4_MIN", "18"))
    golf_sg_approach_rank_max: int = int(os.getenv("GOLF_SG_APPROACH_RANK_MAX", "30"))
    golf_course_fit_min: float = float(os.getenv("GOLF_COURSE_FIT_MIN", "0"))
    golf_prev_start_finish_max: int = int(os.getenv("GOLF_PREV_START_FINISH_MAX", "35"))
    golf_top8_last7_min: int = int(os.getenv("GOLF_TOP8_LAST7_MIN", "1"))
    golf_owgr_rank_max: int = int(os.getenv("GOLF_OWGR_RANK_MAX", "60"))
    golf_sg_arg_per_round_min: float = float(os.getenv("GOLF_SG_ARG_PER_ROUND_MIN", "0.25"))
    golf_outright_min_price_cents: int = int(os.getenv("GOLF_OUTRIGHT_MIN_PRICE_CENTS", "3"))
    golf_outright_max_price_cents: int = int(os.getenv("GOLF_OUTRIGHT_MAX_PRICE_CENTS", "12"))
    golf_outright_min_ratio: float = float(os.getenv("GOLF_OUTRIGHT_MIN_RATIO", "1.8"))
    golf_top10_min_ratio: float = float(os.getenv("GOLF_TOP10_MIN_RATIO", "0"))
    golf_make_cut_min_ratio: float = float(os.getenv("GOLF_MAKE_CUT_MIN_RATIO", "0"))

    def __post_init__(self) -> None:
        if self.category_filter is None:
            self.category_filter = _csv("CATEGORY_FILTER", "Crypto")
        if self.golf_enabled_market_types is None:
            self.golf_enabled_market_types = _csv("GOLF_ENABLED_MARKET_TYPES", "outright")
        if self.golf_outright_series is None:
            self.golf_outright_series = _csv("GOLF_OUTRIGHT_SERIES", "KXPGATOUR,KXMASTERS")
        if self.golf_top10_series is None:
            self.golf_top10_series = _csv("GOLF_TOP10_SERIES", "KXPGATOP10")
        if self.golf_make_cut_series is None:
            self.golf_make_cut_series = _csv("GOLF_MAKE_CUT_SERIES", "KXPGAMAKECUT,KXMASTERSCUT")

        if not self.api_key_id or not self.private_key_path:
            logging.warning(
                "KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH not set — "
                "running without authenticated client (dry_run only)"
            )

        if self.live_side_mode not in {"both", "yes_only"}:
            logging.warning(
                "Unknown LIVE_SIDE_MODE=%s; defaulting to both",
                self.live_side_mode,
            )
            self.live_side_mode = "both"

        if self.auto_sizing and self.bankroll_cents <= 0:
            logging.warning(
                "AUTO_SIZING is enabled but BANKROLL_CENTS is not positive; "
                "falling back to fixed ORDER_COUNT"
            )
            self.auto_sizing = False

        if self.min_order_count < 1:
            logging.warning("MIN_ORDER_COUNT must be >= 1; defaulting to 1")
            self.min_order_count = 1

        if self.max_order_count < self.min_order_count:
            logging.warning(
                "MAX_ORDER_COUNT=%s is below MIN_ORDER_COUNT=%s; raising max to min",
                self.max_order_count,
                self.min_order_count,
            )
            self.max_order_count = self.min_order_count

        valid_market_types = {"outright", "top10", "make_cut"}
        requested_market_types = [m.lower() for m in self.golf_enabled_market_types]
        self.golf_enabled_market_types = [
            m for m in requested_market_types if m in valid_market_types
        ]
        invalid_market_types = sorted(set(requested_market_types) - valid_market_types)
        if invalid_market_types:
            logging.warning(
                "Unknown GOLF_ENABLED_MARKET_TYPES=%s; ignoring invalid values",
                ",".join(invalid_market_types),
            )

        if self.golf_auto_refresh_snapshot and not self.datagolf_api_key:
            logging.warning(
                "GOLF_AUTO_REFRESH_SNAPSHOT is enabled but DATAGOLF_API_KEY is not set; "
                "golf snapshot auto-refresh will stay idle"
            )
