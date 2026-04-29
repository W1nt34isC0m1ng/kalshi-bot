"""Recovery tool — shadow-settles signals that were blocked by the stale-risk-state bug.

The risk manager's `_reconcile_positions` doesn't run in DRY_RUN mode, so dry-run
"fills" accumulated forever in `risk_state.json` until the portfolio notional
cap saturated and started blocking otherwise-valid signals. This tool reads the
trade journal, finds rows blocked by that bug, dedups them per (ticker, side),
fetches Coinbase settlements, and writes shadow outcomes to a separate file.

Output goes to `logs/recovered_outcomes.csv` — kept distinct from `outcomes.csv`
because these trades did not actually fire. They are useful only for shadow
analysis (validating the strategy as if it had been allowed to run).

Usage:
    python -m kalshi_bot.recover [--journal PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .settle import (
    SETTLE_GRACE_SECONDS,
    compute_outcome,
    coinbase_product_for,
    fetch_settle_price,
    parse_expiry_utc,
    parse_strike,
)

# Substrings that identify the stale-risk-state bug. Other `blocked` reasons
# (e.g. cooldown, max-premium) are legitimate and should not be recovered.
BUG_REASONS = (
    "portfolio notional cap breached",
    "market position cap breached",
    "total notional cap breached",
)

RECOVERED_FIELDS = [
    "first_attempt_ts",
    "ticker",
    "strategy",
    "side",
    "price",
    "qty",
    "strike",
    "expiry_utc",
    "settle_price",
    "outcome",
    "pnl_cents",
    "block_reason",
    "n_attempts",
    "settled_at_utc",
]


def load_recovered_keys(out_path: Path) -> set[tuple[str, str, str]]:
    """Already-recovered (ticker, side, expiry_utc) keys."""
    if not out_path.exists() or out_path.stat().st_size == 0:
        return set()
    keys: set[tuple[str, str, str]] = set()
    with out_path.open(newline="") as f:
        for row in csv.DictReader(f):
            keys.add((row.get("ticker", ""), row.get("side", ""), row.get("expiry_utc", "")))
    return keys


def recover(journal_path: Path, out_path: Path) -> dict:
    if not journal_path.exists():
        logging.error("recover: journal not found at %s", journal_path)
        return {"recovered": 0}

    already = load_recovered_keys(out_path)
    logging.info("recover: %d (ticker,side) already recovered", len(already))

    # First pass: collect all blocked-by-bug rows per (ticker, side), tracking
    # first attempt and total attempt count.
    grouped: dict[tuple[str, str], dict] = {}
    with journal_path.open(newline="") as f:
        for row in csv.DictReader(f):
            if row.get("status") != "blocked":
                continue
            reason = (row.get("status_reason") or "").lower()
            if not any(b in reason for b in BUG_REASONS):
                continue
            ticker = row.get("ticker", "")
            side = row.get("side", "")
            if not ticker or not side:
                continue
            key = (ticker, side)
            if key not in grouped:
                grouped[key] = {"first": row, "n": 0}
            grouped[key]["n"] += 1

    logging.info("recover: %d unique blocked (ticker,side) signals to evaluate", len(grouped))

    now_utc = datetime.now(timezone.utc)
    new_rows: list[dict] = []
    skipped_unexpired = 0
    skipped_already = 0
    errors = 0

    for (ticker, side), payload in grouped.items():
        row = payload["first"]
        n = payload["n"]

        expiry = parse_expiry_utc(ticker)
        if expiry is None:
            errors += 1
            continue

        if (ticker, side, expiry.isoformat()) in already:
            skipped_already += 1
            continue

        if (now_utc - expiry).total_seconds() < SETTLE_GRACE_SECONDS:
            skipped_unexpired += 1
            continue

        strike = parse_strike(row.get("reason", ""))
        if strike is None:
            errors += 1
            continue

        try:
            yes_price = int(row.get("price", "") or 0)
            qty = int(row.get("requested_count", "") or 0)
        except ValueError:
            errors += 1
            continue

        if yes_price <= 0 or qty <= 0:
            errors += 1
            continue

        product = coinbase_product_for(ticker)
        if product is None:
            errors += 1
            continue

        settle_price = fetch_settle_price(product, expiry)
        if settle_price is None:
            errors += 1
            continue

        outcome, pnl = compute_outcome(side, yes_price, qty, strike, settle_price)

        new_rows.append({
            "first_attempt_ts": row.get("ts_utc", ""),
            "ticker": ticker,
            "strategy": row.get("strategy", ""),
            "side": side,
            "price": yes_price,
            "qty": qty,
            "strike": strike,
            "expiry_utc": expiry.isoformat(),
            "settle_price": settle_price,
            "outcome": outcome,
            "pnl_cents": pnl,
            "block_reason": row.get("status_reason", ""),
            "n_attempts": n,
            "settled_at_utc": now_utc.isoformat(),
        })

    if new_rows:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not out_path.exists() or out_path.stat().st_size == 0
        with out_path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=RECOVERED_FIELDS, extrasaction="ignore")
            if new_file:
                writer.writeheader()
            for r in new_rows:
                writer.writerow(r)

    wins = sum(1 for r in new_rows if r["outcome"] == "win")
    losses = sum(1 for r in new_rows if r["outcome"] == "loss")
    pnl = sum(r["pnl_cents"] for r in new_rows)

    logging.info(
        "recover: shadow-settled=%d wins=%d losses=%d shadow_pnl=%+dc skipped(already=%d, unexpired=%d) errors=%d",
        len(new_rows), wins, losses, pnl, skipped_already, skipped_unexpired, errors,
    )

    return {
        "recovered": len(new_rows),
        "wins": wins,
        "losses": losses,
        "pnl_cents": pnl,
        "skipped_already": skipped_already,
        "skipped_unexpired": skipped_unexpired,
        "errors": errors,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Shadow-settle blocked signals from the stale-risk-state bug."
    )
    p.add_argument("--journal", default="logs/trade_journal.csv")
    p.add_argument("--out", default="logs/recovered_outcomes.csv")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    s = recover(Path(args.journal), Path(args.out))
    print(
        f"Shadow-recovered {s['recovered']} signals: "
        f"{s['wins']}W-{s['losses']}L  "
        f"shadow_pnl={s['pnl_cents']:+}c (${s['pnl_cents']/100:+.2f})"
    )
    if s["errors"]:
        print(f"  errors: {s['errors']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
