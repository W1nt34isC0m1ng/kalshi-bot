from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import mean
from urllib.parse import parse_qs, urlparse

from src.kalshi_bot.config import Settings
from src.kalshi_bot.main import build_clients


REPO_ROOT = Path(__file__).resolve().parent
LOGS_DIR = REPO_ROOT / "logs"
DEFAULT_PORT = 8765
MOMENTUM_RE = re.compile(r"momentum_boost=([0-9.]+)")
CONF_RE = re.compile(r"conf=([0-9.]+)")
REALIZED_STATUSES = {"sent", "dry_run", "shadow_no"}
MARKET_RESULT_CACHE: dict[str, str] = {}
_CLIENTS = build_clients(Settings())
MARKET_CLIENT = _CLIENTS[1] or _CLIENTS[0]


def _iso_to_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _safe_float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _reason_metric(reason: str, pattern: re.Pattern[str]) -> float | None:
    match = pattern.search(reason or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def available_journal_files() -> list[Path]:
    files = sorted(LOGS_DIR.glob("*.csv"))
    if (LOGS_DIR / "trade_journal.csv") in files:
        main = LOGS_DIR / "trade_journal.csv"
        files.remove(main)
        files.insert(0, main)
    return files


def resolve_journal_file(name: str | None) -> Path:
    files = available_journal_files()
    if not files:
        raise FileNotFoundError("No journal CSV files found in logs/")
    if not name:
        return files[0]
    for path in files:
        if path.name == name:
            return path
    raise FileNotFoundError(f"Unknown journal file: {name}")


def load_rows(path: Path) -> list[dict]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            row = dict(row)
            row.setdefault("strategy", "")
            row.setdefault("status_reason", "")
            row.setdefault("order_id", "")
            row.setdefault("filled_count", "")
            row.setdefault("ev_cents", "")
            row.setdefault("ev_roi", "")
            row.setdefault("requested_count", "")
            row.setdefault("premium_cents_per_contract", "")
            row.setdefault("notional_cents", "")
            row["_ts"] = _iso_to_dt(row["ts_utc"])
            row["_score"] = _safe_float(row.get("score"))
            row["_price"] = _safe_float(row.get("price"))
            row["_edge"] = _safe_float(row.get("edge_cents"))
            row["_ev_cents"] = _safe_float(row.get("ev_cents"))
            row["_ev_roi"] = _safe_float(row.get("ev_roi"))
            row["_spread"] = _safe_float(row.get("spread_cents"))
            row["_momentum"] = _reason_metric(row.get("reason", ""), MOMENTUM_RE)
            row["_conf"] = _reason_metric(row.get("reason", ""), CONF_RE)
            rows.append(row)
    return rows


def _selected_values(params: dict[str, list[str]], key: str) -> set[str]:
    values = {value.lower() for value in params.get(key, []) if value}
    if not values or "all" in values:
        return set()
    return values


def filter_rows(rows: list[dict], params: dict[str, list[str]]) -> list[dict]:
    statuses = _selected_values(params, "status")
    sides = _selected_values(params, "side")
    strategy = params.get("strategy", ["all"])[0] or "all"
    ticker_text = (params.get("ticker", [""])[0] or "").strip().upper()
    days_raw = (params.get("days", [""])[0] or "").strip()
    min_momentum_raw = (params.get("min_momentum", [""])[0] or "").strip()
    max_momentum_raw = (params.get("max_momentum", [""])[0] or "").strip()
    min_score_raw = (params.get("min_score", [""])[0] or "").strip()
    max_score_raw = (params.get("max_score", [""])[0] or "").strip()
    min_conf_raw = (params.get("min_conf", [""])[0] or "").strip()
    max_conf_raw = (params.get("max_conf", [""])[0] or "").strip()
    min_hour_raw = (params.get("min_hour", [""])[0] or "").strip()
    max_hour_raw = (params.get("max_hour", [""])[0] or "").strip()

    filtered = rows
    if statuses:
        filtered = [row for row in filtered if row.get("status", "").lower() in statuses]
    if sides:
        filtered = [row for row in filtered if row.get("side", "").lower() in sides]
    if strategy != "all":
        filtered = [row for row in filtered if (row.get("strategy") or "") == strategy]
    if ticker_text:
        filtered = [row for row in filtered if ticker_text in row.get("ticker", "").upper()]
    if min_momentum_raw:
        try:
            min_momentum = float(min_momentum_raw)
            filtered = [row for row in filtered if (row["_momentum"] or 0.0) >= min_momentum]
        except ValueError:
            pass
    if max_momentum_raw:
        try:
            max_momentum = float(max_momentum_raw)
            filtered = [row for row in filtered if (row["_momentum"] or 0.0) <= max_momentum]
        except ValueError:
            pass
    if min_score_raw:
        try:
            min_score = float(min_score_raw)
            filtered = [row for row in filtered if row["_score"] >= min_score]
        except ValueError:
            pass
    if max_score_raw:
        try:
            max_score = float(max_score_raw)
            filtered = [row for row in filtered if row["_score"] <= max_score]
        except ValueError:
            pass
    if min_conf_raw:
        try:
            min_conf = float(min_conf_raw)
            filtered = [row for row in filtered if (row["_conf"] or 0.0) >= min_conf]
        except ValueError:
            pass
    if max_conf_raw:
        try:
            max_conf = float(max_conf_raw)
            filtered = [row for row in filtered if (row["_conf"] or 0.0) <= max_conf]
        except ValueError:
            pass
    if min_hour_raw:
        try:
            min_hour = int(min_hour_raw)
            filtered = [row for row in filtered if row["_ts"].hour >= min_hour]
        except ValueError:
            pass
    if max_hour_raw:
        try:
            max_hour = int(max_hour_raw)
            filtered = [row for row in filtered if row["_ts"].hour <= max_hour]
        except ValueError:
            pass
    if days_raw:
        try:
            days = max(1, int(days_raw))
        except ValueError:
            days = 0
        if days:
            max_ts = max((row["_ts"] for row in filtered), default=None)
            if max_ts is not None:
                cutoff = max_ts.timestamp() - (days * 86400)
                filtered = [row for row in filtered if row["_ts"].timestamp() >= cutoff]
    return filtered


def _bucket_score(score: float) -> str:
    for low, high in [(0, 5), (5, 8), (8, 12), (12, 20), (20, 999)]:
        if low <= score < high:
            hi = "+" if high == 999 else str(high)
            return f"{low}-{hi}"
    return "unknown"


def _bucket_momentum(momentum: float | None) -> str:
    value = momentum or 0.0
    for low, high in [(0.0, 0.01), (0.01, 0.02), (0.02, 0.03), (0.03, 0.05), (0.05, 999)]:
        if low <= value < high:
            hi = "+" if high == 999 else f"{high:.2f}"
            return f"{low:.2f}-{hi}"
    return "unknown"


def _market_result_for_ticker(ticker: str) -> str:
    cached = MARKET_RESULT_CACHE.get(ticker)
    if cached is not None:
        return cached
    if MARKET_CLIENT is None:
        MARKET_RESULT_CACHE[ticker] = ""
        return ""
    try:
        response = MARKET_CLIENT.get_market(ticker)
        market = response.get("market", response) if isinstance(response, dict) else {}
        result = str(market.get("result", "") or "").lower()
    except Exception:
        result = ""
    MARKET_RESULT_CACHE[ticker] = result
    return result


def build_realized_metrics(rows: list[dict]) -> dict:
    realized_rows = []
    unresolved = 0
    for row in rows:
        status = (row.get("status") or "").lower()
        if status not in REALIZED_STATUSES:
            continue
        result = _market_result_for_ticker(row.get("ticker", ""))
        if result not in {"yes", "no"}:
            unresolved += 1
            continue

        side = (row.get("side") or "").lower()
        yes_price = row["_price"]
        premium = yes_price if side == "yes" else (100 - yes_price)

        if side == result == "yes":
            pnl_cents = 100 - yes_price
        elif side == result == "no":
            pnl_cents = yes_price
        else:
            pnl_cents = -premium

        realized_rows.append(
            {
                "day": row["_ts"].date().isoformat(),
                "side": side,
                "premium": premium / 100.0,
                "pnl": pnl_cents / 100.0,
                "won": pnl_cents > 0,
            }
        )

    wins = sum(1 for row in realized_rows if row["won"])
    spent = sum(row["premium"] for row in realized_rows)
    pnl_total = sum(row["pnl"] for row in realized_rows)

    by_side: dict[str, dict] = {}
    for side in ["yes", "no"]:
        side_rows = [row for row in realized_rows if row["side"] == side]
        side_spent = sum(row["premium"] for row in side_rows)
        side_pnl = sum(row["pnl"] for row in side_rows)
        side_wins = sum(1 for row in side_rows if row["won"])
        by_side[side] = {
            "rows": len(side_rows),
            "win_rate": round(side_wins / len(side_rows), 4) if side_rows else 0.0,
            "pnl": round(side_pnl, 2),
            "roi": round(side_pnl / side_spent, 4) if side_spent else 0.0,
        }

    by_day: dict[str, dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    for row in realized_rows:
        by_day[row["day"]]["pnl"] += row["pnl"]
        by_day[row["day"]]["trades"] += 1

    return {
        "rows": len(realized_rows),
        "unresolved": unresolved,
        "wins": wins,
        "losses": len(realized_rows) - wins,
        "win_rate": round(wins / len(realized_rows), 4) if realized_rows else 0.0,
        "pnl": round(pnl_total, 2),
        "avg_pnl": round(pnl_total / len(realized_rows), 4) if realized_rows else 0.0,
        "roi": round(pnl_total / spent, 4) if spent else 0.0,
        "spent": round(spent, 2),
        "by_side": by_side,
        "by_day": [{"day": day, **values} for day, values in sorted(by_day.items())],
    }


def build_dashboard(rows: list[dict], file_name: str) -> dict:
    status_counts = Counter(row.get("status", "") or "unknown" for row in rows)
    side_counts = Counter(row.get("side", "") or "unknown" for row in rows)
    strategy_counts = Counter((row.get("strategy") or "(blank)") for row in rows)
    ticker_counts = Counter(row.get("ticker", "") for row in rows)
    score_buckets = Counter(_bucket_score(row["_score"]) for row in rows)
    momentum_buckets = Counter(_bucket_momentum(row["_momentum"]) for row in rows)

    by_day_status: dict[str, Counter] = defaultdict(Counter)
    by_day_side: dict[str, Counter] = defaultdict(Counter)
    by_hour_status: dict[int, Counter] = defaultdict(Counter)

    for row in rows:
        day = row["_ts"].date().isoformat()
        hour = row["_ts"].hour
        by_day_status[day][row.get("status", "unknown")] += 1
        by_day_side[day][row.get("side", "unknown")] += 1
        by_hour_status[hour][row.get("status", "unknown")] += 1

    avg_score = mean([row["_score"] for row in rows]) if rows else 0.0
    avg_price = mean([row["_price"] for row in rows]) if rows else 0.0
    avg_edge = mean([row["_edge"] for row in rows]) if rows else 0.0
    avg_ev_cents = mean([row["_ev_cents"] for row in rows]) if rows else 0.0
    avg_ev_roi = mean([row["_ev_roi"] for row in rows]) if rows else 0.0
    avg_spread = mean([row["_spread"] for row in rows]) if rows else 0.0
    momentum_values = [row["_momentum"] for row in rows if row["_momentum"] is not None]
    conf_values = [row["_conf"] for row in rows if row["_conf"] is not None]
    realized = build_realized_metrics(rows)

    recent_rows = []
    for row in sorted(rows, key=lambda item: item["_ts"], reverse=True)[:40]:
        recent_rows.append(
            {
                "ts_utc": row["ts_utc"],
                "strategy": row.get("strategy", ""),
                "ticker": row.get("ticker", ""),
                "side": row.get("side", ""),
                "price": row.get("price", ""),
                "ev_cents": row.get("ev_cents", ""),
                "ev_roi": row.get("ev_roi", ""),
                "requested_count": row.get("requested_count", ""),
                "premium_cents_per_contract": row.get("premium_cents_per_contract", ""),
                "notional_cents": row.get("notional_cents", ""),
                "score": row.get("score", ""),
                "status": row.get("status", ""),
                "status_reason": row.get("status_reason", ""),
            }
        )

    scatter_points = []
    for row in sorted(rows, key=lambda item: item["_ts"], reverse=True)[:800]:
        scatter_points.append(
            {
                "x": row["_price"],
                "y": row["_score"],
                "status": row.get("status", ""),
                "ticker": row.get("ticker", ""),
                "side": row.get("side", ""),
                "momentum": row["_momentum"] or 0.0,
                "conf": row["_conf"] or 0.0,
            }
        )

    return {
        "file_name": file_name,
        "summary": {
            "rows": len(rows),
            "start": min((row["ts_utc"] for row in rows), default=""),
            "end": max((row["ts_utc"] for row in rows), default=""),
            "avg_score": round(avg_score, 2),
            "avg_price": round(avg_price, 2),
                "avg_edge": round(avg_edge, 2),
                "avg_ev_cents": round(avg_ev_cents, 2),
                "avg_ev_roi": round(avg_ev_roi * 100, 2),
                "avg_spread": round(avg_spread, 2),
            "avg_momentum": round(mean(momentum_values), 4) if momentum_values else 0.0,
            "avg_conf": round(mean(conf_values), 4) if conf_values else 0.0,
        },
        "counts": {
            "status": dict(status_counts),
            "side": dict(side_counts),
            "strategy": dict(strategy_counts),
        },
        "realized": realized,
        "charts": {
            "status_by_day": [
                {"day": day, **counts} for day, counts in sorted(by_day_status.items())
            ],
            "side_by_day": [
                {"day": day, **counts} for day, counts in sorted(by_day_side.items())
            ],
            "hourly_status": [
                {"hour": hour, **counts} for hour, counts in sorted(by_hour_status.items())
            ],
            "score_buckets": [{"bucket": bucket, "count": count} for bucket, count in score_buckets.items()],
            "momentum_buckets": [
                {"bucket": bucket, "count": count} for bucket, count in momentum_buckets.items()
            ],
            "top_tickers": [
                {"ticker": ticker, "count": count}
                for ticker, count in ticker_counts.most_common(12)
            ],
            "scatter": scatter_points,
        },
        "recent_rows": recent_rows,
    }


HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kalshi Journal Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #f4f0e8;
      --panel: #fffaf2;
      --ink: #182126;
      --muted: #6b6f72;
      --line: #d8ccb8;
      --accent: #0b6e4f;
      --accent-2: #d17b0f;
      --danger: #b63720;
      --shadow: 0 18px 40px rgba(24, 33, 38, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top right, rgba(209, 123, 15, 0.14), transparent 28%),
        radial-gradient(circle at top left, rgba(11, 110, 79, 0.12), transparent 32%),
        var(--bg);
    }
    .shell {
      max-width: 1440px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }
    .hero {
      display: grid;
      grid-template-columns: 1.3fr 1fr;
      gap: 18px;
      margin-bottom: 20px;
    }
    .hero-card, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
    }
    .hero-card {
      padding: 28px;
      min-height: 180px;
    }
    h1 {
      margin: 0 0 10px;
      font-size: 36px;
      line-height: 1.05;
      letter-spacing: -0.03em;
    }
    .sub {
      color: var(--muted);
      max-width: 70ch;
      line-height: 1.5;
    }
    .hero-stats {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-top: 22px;
    }
    .hero-stat {
      padding: 16px;
      border-radius: 18px;
      background: rgba(24, 33, 38, 0.04);
    }
    .hero-stat .label, .mini .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .hero-stat .value, .mini .value {
      margin-top: 6px;
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.03em;
    }
    .filters {
      padding: 20px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-content: start;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .field label {
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .field input, .field select {
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: #fff;
      font: inherit;
      color: var(--ink);
    }
    .chip-group {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding-top: 2px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 9px 12px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      cursor: pointer;
      font-size: 13px;
    }
    .chip input {
      margin: 0;
    }
    .field.full { grid-column: 1 / -1; }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 18px;
    }
    .span-3 { grid-column: span 3; }
    .span-4 { grid-column: span 4; }
    .span-6 { grid-column: span 6; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    .panel {
      padding: 20px;
      overflow: hidden;
    }
    .panel h2 {
      margin: 0 0 6px;
      font-size: 18px;
    }
    .panel .caption {
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 16px;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
    }
    .mini {
      padding: 16px;
      border: 1px solid rgba(24, 33, 38, 0.08);
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.75);
    }
    .canvas-wrap {
      min-height: 290px;
    }
    canvas {
      width: 100% !important;
      height: 290px !important;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    th, td {
      padding: 10px 8px;
      border-bottom: 1px solid rgba(24, 33, 38, 0.08);
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .status-pill {
      display: inline-flex;
      padding: 4px 9px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      background: rgba(24, 33, 38, 0.08);
    }
    .status-sent { background: rgba(11, 110, 79, 0.14); color: var(--accent); }
    .status-error { background: rgba(182, 55, 32, 0.14); color: var(--danger); }
    .status-shadow_no { background: rgba(209, 123, 15, 0.14); color: var(--accent-2); }
    .status-cooldown { background: rgba(24, 33, 38, 0.08); color: var(--ink); }
    .status-dry_run { background: rgba(31, 74, 125, 0.14); color: #1f4a7d; }
    .status-blocked { background: rgba(99, 60, 146, 0.14); color: #633c92; }
    .footer-note {
      margin-top: 14px;
      color: var(--muted);
      font-size: 12px;
    }
    @media (max-width: 1080px) {
      .hero, .metric-grid, .grid { grid-template-columns: repeat(1, minmax(0, 1fr)); }
      .span-3, .span-4, .span-6, .span-8, .span-12 { grid-column: span 1; }
      .hero-stats { grid-template-columns: repeat(1, minmax(0, 1fr)); }
      .filters { grid-template-columns: repeat(1, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="hero-card">
        <h1>Kalshi Journal Dashboard</h1>
        <div class="sub">
          Local dashboard for the trade journals. Switch files, filter rows, and inspect how signals,
          statuses, and trade flow shift over time without touching the live bot.
        </div>
        <div class="hero-stats">
          <div class="hero-stat"><div class="label">Rows</div><div class="value" id="hero-rows">-</div></div>
          <div class="hero-stat"><div class="label">Date Range</div><div class="value" id="hero-range">-</div></div>
          <div class="hero-stat"><div class="label">Active File</div><div class="value" id="hero-file">-</div></div>
        </div>
      </div>
      <div class="hero-card filters">
        <div class="field full">
          <label for="file">Journal file</label>
          <select id="file"></select>
        </div>
        <div class="field">
          <label>Status</label>
          <div id="status-group" class="chip-group"></div>
        </div>
        <div class="field">
          <label>Side</label>
          <div id="side-group" class="chip-group"></div>
        </div>
        <div class="field">
          <label for="strategy">Strategy</label>
          <select id="strategy">
            <option value="all">All</option>
          </select>
        </div>
        <div class="field">
          <label for="days">Trailing days</label>
          <input id="days" type="number" min="1" step="1" placeholder="all">
        </div>
        <div class="field">
          <label for="min_momentum">Min momentum</label>
          <input id="min_momentum" type="number" min="0" step="0.01" placeholder="0.00">
        </div>
        <div class="field">
          <label for="max_momentum">Max momentum</label>
          <input id="max_momentum" type="number" min="0" step="0.01" placeholder="any">
        </div>
        <div class="field">
          <label for="min_score">Min score</label>
          <input id="min_score" type="number" step="0.1" placeholder="any">
        </div>
        <div class="field">
          <label for="max_score">Max score</label>
          <input id="max_score" type="number" step="0.1" placeholder="any">
        </div>
        <div class="field">
          <label for="min_conf">Min confidence</label>
          <input id="min_conf" type="number" min="0" max="1" step="0.01" placeholder="0.00">
        </div>
        <div class="field">
          <label for="max_conf">Max confidence</label>
          <input id="max_conf" type="number" min="0" max="1" step="0.01" placeholder="1.00">
        </div>
        <div class="field">
          <label for="min_hour">Min UTC hour</label>
          <input id="min_hour" type="number" min="0" max="23" step="1" placeholder="0">
        </div>
        <div class="field">
          <label for="max_hour">Max UTC hour</label>
          <input id="max_hour" type="number" min="0" max="23" step="1" placeholder="23">
        </div>
        <div class="field full">
          <label for="ticker">Ticker contains</label>
          <input id="ticker" type="text" placeholder="KXBTC15M">
        </div>
      </div>
    </section>

    <section class="panel span-12" style="margin-bottom: 18px;">
      <h2>Quick Read</h2>
      <div class="caption">Top-line metrics for the current file and filter set.</div>
      <div class="metric-grid" id="metric-grid"></div>
    </section>

    <section class="panel span-12" style="margin-bottom: 18px;">
      <h2>Realized P&amp;L</h2>
      <div class="caption">Resolved per-contract results for `sent`, `dry_run`, and `shadow_no` only. This section ignores cooldowns, errors, and blocks even if you leave them visible elsewhere.</div>
      <div class="metric-grid" id="realized-grid"></div>
    </section>

    <section class="grid">
      <div class="panel span-8">
        <h2>Status Flow By Day</h2>
        <div class="caption">Stacked day view of journal statuses.</div>
        <div class="canvas-wrap"><canvas id="statusByDay"></canvas></div>
      </div>
      <div class="panel span-4">
        <h2>Status Mix</h2>
        <div class="caption">Current filtered status breakdown.</div>
        <div class="canvas-wrap"><canvas id="statusMix"></canvas></div>
      </div>

      <div class="panel span-6">
        <h2>Side Flow By Day</h2>
        <div class="caption">How YES and NO activity moved over time.</div>
        <div class="canvas-wrap"><canvas id="sideByDay"></canvas></div>
      </div>
      <div class="panel span-6">
        <h2>Hourly Status Pattern</h2>
        <div class="caption">Activity distribution by UTC hour.</div>
        <div class="canvas-wrap"><canvas id="hourlyStatus"></canvas></div>
      </div>

      <div class="panel span-6">
        <h2>Realized P&amp;L By Day</h2>
        <div class="caption">Daily realized per-contract P&amp;L in the current slice.</div>
        <div class="canvas-wrap"><canvas id="realizedPnlByDay"></canvas></div>
      </div>
      <div class="panel span-6">
        <h2>YES vs NO Realized</h2>
        <div class="caption">Side-by-side realized comparison for the filtered slice.</div>
        <div class="canvas-wrap"><canvas id="realizedBySide"></canvas></div>
      </div>

      <div class="panel span-4">
        <h2>Top Tickers</h2>
        <div class="caption">Most active tickers in the current slice.</div>
        <div class="canvas-wrap"><canvas id="topTickers"></canvas></div>
      </div>
      <div class="panel span-4">
        <h2>Score Buckets</h2>
        <div class="caption">How signals cluster by score.</div>
        <div class="canvas-wrap"><canvas id="scoreBuckets"></canvas></div>
      </div>
      <div class="panel span-4">
        <h2>Momentum Buckets</h2>
        <div class="caption">Momentum extracted from the reason field.</div>
        <div class="canvas-wrap"><canvas id="momentumBuckets"></canvas></div>
      </div>

      <div class="panel span-12">
        <h2>Price vs Score</h2>
        <div class="caption">Most recent points in the filtered set. Color shows status.</div>
        <div class="canvas-wrap"><canvas id="priceScoreScatter"></canvas></div>
      </div>

      <div class="panel span-12">
        <h2>Recent Rows</h2>
        <div class="caption">Latest rows in the current slice.</div>
        <div style="overflow:auto;">
          <table>
            <thead>
              <tr>
                <th>UTC</th>
                <th>Strategy</th>
                <th>Ticker</th>
                <th>Side</th>
                <th>Price</th>
                <th>EV (c)</th>
                <th>EV ROI</th>
                <th>Count</th>
                <th>Notional</th>
                <th>Score</th>
                <th>Status</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody id="recentRows"></tbody>
          </table>
        </div>
        <div class="footer-note">The dashboard reads CSVs live each refresh. It does not mutate bot state.</div>
      </div>
    </section>
  </div>

  <script>
    const charts = {};
    const statusPalette = {
      sent: '#0b6e4f',
      error: '#b63720',
      cooldown: '#44515a',
      shadow_no: '#d17b0f',
      dry_run: '#1f4a7d',
      blocked: '#633c92',
      unknown: '#888888',
    };

    function colorForStatus(name) {
      return statusPalette[name] || '#888888';
    }

    function metricCard(label, value) {
      return `<div class="mini"><div class="label">${label}</div><div class="value">${value}</div></div>`;
    }

    function fmtPct(value) {
      return `${(Number(value || 0) * 100).toFixed(1)}%`;
    }

    async function fetchJson(url) {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Request failed: ${response.status}`);
      return response.json();
    }

    function destroyCharts() {
      Object.values(charts).forEach((chart) => chart.destroy());
    }

    function stackedBar(ctx, rows, keyField, titleOrder) {
      const labels = rows.map((row) => row[keyField]);
      const keys = titleOrder || [...new Set(rows.flatMap((row) => Object.keys(row).filter((key) => key !== keyField)))];
      return new Chart(ctx, {
        type: 'bar',
        data: {
          labels,
          datasets: keys.map((key) => ({
            label: key,
            data: rows.map((row) => row[key] || 0),
            backgroundColor: colorForStatus(key),
            borderRadius: 6,
          })),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
          plugins: { legend: { position: 'bottom' } },
        },
      });
    }

    function simpleBar(ctx, labels, values, color) {
      return new Chart(ctx, {
        type: 'bar',
        data: { labels, datasets: [{ data: values, backgroundColor: color, borderRadius: 8 }] },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: { y: { beginAtZero: true } },
        },
      });
    }

    function multiMetricBar(ctx, labels, datasets) {
      return new Chart(ctx, {
        type: 'bar',
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: { y: { beginAtZero: true } },
          plugins: { legend: { position: 'bottom' } },
        },
      });
    }

    function doughnut(ctx, labels, values) {
      return new Chart(ctx, {
        type: 'doughnut',
        data: {
          labels,
          datasets: [{
            data: values,
            backgroundColor: labels.map((label) => colorForStatus(label)),
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { position: 'bottom' } },
        },
      });
    }

    function scatter(ctx, points) {
      const statuses = [...new Set(points.map((point) => point.status))];
      return new Chart(ctx, {
        type: 'scatter',
        data: {
          datasets: statuses.map((status) => ({
            label: status,
            data: points.filter((point) => point.status === status).map((point) => ({
              x: point.x,
              y: point.y,
              ticker: point.ticker,
              side: point.side,
              momentum: point.momentum,
              conf: point.conf,
            })),
            backgroundColor: colorForStatus(status),
          })),
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom' },
            tooltip: {
              callbacks: {
                label(context) {
                  const raw = context.raw;
                  return `${raw.ticker} ${raw.side} price=${raw.x} score=${raw.y} momentum=${raw.momentum.toFixed(2)} conf=${raw.conf.toFixed(2)}`;
                },
              },
            },
          },
          scales: {
            x: { title: { display: true, text: 'Price' } },
            y: { title: { display: true, text: 'Score' } },
          },
        },
      });
    }

    async function loadFiles() {
      const data = await fetchJson('/api/files');
      const select = document.getElementById('file');
      select.innerHTML = data.files.map((file) => `<option value="${file}">${file}</option>`).join('');
      select.value = data.default_file;
    }

    function renderChipGroup(id, values, defaults) {
      const root = document.getElementById(id);
      root.innerHTML = values.map((value) => `
        <label class="chip">
          <input type="checkbox" value="${value}" ${defaults.includes(value) ? 'checked' : ''}>
          <span>${value}</span>
        </label>
      `).join('');
    }

    function queryFromInputs() {
      const params = new URLSearchParams();
      ['file', 'strategy', 'days', 'min_momentum', 'max_momentum', 'min_score', 'max_score', 'min_conf', 'max_conf', 'min_hour', 'max_hour', 'ticker'].forEach((id) => {
        const value = document.getElementById(id).value;
        if (value) params.set(id, value);
      });
      document.querySelectorAll('#status-group input:checked').forEach((el) => params.append('status', el.value));
      document.querySelectorAll('#side-group input:checked').forEach((el) => params.append('side', el.value));
      return params.toString();
    }

    async function refresh() {
      const data = await fetchJson(`/api/dashboard?${queryFromInputs()}`);
      document.getElementById('hero-rows').textContent = data.summary.rows.toLocaleString();
      document.getElementById('hero-range').textContent = data.summary.start ? `${data.summary.start.slice(0, 10)} to ${data.summary.end.slice(0, 10)}` : 'n/a';
      document.getElementById('hero-file').textContent = data.file_name;

      const strategySelect = document.getElementById('strategy');
      const currentStrategy = strategySelect.value;
      const strategyOptions = ['all', ...Object.keys(data.counts.strategy)];
      strategySelect.innerHTML = strategyOptions.map((value) => `<option value="${value}">${value}</option>`).join('');
      if (strategyOptions.includes(currentStrategy)) strategySelect.value = currentStrategy;

      document.getElementById('metric-grid').innerHTML = [
        metricCard('Avg score', data.summary.avg_score),
        metricCard('Avg price', data.summary.avg_price),
        metricCard('Avg edge', data.summary.avg_edge),
        metricCard('Avg EV (c)', data.summary.avg_ev_cents),
        metricCard('Avg EV ROI', fmtPct(data.summary.avg_ev_roi / 100)),
        metricCard('Avg spread', data.summary.avg_spread),
        metricCard('Avg momentum', data.summary.avg_momentum),
        metricCard('Avg conf', data.summary.avg_conf),
      ].join('');

      document.getElementById('realized-grid').innerHTML = [
        metricCard('Resolved trades', data.realized.rows),
        metricCard('Unresolved', data.realized.unresolved),
        metricCard('Realized P&L', data.realized.pnl.toFixed(2)),
        metricCard('ROI', fmtPct(data.realized.roi)),
        metricCard('Win rate', fmtPct(data.realized.win_rate)),
        metricCard('Avg P&L', data.realized.avg_pnl),
      ].join('');

      destroyCharts();
      charts.statusByDay = stackedBar(document.getElementById('statusByDay'), data.charts.status_by_day, 'day');
      charts.sideByDay = stackedBar(document.getElementById('sideByDay'), data.charts.side_by_day, 'day', ['yes', 'no']);
      charts.hourlyStatus = stackedBar(document.getElementById('hourlyStatus'), data.charts.hourly_status, 'hour');
      charts.realizedPnlByDay = simpleBar(
        document.getElementById('realizedPnlByDay'),
        data.realized.by_day.map((row) => row.day),
        data.realized.by_day.map((row) => row.pnl),
        '#0b6e4f',
      );
      charts.realizedBySide = multiMetricBar(
        document.getElementById('realizedBySide'),
        ['yes', 'no'],
        [
          {
            label: 'Realized P&L',
            data: ['yes', 'no'].map((side) => data.realized.by_side[side]?.pnl || 0),
            backgroundColor: ['#0b6e4f', '#d17b0f'],
            borderRadius: 8,
          },
          {
            label: 'Win rate x10',
            data: ['yes', 'no'].map((side) => (data.realized.by_side[side]?.win_rate || 0) * 10),
            backgroundColor: ['rgba(11,110,79,0.28)', 'rgba(209,123,15,0.28)'],
            borderRadius: 8,
          },
        ],
      );
      charts.statusMix = doughnut(
        document.getElementById('statusMix'),
        Object.keys(data.counts.status),
        Object.values(data.counts.status),
      );
      charts.topTickers = simpleBar(
        document.getElementById('topTickers'),
        data.charts.top_tickers.map((row) => row.ticker),
        data.charts.top_tickers.map((row) => row.count),
        '#0b6e4f',
      );
      charts.scoreBuckets = simpleBar(
        document.getElementById('scoreBuckets'),
        data.charts.score_buckets.map((row) => row.bucket),
        data.charts.score_buckets.map((row) => row.count),
        '#1f4a7d',
      );
      charts.momentumBuckets = simpleBar(
        document.getElementById('momentumBuckets'),
        data.charts.momentum_buckets.map((row) => row.bucket),
        data.charts.momentum_buckets.map((row) => row.count),
        '#d17b0f',
      );
      charts.priceScoreScatter = scatter(document.getElementById('priceScoreScatter'), data.charts.scatter);

      document.getElementById('recentRows').innerHTML = data.recent_rows.map((row) => `
        <tr>
          <td>${row.ts_utc}</td>
          <td>${row.strategy || ''}</td>
          <td>${row.ticker}</td>
          <td>${row.side}</td>
          <td>${row.price}</td>
          <td>${row.ev_cents || ''}</td>
          <td>${row.ev_roi ? `${(row.ev_roi * 100).toFixed(2)}%` : ''}</td>
          <td>${row.requested_count || ''}</td>
          <td>${row.notional_cents ? `$${(row.notional_cents / 100).toFixed(2)}` : ''}</td>
          <td>${row.score}</td>
          <td><span class="status-pill status-${row.status}">${row.status}</span></td>
          <td>${row.status_reason || ''}</td>
        </tr>
      `).join('');
    }

    async function boot() {
      await loadFiles();
      renderChipGroup('status-group', ['sent', 'error', 'cooldown', 'shadow_no', 'dry_run', 'blocked'], ['sent', 'error', 'cooldown', 'shadow_no', 'dry_run', 'blocked']);
      renderChipGroup('side-group', ['yes', 'no'], ['yes', 'no']);
      ['file', 'strategy', 'days', 'min_momentum', 'max_momentum', 'min_score', 'max_score', 'min_conf', 'max_conf', 'min_hour', 'max_hour', 'ticker'].forEach((id) => {
        document.getElementById(id).addEventListener('change', refresh);
        if (id === 'ticker' || id === 'days' || id.startsWith('min_') || id.startsWith('max_')) {
          document.getElementById(id).addEventListener('input', refresh);
        }
      });
      document.querySelectorAll('#status-group input, #side-group input').forEach((el) => {
        el.addEventListener('change', refresh);
      });
      await refresh();
    }

    boot().catch((error) => {
      document.body.innerHTML = `<pre style="padding:24px;">${error.message}</pre>`;
    });
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, payload: str, status: int = HTTPStatus.OK) -> None:
        data = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        try:
            if parsed.path == "/":
                self._send_html(HTML_PAGE)
                return
            if parsed.path == "/api/files":
                files = available_journal_files()
                self._send_json(
                    {
                        "files": [path.name for path in files],
                        "default_file": files[0].name if files else "",
                    }
                )
                return
            if parsed.path == "/api/dashboard":
                journal_path = resolve_journal_file(params.get("file", [None])[0])
                rows = load_rows(journal_path)
                filtered = filter_rows(rows, params)
                payload = build_dashboard(filtered, journal_path.name)
                self._send_json(payload)
                return
            self._send_json({"error": f"Not found: {html.escape(parsed.path)}"}, HTTPStatus.NOT_FOUND)
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # pragma: no cover
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", DEFAULT_PORT), DashboardHandler)
    print(f"Journal dashboard running at http://127.0.0.1:{DEFAULT_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
