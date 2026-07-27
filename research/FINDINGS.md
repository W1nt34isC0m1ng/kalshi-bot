# Kalshi 15-min BTC (KXBTC15M) — research findings

Market: "BTC price up in next 15 min?" YES settles if Coinbase BTC at expiry ≥
the spot at window-open (= `floor_strike`). Binary, ~50/50 at open.

## Data collected (`research/data/`)
- `markets.csv` — 1,500 settled markets, 2026-05-25 → 06-10 (strike, result, vol).
- `kalshi_candles.parquet` — 24k 1-min Kalshi quotes (yes_bid/ask/last).
- `btc_1m.parquet` — 23k Coinbase 1-min candles.
- High-frequency window (last 2.5 days, 240 markets):
  - `btc_1s.parquet` — 216k Binance 1s candles (underlying).
  - `kalshi_trades.parquet` — **1.9M Kalshi trade prints** (microsecond stamps).

Fills modeled at executable prices (YES@ask, NO@1−bid). Fees:
taker `ceil(0.07·p(1−p))`, maker `≈0.0175·p(1−p)` (¼ of taker; crypto may be higher).

## What does NOT work (after fixing a 60s look-ahead bug)
The original panel keyed Coinbase spot by the Kalshi candle's *end* timestamp,
pulling a close realized up to 60s in the future → a phantom ~5¢ momentum edge.
Fixed to use only fully-realized spot. After the fix:

| Thesis | Result |
|---|---|
| Calibration of mid vs realized | Market well-calibrated; realized YES ≈ quoted mid in every distance bucket |
| Displacement / momentum → settlement | **~0 gross**, negative after costs |
| BS fair-value vs market (incl. live `vol_mult=3.5`) | **−2 to −3¢/contract** at all settings, every threshold |
| Fair-gap → settlement (gross, no fees) | **−1.5¢** — the "stale" price isn't actually fillable (adverse selection) |
| Hour-of-day | Apparent signals are sample-path artifacts (when BTC happened to fall); won't generalize |

**Conclusion: at minute/settlement resolution the market is efficient.** The
deployed BS-vs-market strategy is not +EV on real prices net of costs.

## What DOES have potential: sub-minute lead-lag scalp
- **Mechanism (strong):** underlying 1s return vs Kalshi *next-second* price move
  correlates **+0.47 @1s**, decaying to +0.18 @10s (n≈215k). Kalshi quotes lag
  the underlying by several seconds.
- **Scalp:** enter in the direction of a sharp underlying tick (|3s log-ret| >
  ~7bps), exit ~5s later as the quote catches up. Gross **+2.3¢ (t=+48)**.
- **Net P&L by execution regime** (H=5s, thr=7bps, spread=1¢, conservative maker fee):

  | Regime | mean | t | note |
  |---|---|---|---|
  | taker / taker | −0.4¢ | −1.8 | round-trip taker fee kills it |
  | maker entry **or** maker exit | +1.9¢ | +8.2 | saving one fee leg flips it positive |
  | maker / maker | +4.2¢ | +18 | best case; robust across both time-halves & spreads |

## Honest caveats
1. **Fill realism is the crux.** The maker numbers assume passive fills on the
   favorable side — but maker fills are adversely selected (you tend to get filled
   when the move *reverses*). True maker P&L sits between taker/taker and
   maker/maker and needs a fill-probability model + low-latency infra to realize.
2. Only 2.5 days of HF data, a single (mildly down-trending) BTC regime.
3. Last-trade price used as a mid proxy (no historical L2 book).
4. This is latency arbitrage — a contested, infrastructure-sensitive game.

## Realistic maker-fill simulation (trade-stream matching engine)
No historical L2 book, but each trade print's `taker_side` reveals the touched
price, so a resting order is "filled" only when a later taker actually crosses it
(`fill_sim.py`). Scalp = post passive on the signal side, exit as taker after 5s.

- **Fill rate 59.8%** — ~40% of signals never fill (the fastest-moving = best ones).
- **Conditional P&L on fills: +0.73¢/contract, t=+2.63, win 38%** (n=626, 2.5 days).
- vs +1.9¢ in the naive always-filled model → **adverse selection eats ~60%** of edge.
- Median fill delay 0.1s (bid hit almost instantly — a mild adverse-selection tell).

**Bottom line: a real but THIN edge (~+0.7¢/contract) after realistic fills.**
Positive and weakly significant over one 2.5-day regime; fragile, execution-bound,
and unproven out-of-sample. Not yet sized-up material.

## WIDE-WINDOW VALIDATION (15.9 days, 1,499 markets, 10.9M trade prints)
Re-ran the scalp + momentum theses on the full window with look-ahead removed at
**both 60s and 1s scales** (a 1s bug — Binance bucket close at `t` realizes at
`t+1` — had again inflated results) and out-of-sample (first half vs second half).

| Strategy (net of fees, no look-ahead) | Result |
|---|---|
| Lead-lag scalp, realistic maker-entry/taker-exit fills (`analyze_wide.py`) | **+0.11¢, t=1.3** over 4,814 fills — not significant; signs flip by third |
| Extreme-jump taker scalp (exit +5s) | **−2.7¢, t=−24** — dead |
| Momentum→settlement, full sample | +1.4¢, t≈2.0 — borderline, inconsistent across thresholds |
| ↳ split by side | YESup +5.5¢ (t=5.9), NOdn −3.3¢ (t=−3.3) — asymmetric, *opposite* of drift |
| ↳ **out-of-sample halves** (`validate_momentum.py`) | **NOT robust:** YESup edge lives only in H2 (+3.6–6¢) and is **negative in H1** (−1.7¢); NOdn flips the other way |

**Verdict: no strategy shows a robust, stationary edge net of costs.** Every
apparent edge was either a look-ahead artifact or regime-specific (doesn't survive
the H1/H2 split). The momentum-to-settlement signal is real but is *directional
trend exposure* that only pays in trending regimes — not a stationary mispricing.

## Passive market-making (`mm_sim.py`)
Posted two-sided quotes around an underlying fair value with defensive skew,
filled against real trade flow, maker fees, inventory caps, settled residual.
- Loses **−2 to −4.4¢/contract** at every spread / skew / inventory setting.
- The flow is **toxic**: our quotes fill exactly when fair is about to move against
  us (adverse selection). Skew doesn't help because our fair value also lags.
- => No passive-MM money printer either, for a non-colocated participant.

## Honest overall conclusion
I could **not** find a reliably profitable model for KXBTC15M with the data
available. The market is efficiently priced for what a taker (or realistically-
filled maker) can capture. The one true inefficiency — the seconds-scale quote
lag — is not monetizable after fees + adverse selection in any execution regime I
could model. The currently-deployed BS-vs-market strategy is **−EV** and should be
disabled. Biggest lesson: **timestamp look-ahead** (60s AND 1s) repeatedly
manufactured phantom edges; the no-look-ahead, out-of-sample discipline is what
separated signal from noise.

## Recommended next steps
1. **Disable the deployed BS-vs-market strategy** — it is −EV on real prices.
2. If pursuing the quote-lag at all, it is a latency/market-making game: needs
   colocated low-latency infra + live L2 book capture to model maker fills, and
   even then the realistic-fill edge here was ~0. Likely not worth it for a retail
   setup against established HFT arbs.
3. Treat the momentum-to-settlement signal as **regime exposure**, not alpha; if
   traded, it must be paired with a regime filter and accepted as a directional bet.
4. Any future backtest MUST enforce: lag the underlying to the last fully-closed
   bucket (no `t→t+1` leakage), and validate out-of-sample by time split.
