"""Outcome settler — closes the loop on the trade journal.

Reads trade_journal.csv, finds rows whose option expiry has passed and which
haven't been settled yet, fetches the BTC settlement price from Coinbase, and
appends the result to logs/outcomes.csv.

Append-only by design: never modifies the trade journal. Idempotent — re-running
no-ops on already-settled trades. Run via:

    python -m kalshi_bot.settle [--journal PATH] [--outcomes PATH]

The two files are joinable on (ts_utc, ticker).
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


# ------------------------------------------------------------------ #
# Configuration                                                       #
# ------------------------------------------------------------------ #

# Statuses that represent a position with real exposure (paper or live).
# Anything else (cooldown, blocked, shadow_*, etc.) didn't actually trade.
SETTLEABLE_STATUSES = {"dry_run", "filled", "accepted"}

# Eastern (Kalshi market) time zone offset relative to UTC for ticker parsing.
# Kalshi ticker timestamps are in EDT/EST. We use a fixed -4 offset (EDT);
# when DST ends, this becomes a 1-hour bug — fix at that point if still relevant.
KALSHI_TZ = timezone(timedelta(hours=-4))

# Margin after expiry before we'll attempt to settle (gives Coinbase time to
# publish the closing minute candle).
SETTLE_GRACE_SECONDS = 90

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


OUTCOME_FIELDS = [
    "trade_ts_utc",
    "ticker",
    "strategy",
    "side",
    "price",
    "qty",
    "strike",
    "expiry_utc",
    "settle_price",
    "outcome",        # win | loss | unsettled_error
    "pnl_cents",
    "settled_at_utc",
]


# ------------------------------------------------------------------ #
# Ticker parsing                                                      #
# ------------------------------------------------------------------ #

_TICKER_RE = re.compile(r"^KX(BTC|ETH)15M-(\d{2})(\w{3})(\d{2})(\d{2})(\d{2})-")


def parse_expiry_utc(ticker: str) -> datetime | None:
    """Parse the option expiry from a Kalshi 15-min ticker.

    Format: ``KX{ASSET}15M-{YY}{MMM}{DD}{HH}{MM}-{STRIKE_CODE}``
    Date/time is in Eastern (Kalshi) time. Returns timezone-aware UTC datetime
    or None if the ticker doesn't match the expected shape.
    """
    m = _TICKER_RE.match(ticker.upper() if ticker else "")
    if not m:
        return None
    _asset, yy, mmm, dd, hh, mn = m.groups()
    if mmm not in MONTHS:
        return None
    try:
        et = datetime(
            year=2000 + int(yy),
            month=MONTHS[mmm],
            day=int(dd),
            hour=int(hh),
            minute=int(mn),
            tzinfo=KALSHI_TZ,
        )
    except ValueError:
        return None
    return et.astimezone(timezone.utc)


def coinbase_product_for(ticker: str) -> str | None:
    if ticker.upper().startswith("KXBTC15M"):
        return "BTC-USD"
    if ticker.upper().startswith("KXETH15M"):
        return "ETH-USD"
    return None


# ------------------------------------------------------------------ #
# Coinbase settlement fetch                                           #
# ------------------------------------------------------------------ #

_settle_cache: dict[tuple[str, datetime], float] = {}


def fetch_settle_price(product: str, expiry_utc: datetime) -> float | None:
    """Fetch the 1-minute close at the expiry minute. Cached per (product, minute)."""
    minute = expiry_utc.replace(second=0, microsecond=0)
    key = (product, minute)
    if key in _settle_cache:
        return _settle_cache[key]

    start = int(minute.timestamp()) - 60
    end = int(minute.timestamp()) + 60
    url = f"https://api.exchange.coinbase.com/products/{product}/candles"
    headers = {"User-Agent": "kalshi-bot-settler/1.0"}

    for attempt in range(3):
        try:
            resp = requests.get(
                url,
                params={"start": start, "end": end, "granularity": 60},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            candles = resp.json()
            for c in candles:
                ts = datetime.fromtimestamp(c[0], timezone.utc)
                if ts.replace(second=0, microsecond=0) == minute:
                    price = float(c[4])
                    _settle_cache[key] = price
                    return price
            return None  # candle for that minute not available
        except Exception as exc:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            logging.warning("settle: coinbase fetch failed for %s @ %s: %s", product, minute, exc)
            return None
    return None


# ------------------------------------------------------------------ #
# Outcome computation                                                 #
# ------------------------------------------------------------------ #

def compute_outcome(side: str, yes_price: int, qty: int, strike: float, settle_price: float):
    """Returns (outcome_str, pnl_cents).

    Kalshi binary: YES wins if settle > strike. Premium per contract:
      - YES: yes_price (paying for the YES claim)
      - NO:  100 - yes_price (paying for the NO claim)
    Win pays 100 cents per contract; loss forfeits the premium.
    """
    yes_wins = settle_price > strike
    win = (side == "yes" and yes_wins) or (side == "no" and not yes_wins)

    if side == "yes":
        premium = yes_price * qty
        pnl = (100 - yes_price) * qty if win else -premium
    else:
        premium = (100 - yes_price) * qty
        pnl = yes_price * qty if win else -premium

    return ("win" if win else "loss"), pnl


# ------------------------------------------------------------------ #
# Journal/outcomes IO                                                 #
# ------------------------------------------------------------------ #

_STRIKE_RE = re.compile(r"strike=([\d.]+)")


def parse_strike(reason: str) -> float | None:
    if not reason:
        return None
    m = _STRIKE_RE.search(reason)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def load_settled_keys(outcomes_path: Path) -> set[tuple[str, str]]:
    """Set of (trade_ts_utc, ticker) already in outcomes.csv."""
    if not outcomes_path.exists() or outcomes_path.stat().st_size == 0:
        return set()
    keys: set[tuple[str, str]] = set()
    with outcomes_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("trade_ts_utc", "")
            ticker = row.get("ticker", "")
            if ts and ticker:
                keys.add((ts, ticker))
    return keys


def append_outcomes(outcomes_path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    new_file = not outcomes_path.exists() or outcomes_path.stat().st_size == 0
    outcomes_path.parent.mkdir(parents=True, exist_ok=True)
    with outcomes_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTCOME_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ------------------------------------------------------------------ #
# Main settler                                                        #
# ------------------------------------------------------------------ #

def settle(journal_path: Path, outcomes_path: Path) -> dict:
    if not journal_path.exists():
        logging.error("settle: journal not found at %s", journal_path)
        return {"settled": 0, "wins": 0, "losses": 0, "pnl_cents": 0, "skipped": 0, "errors": 0}

    already_settled = load_settled_keys(outcomes_path)
    logging.info("settle: %d trades already settled", len(already_settled))

    now_utc = datetime.now(timezone.utc)
    grace = timedelta(seconds=SETTLE_GRACE_SECONDS)

    new_rows: list[dict] = []
    skipped_unexpired = 0
    skipped_unsettleable = 0
    skipped_already = 0
    errors = 0

    with journal_path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("ts_utc", "")
            ticker = row.get("ticker", "")
            status = row.get("status", "")
            if not ts or not ticker:
                continue

            # Skip marker rows / non-settleable statuses
            if status not in SETTLEABLE_STATUSES:
                skipped_unsettleable += 1
                continue

            if (ts, ticker) in already_settled:
                skipped_already += 1
                continue

            expiry = parse_expiry_utc(ticker)
            if expiry is None:
                logging.debug("settle: could not parse expiry from %s", ticker)
                errors += 1
                continue

            if now_utc < expiry + grace:
                skipped_unexpired += 1
                continue

            strike = parse_strike(row.get("reason", ""))
            if strike is None:
                logging.warning("settle: missing strike for %s @ %s", ticker, ts)
                errors += 1
                continue

            try:
                yes_price = int(row.get("price", "") or 0)
                qty = int(row.get("requested_count", "") or 0)
            except ValueError:
                errors += 1
                continue

            if qty <= 0 or yes_price <= 0:
                # No real position — log a row marking unsettled_error so we don't retry.
                qty = qty or 0
                yes_price = yes_price or 0

            product = coinbase_product_for(ticker)
            if product is None:
                errors += 1
                continue

            settle_price = fetch_settle_price(product, expiry)
            if settle_price is None:
                logging.warning("settle: no settle price for %s @ %s", ticker, expiry.isoformat())
                errors += 1
                continue

            if qty <= 0:
                outcome, pnl = "unsettled_error", 0
            else:
                outcome, pnl = compute_outcome(row.get("side", ""), yes_price, qty, strike, settle_price)

            new_rows.append({
                "trade_ts_utc": ts,
                "ticker": ticker,
                "strategy": row.get("strategy", ""),
                "side": row.get("side", ""),
                "price": yes_price,
                "qty": qty,
                "strike": strike,
                "expiry_utc": expiry.isoformat(),
                "settle_price": settle_price,
                "outcome": outcome,
                "pnl_cents": pnl,
                "settled_at_utc": now_utc.isoformat(),
            })

    append_outcomes(outcomes_path, new_rows)

    wins = sum(1 for r in new_rows if r["outcome"] == "win")
    losses = sum(1 for r in new_rows if r["outcome"] == "loss")
    pnl = sum(r["pnl_cents"] for r in new_rows if isinstance(r["pnl_cents"], int))

    logging.info(
        "settle: settled=%d wins=%d losses=%d pnl=%+dc skipped(already=%d, unexpired=%d, non-trade=%d) errors=%d",
        len(new_rows), wins, losses, pnl,
        skipped_already, skipped_unexpired, skipped_unsettleable, errors,
    )

    return {
        "settled": len(new_rows),
        "wins": wins,
        "losses": losses,
        "pnl_cents": pnl,
        "skipped_already": skipped_already,
        "skipped_unexpired": skipped_unexpired,
        "skipped_non_trade": skipped_unsettleable,
        "errors": errors,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Settle trade outcomes from Coinbase BTC closes.")
    p.add_argument("--journal", default="logs/trade_journal.csv", help="Path to trade journal CSV")
    p.add_argument("--outcomes", default="logs/outcomes.csv", help="Path to outcomes CSV (append)")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    summary = settle(Path(args.journal), Path(args.outcomes))
    print(
        f"Settled {summary['settled']} new trade(s): "
        f"{summary['wins']}W-{summary['losses']}L  "
        f"pnl={summary['pnl_cents']:+}c (${summary['pnl_cents']/100:+.2f})"
    )
    if summary["errors"]:
        print(f"  errors: {summary['errors']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
