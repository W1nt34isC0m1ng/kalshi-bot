from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from src.kalshi_bot.assets import ASSET_CONFIG, asset_prefix_from_ticker


JOURNAL_PATH = "logs/trade_journal.csv"
OUTPUT_PATH = "logs/trade_backtest_results.csv"

# Kalshi binary fee estimate for journal dry-runs.
#
# live_account_report.py uses exchange-returned maker_fees_dollars and
# taker_fees_dollars. Dry-run journals do not have exchange fee fields, so the
# backtest estimates fees with Kalshi's public binary-contract formula:
#   fee = ceil($0.07 * contracts * price * (1 - price) to whole cents)
# where price is the bought contract premium in dollars. The estimate is
# charged once on entry and subtracted from realized gross P&L.
KALSHI_BINARY_FEE_RATE_DOLLARS = Decimal("0.07")

COINBASE_PRODUCTS = {
    series: config["product"]
    for series, config in ASSET_CONFIG.items()
}

# Compiled regex for the Kalshi ticker format: KXBTC15M-26APR111700-00
# Groups: (series_prefix, YYMMMDDHHMM, suffix)
# The date+time chunk is 11 chars: YY (2) + MMM (3) + DD (2) + HH (2) + MM (2)
_TICKER_RE = re.compile(
    r"^([A-Z][A-Z0-9]+)-(\d{2}[A-Z]{3}\d{6})-(\d+)$",
    re.IGNORECASE,
)


_MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
    "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_MARKET_TICKER_TZ = ZoneInfo("America/New_York")


def parse_market_ticker(ticker: str) -> tuple[str, datetime, str]:
    """Parse a Kalshi ticker like KXBTC15M-26APR111700-00.

    Returns (series_prefix, expiry_utc, suffix_str).

    The 11-char date+time chunk is YYMMMDDHHMM:
      YY  = 2-digit year   (e.g. "26" → 2026)
      MMM = month abbrev   (e.g. "APR")
      DD  = day            (e.g. "11")
      HH  = hour in America/New_York
      MM  = minute in America/New_York

    The suffix is the last two digits of the expiry minute (00, 15, 30, 45)
    and is purely a naming convention — it does NOT indicate market direction.
    """
    m = _TICKER_RE.match((ticker or "").strip().upper())
    if not m:
        raise ValueError(f"Unrecognized ticker format: {ticker!r}")

    prefix = m.group(1)
    dt_str = m.group(2)   # YYMMMDDHHMM (11 chars)
    suffix = m.group(3)

    yy = int(dt_str[0:2])
    mon_str = dt_str[2:5]
    day = int(dt_str[5:7])
    hour = int(dt_str[7:9])
    minute = int(dt_str[9:11])

    month = _MONTH_MAP.get(mon_str)
    if month is None:
        raise ValueError(f"Unrecognized month abbreviation {mon_str!r} in {ticker!r}")

    expiry_local = datetime(2000 + yy, month, day, hour, minute, 0, tzinfo=_MARKET_TICKER_TZ)
    expiry_utc = expiry_local.astimezone(timezone.utc)
    return prefix, expiry_utc, suffix


def get_precision_for_product(product: str) -> int:
    return {
        "BTC-USD": 2,
        "ETH-USD": 2,
        "SOL-USD": 4,
        "DOGE-USD": 7,
        "XRP-USD": 5,
    }.get(product, 2)


