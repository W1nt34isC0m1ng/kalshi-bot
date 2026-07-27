"""Collect settled markets + 1-min candles for the less-liquid 15-min coins,
plus Coinbase 1-min underlying. Tests whether thinner markets are miscalibrated.
"""
import sys, time
from datetime import datetime, timezone, timedelta
import pandas as pd, requests
from collect_kalshi import _get, iso_to_ts, _px, BASE

SERIES_PRODUCT = {
    "KXETH15M": "ETH-USD",
    "KXSOL15M": "SOL-USD",
    "KXDOGE15M": "DOGE-USD",
    "KXXRP15M": "XRP-USD",
}
PER = int(sys.argv[1]) if len(sys.argv) > 1 else 700
CB = "https://api.exchange.coinbase.com/products/{}/candles"
S = requests.Session()


def collect_markets(series, n):
    rows, cursor = [], None
    while len(rows) < n:
        p = {"series_ticker": series, "status": "settled", "limit": 200}
        if cursor: p["cursor"] = cursor
        j = _get(f"{BASE}/markets", p)
        mk = j.get("markets", [])
        if not mk: break
        for m in mk:
            if m.get("result") not in ("yes", "no"): continue
            rows.append({"series": series, "ticker": m["ticker"],
                         "open_ts": iso_to_ts(m["open_time"]), "close_ts": iso_to_ts(m["close_time"]),
                         "floor_strike": float(m.get("floor_strike") or 0), "result": m["result"],
                         "volume": float(m.get("volume_fp") or 0)})
        cursor = j.get("cursor")
        if not cursor: break
        time.sleep(0.12)
    return rows[:n]


def collect_candles(series, markets):
    out = []
    for m in markets:
        try:
            j = _get(f"{BASE}/series/{series}/markets/{m['ticker']}/candlesticks",
                     {"start_ts": m["open_ts"]-120, "end_ts": m["close_ts"]+120, "period_interval": 1})
        except Exception:
            continue
        for c in j.get("candlesticks", []):
            out.append({"ticker": m["ticker"], "end_ts": int(c["end_period_ts"]),
                        "yes_bid": _px(c, "yes_bid"), "yes_ask": _px(c, "yes_ask"),
                        "last": _px(c, "price")})
        time.sleep(0.04)
    return out


def collect_coinbase(product, start, end):
    rows, cur = [], start
    step = timedelta(minutes=300)
    while cur < end:
        ce = min(cur+step, end)
        for a in range(5):
            r = S.get(CB.format(product), params={"granularity":60,
                "start":cur.isoformat().replace('+00:00','Z'),"end":ce.isoformat().replace('+00:00','Z')}, timeout=25)
            if r.status_code == 429: time.sleep(0.6*2**a); continue
            r.raise_for_status(); break
        for c in r.json():
            rows.append({"ts":int(c[0]),"close":c[4]})
        cur = ce; time.sleep(0.25)
    return rows


all_mkts, all_candles, all_cb = [], [], []
for series, product in SERIES_PRODUCT.items():
    print(f"== {series} ({product}) ==", flush=True)
    mkts = collect_markets(series, PER)
    print(f"  {len(mkts)} markets", flush=True)
    all_mkts += mkts
    all_candles += collect_candles(series, mkts)
    if mkts:
        st = datetime.fromtimestamp(min(m["open_ts"] for m in mkts)-1800, timezone.utc)
        en = datetime.fromtimestamp(max(m["close_ts"] for m in mkts)+1800, timezone.utc)
        cb = collect_coinbase(product, st, en)
        for r in cb: r["product"] = product
        all_cb += cb
        print(f"  {len(cb)} coinbase candles", flush=True)

pd.DataFrame(all_mkts).to_csv("data/multi_markets.csv", index=False)
pd.DataFrame(all_candles).to_parquet("data/multi_candles.parquet", index=False)
pd.DataFrame(all_cb).to_parquet("data/multi_cb.parquet", index=False)
print(f"saved {len(all_mkts)} markets, {len(all_candles)} candles, {len(all_cb)} cb rows")