def round_target(value: float, product: str) -> float:
    return round(value, get_precision_for_product(product))


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _optional_int(value: Any) -> int | None:
    if _is_missing(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if hasattr(row, "get"):
        value = row.get(key, default)
    else:
        value = default
    return default if _is_missing(value) else value


def premium_cents_for_side(side: str, yes_price_cents: int) -> int:
    """Return the bought contract premium in cents for a YES-equivalent price."""
    normalized = (side or "").lower()
    if normalized == "yes":
        return yes_price_cents
    if normalized == "no":
        return 100 - yes_price_cents
    raise ValueError(f"Unknown binary side: {side!r}")


def estimate_kalshi_fee_cents(side: str, yes_price_cents: int, contract_count: int = 1) -> int:
    """Estimate Kalshi binary-contract fees in cents for a dry-run order."""
    if contract_count <= 0:
        return 0

    premium_cents = premium_cents_for_side(side, yes_price_cents)
    if premium_cents <= 0 or premium_cents >= 100:
        return 0

    premium_dollars = Decimal(premium_cents) / Decimal(100)
    fee_dollars = (
        KALSHI_BINARY_FEE_RATE_DOLLARS
        * Decimal(contract_count)
        * premium_dollars
        * (Decimal(1) - premium_dollars)
    )
    return int((fee_dollars * Decimal(100)).to_integral_value(rounding=ROUND_CEILING))


def calculate_trade_pnl(
    side: str,
    yes_price_cents: int,
    yes_outcome: int,
    contract_count: int = 1,
) -> dict[str, int | bool]:
    """Compute gross and after-fee P&L for a binary dry-run trade."""
    normalized_side = (side or "").lower()
    won = (yes_outcome == 1 and normalized_side == "yes") or (
        yes_outcome == 0 and normalized_side == "no"
    )
    premium_cents = premium_cents_for_side(normalized_side, yes_price_cents)
    payout_cents = 100 if won else 0
    pnl_cents_gross = (payout_cents - premium_cents) * max(contract_count, 0)
    fee_cents = estimate_kalshi_fee_cents(normalized_side, yes_price_cents, contract_count)

    return {
        "won": won,
        "pnl_cents_gross": pnl_cents_gross,
        "fee_cents": fee_cents,
        "pnl_cents_net": pnl_cents_gross - fee_cents,
    }


def contract_count_from_row(row: Any) -> int:
    """Prefer actual fills, then intended dry-run size, then a single contract."""
    for key in ("filled_count", "requested_count", "contract_count"):
        count = _optional_int(_row_value(row, key))
        if count is not None and count > 0:
            return count
    return 1


_REASON_VALUE_RE = re.compile(r"(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)=(?P<value>[-+]?\d+(?:\.\d+)?)")


def _reason_number(reason: Any, key: str) -> float | None:
    if _is_missing(reason):
        return None
    for match in _REASON_VALUE_RE.finditer(str(reason)):
        if match.group("key").lower() == key.lower():
            return _optional_float(match.group("value"))
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if _is_missing(value):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _range_bucket(value: float | None, ranges: list[tuple[float, float, str]], *, overflow: str | None = None) -> str | None:
    if value is None:
        return None
    for lower, upper, label in ranges:
        if lower < value <= upper:
            return label
    if overflow is not None and value > ranges[-1][1]:
        return overflow
    return None


def secs_left_bucket(secs_left: float | None) -> str | None:
    return _range_bucket(
        secs_left,
        [
            (600, 900, "900-600"),
            (300, 600, "600-300"),
            (90, 300, "300-90"),
            (30, 90, "90-30"),
        ],
    )


def d2_bucket(d2: float | None) -> str | None:
    if d2 is None:
        return None
    return _range_bucket(
        abs(d2),
        [
            (-1, 0.5, "0-0.5"),
            (0.5, 1.0, "0.5-1.0"),
            (1.0, 1.5, "1.0-1.5"),
            (1.5, 2.0, "1.5-2.0"),
        ],
        overflow="2.0+",
    )


def spread_bucket(spread_cents: float | None) -> str | None:
    return _range_bucket(
        spread_cents,
        [
            (-1, 0, "0"),
            (0, 2, "1-2"),
            (2, 5, "3-5"),
            (5, 10, "5-10"),
        ],
        overflow="10+",
    )


def edge_bucket(edge_cents: float | None) -> str | None:
    return _range_bucket(
        edge_cents,
        [
            (-1, 3, "0-3"),
            (3, 6, "3-6"),
            (6, 10, "6-10"),
        ],
        overflow="10+",
    )


def regime_labels_for_trade(row: Any) -> dict[str, object]:
    ticker = str(_row_value(row, "ticker", "") or "")
    asset = asset_prefix_from_ticker(ticker)
    reason = _row_value(row, "reason", "")
    trade_time = _parse_timestamp(_row_value(row, "ts_utc")) or _parse_timestamp(
        _row_value(row, "expiry_time")
    )

    hour_et = None
    hour_et_band = None
    if trade_time is not None:
        local_time = trade_time.astimezone(ZoneInfo("America/New_York"))
        hour_et = local_time.hour
        minutes = (local_time.hour * 60) + local_time.minute
        hour_et_band = "cash_hours" if (9 * 60 + 30) <= minutes < (16 * 60) else "other"

    secs_left = _optional_float(_row_value(row, "secs_left"))
    if secs_left is None:
        secs_left = _reason_number(reason, "secs_left")
    d2_value = _optional_float(_row_value(row, "d2"))
    if d2_value is None:
        d2_value = _reason_number(reason, "d2")
    spread = _optional_float(_row_value(row, "spread_cents"))
    edge = _optional_float(_row_value(row, "edge_cents"))
    side = str(_row_value(row, "side", "") or "").lower() or None

    return {
        "asset": asset,
        "hour_et": hour_et,
        "hour_et_band": hour_et_band,
        "secs_left_bucket": secs_left_bucket(secs_left),
        "d2_bucket": d2_bucket(d2_value),
        "spread_bucket": spread_bucket(spread),
        "side": side,
        "edge_bucket": edge_bucket(edge),
    }


def fetch_coinbase_candles(product: str, start: datetime, end: datetime, granularity: int = 60):
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    params = {
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "granularity": granularity,
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError(f"Unexpected candle response: {data}")
    return sorted(data, key=lambda x: x[0])


def fetch_spot_near_time(product: str, target_time: datetime, window_minutes: int = 20) -> float:
    """Return the close price of the 1-minute candle nearest to target_time."""
    start = target_time - timedelta(minutes=window_minutes)
    end = target_time + timedelta(minutes=5)

    candles = fetch_coinbase_candles(product, start, end, granularity=60)
    if not candles:
        raise ValueError(f"No candles found for {product} near {target_time.isoformat()}")

    target_ts = target_time.timestamp()
    best = min(candles, key=lambda c: abs(c[0] - target_ts))
    return float(best[4])  # close price


def resolve_yes_outcome(ticker: str, product: str, expiry_time: datetime):
    """Determine the YES/NO outcome for a resolved KXBTC15M / KXETH15M market.

    Market structure:
      - The 15-minute window opens at (expiry_time - 15 minutes).
      - The *strike* is the Coinbase spot at window open.
      - The *settlement* price is the Coinbase spot at expiry_time.
      - YES wins if settlement >= strike  (price was flat or higher by expiry).
      - NO  wins if settlement <  strike  (price fell).

    The ticker suffix (00, 15, 30, 45) is only the minute component of the
    expiry time — it encodes WHEN the market expires, not which direction it
    pays.  All KXBTC15M markets use the same YES=up / NO=down convention.
    """
    window_open = expiry_time - timedelta(minutes=15)

    # Strike: Coinbase spot at the moment the 15-minute window opened
    target_spot = fetch_spot_near_time(product, window_open)
    target = round_target(target_spot, product)

    # Settlement: Coinbase spot at the moment the window closed (expiry)
    expiry_spot = fetch_spot_near_time(product, expiry_time)

    # YES wins when price ended at or above the opening strike
    yes_outcome = 1 if expiry_spot >= target else 0

    return target, expiry_spot, yes_outcome


def pnl_for_trade(side: str, price: int, yes_outcome: int):
    """Compute gross P&L for a single-contract trade.

    `price` is always the YES mid-price (as stored in the journal).
    For a YES trade you paid `price` cents; for a NO trade you paid
    `100 - price` cents.  P&L is always relative to cost_paid:
        win  → 100 - cost_paid
        lose → -cost_paid
    """
    pnl = calculate_trade_pnl(side, price, yes_outcome, contract_count=1)
    return pnl["won"], pnl["pnl_cents_gross"]


def backtest_journal(journal_path: str) -> pd.DataFrame:
    df = pd.read_csv(journal_path)
    dry = df[df["status"] == "dry_run"].copy()

    results = []
    now = datetime.now(timezone.utc)

    for _, row in dry.iterrows():
        ticker = str(row["ticker"])
        prefix = asset_prefix_from_ticker(ticker)
        contract_count = contract_count_from_row(row)

        base = {
            "ts_utc": row["ts_utc"],
            "ticker": ticker,
            "side": row["side"],
            "price": int(row["price"]),
            "edge_cents": int(row["edge_cents"]),
            "ev_cents": float(_row_value(row, "ev_cents", 0.0)),
            "spread_cents": int(row["spread_cents"]),
            "score": float(row["score"]),
            "reason": row["reason"],
            "contract_count": contract_count,
            "spot": _row_value(row, "spot", ""),
            "strike_signal": _row_value(row, "strike", ""),
            "sigma": _row_value(row, "sigma", ""),
            "d2": _row_value(row, "d2", ""),
            "secs_left": _row_value(row, "secs_left", ""),
            "fair": _row_value(row, "fair", ""),
            "raw_edge": _row_value(row, "raw_edge", ""),
            "momentum_boost": _row_value(row, "momentum_boost", ""),
        }
        base.update(regime_labels_for_trade(row))

        if prefix is None:
            results.append({**base, "status_bt": "error", "error": "unknown asset prefix"})
            continue

        product = COINBASE_PRODUCTS[prefix]

        try:
            _, expiry_time, _ = parse_market_ticker(ticker)
        except Exception as e:
            results.append({**base, "status_bt": "error", "error": f"parse error: {e}"})
            continue

        if expiry_time > now:
            results.append({
                **base,
                "status_bt": "skipped_future",
                "expiry_time": expiry_time.isoformat(),
            })
            continue

        try:
            strike, spot_at_expiry, yes_outcome = resolve_yes_outcome(
                ticker=ticker,
                product=product,
                expiry_time=expiry_time,
            )
            pnl = calculate_trade_pnl(
                side=str(row["side"]),
                yes_price_cents=int(row["price"]),
                yes_outcome=yes_outcome,
                contract_count=contract_count,
            )
            ev_cents_gross = float(base["ev_cents"]) * contract_count
            ev_cents_net = ev_cents_gross - int(pnl["fee_cents"])

            results.append({
                **base,
                "status_bt": "resolved",
                "product": product,
                "expiry_time": expiry_time.isoformat(),
                "strike": strike,
                "spot_at_expiry": spot_at_expiry,
                "yes_outcome": yes_outcome,
                "predicted_side_won": pnl["won"],
                "pnl_cents_gross": pnl["pnl_cents_gross"],
                "fee_cents": pnl["fee_cents"],
                "pnl_cents_net": pnl["pnl_cents_net"],
                "pnl_cents": pnl["pnl_cents_net"],
                "ev_cents_gross": ev_cents_gross,
                "ev_cents_net": ev_cents_net,
            })
        except Exception as e:
            results.append({
                **base,
                "status_bt": "error",
                "product": product,
                "expiry_time": expiry_time.isoformat(),
                "error": str(e),
            })

    return pd.DataFrame(results)


def print_summary(results: pd.DataFrame) -> None:
    print("\n===== BACKTEST STATUS =====")
    print(results["status_bt"].value_counts(dropna=False).to_string())

    resolved = results[results["status_bt"] == "resolved"].copy()
    if resolved.empty:
        print("\nNo trades could be resolved.")
        return

    total = len(resolved)
    wins_net = int((resolved["pnl_cents_net"] > 0).sum())
    losses_net = int((resolved["pnl_cents_net"] <= 0).sum())
    wins_gross = int((resolved["pnl_cents_gross"] > 0).sum())
    print("\n===== BACKTEST SUMMARY =====")
    print(f"Resolved trades: {total}")
    print(f"Net wins:        {wins_net}")
    print(f"Net losses:      {losses_net}")
    print(f"After-fee WR:    {wins_net/total:.2%}")
    print(f"Avg EV net:      {resolved['ev_cents_net'].mean():.2f} cents/trade")
    print(f"Total net P&L:   {resolved['pnl_cents_net'].sum():.1f} cents")
    print(f"Average net P&L: {resolved['pnl_cents_net'].mean():.2f} cents/trade")
    print(f"Gross WR:        {wins_gross/total:.2%}")
    print(f"Total gross P&L: {resolved['pnl_cents_gross'].sum():.1f} cents")
    print(f"Total fees:      {resolved['fee_cents'].sum():.1f} cents")

    print("\n===== BY ASSET =====")
    print(
        _regime_summary_frame(resolved, ["asset"])
        .sort_values("sum", ascending=False)
        .to_string()
    )
    print_regime_summary(resolved)


def _regime_summary_frame(resolved: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    available_cols = [col for col in group_cols if col in resolved.columns]
    if not available_cols:
        return pd.DataFrame()

    grouped = resolved.dropna(subset=available_cols).groupby(available_cols, dropna=False)
    summary = grouped.agg(
        count=("pnl_cents_net", "count"),
        net_wins=("pnl_cents_net", lambda values: int((values > 0).sum())),
        sum=("pnl_cents_net", "sum"),
        mean=("pnl_cents_net", "mean"),
        avg_ev_net=("ev_cents_net", "mean"),
        gross_sum=("pnl_cents_gross", "sum"),
        fees=("fee_cents", "sum"),
    )
    summary["after_fee_wr"] = summary["net_wins"] / summary["count"]
    return summary[
        ["count", "net_wins", "after_fee_wr", "avg_ev_net", "sum", "mean", "gross_sum", "fees"]
    ]


def _print_regime_table(title: str, resolved: pd.DataFrame, group_cols: list[str]) -> None:
    summary = _regime_summary_frame(resolved, group_cols)
    print(f"\n===== {title} =====")
    if summary.empty:
        print("No parseable rows.")
        return
    print(summary.sort_values(["sum", "count"], ascending=[False, False]).to_string())


def print_regime_summary(resolved: pd.DataFrame) -> None:
    _print_regime_table("REGIME: ASSET x HOUR_ET_BAND", resolved, ["asset", "hour_et_band"])
    _print_regime_table("REGIME: SECS_LEFT_BUCKET", resolved, ["secs_left_bucket"])
    _print_regime_table("REGIME: ABS_D2_BUCKET", resolved, ["d2_bucket"])


def main():
    results = backtest_journal(JOURNAL_PATH)
    Path(OUTPUT_PATH).parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(OUTPUT_PATH, index=False)
    print_summary(results)
    print(f"\nSaved detailed results to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
