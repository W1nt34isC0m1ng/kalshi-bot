# Changelog

Notable changes to kalshi-bot. Loosely follows [Keep a Changelog](https://keepachangelog.com/).
Each entry should explain *why* the change was made and, where applicable, what
empirical evidence motivated it. Trading strategy changes are hypotheses — record
the data, not just the diff.

Format: dates are UTC. Sections: Added / Changed / Fixed / Removed.

---

## [Unreleased] — branch `experiment/chop-guard`

### Added
- **Kaufman Efficiency Ratio (`fetch_efficiency_ratio`).** Net price move /
  total path length over a 20-min window. ER ≈ 1 = trend, ER ≈ 0 = chop.
  Reuses the candle cache from `fetch_rolling_vol`, no extra API call.
- **Chop-guard gate** on `CryptoProbStrategy.evaluate()`. Rejects signals
  when ER < `min_efficiency_ratio` (default 0.30).
- `er=` field in journal `reason` for post-hoc analysis.

### Backtest evidence
397 historical settled trades, ER computed at each signal time:

| ER bucket | n | WR | pnl/trade |
|---|---|---|---|
| <0.20 (extreme chop) | 233 (59%) | 30% | −14¢ |
| 0.20–0.30 (chop) | 73 (18%) | 26% | −26¢ |
| 0.30–0.40 (mild chop) | 49 (12%) | 41% | +22¢ |
| 0.40+ (mid/trend) | 40 (10%) | 38% | +1¢ |

77% of historical trades fired in chop regimes (ER < 0.30) where the bot
systematically lost ~−18¢/trade. Above ER 0.30, +9¢/trade across 89 trades.

Threshold sweep at 0.30 maximizes pnl: keep 89 trades (+$8.88) and block
306 (−$51.34 avoided). Net swing of ~$60+ across journal history vs
running ungated. Live test at commit time: ER = 0.13, gate would block.

### Validation plan
- ≥30 post-marker trades.
- Cumulative WR ≥ 42% (we know pre-fix on main has been struggling ~30%).
- Volume should drop ~70%+ vs pre-gate baseline (this is feature, not bug).
- If ER backtest holds out-of-sample, this branch graduates to main.

---

---

## [2026-04-30] — `experiment/c2c-blend` invalidated, not merged

**Hypothesis:** Parkinson HLOC understates vol on directional 1-min candles
→ NO bets in σ 0.30–0.45 are systematically miscalibrated by +26pp →
blending in close-to-close (`max(parkinson, c2c, implied)`) should close
the leak.

**Result:** invalidated. Pre-revert numbers:

| Window | n | WR | P&L |
|---|---|---|---|
| Pre-c2c (post-drift) | 80 | 45% | +$38.88 |
| Post-c2c-ship | 52 | 29% | −$43.60 |
| Post-drift cumulative | 134 | 39% | −$3.28 |

The c2c branch ran for ~17 hours and wiped out the entire +$31 of
accumulated post-drift edge. Statistical test: at n=52 with 15 wins, if
the true rate were still the 45% pre-c2c baseline, this outcome has
probability ~1%. That's significant evidence the change was harmful, the
regime shifted, or both.

**What c2c actually did (mechanistically):**
- C2c won the `max()` 80% of the time → sigma was raised on most signals
  (mean ~0.51 vs Parkinson's ~0.45).
- Implied vol won 1% of the time → market is consistently more confident
  than realized vol justifies.
- This validates the structural argument (c2c IS higher than Parkinson)
  but not the empirical one (raising sigma did not improve outcomes).

**Possible reasons the fix didn't work:**
1. The +26pp NO-side calibration gap may not be a sigma magnitude problem.
   It could be a regime-specific effect (chop) or a structural BS
   limitation that no vol blend addresses.
2. Sample of 52 is still smaller than ideal; true effect could be neutral
   with bad luck on top. But ~1% probability under the null is hard to
   wave away.
3. The market regime changed concurrent with the ship — overnight chop
   would have hurt any model. Hard to disentangle from the change itself.

**Action:** revert the running bot to `main` (pre-c2c), keep
`experiment/c2c-blend` branch intact as a documented failed experiment,
reassess the NO-side leak from a different angle (not just sigma magnitude).

**Reverted at:** 2026-04-30 15:05 UTC (marker in journal)

---

## [2026-04-29] — merged `experiment/parkinson-sigma` → main

**Validation summary (69 post-drift trades):**
- Cumulative WR: 46% (target ≥42%) ✅
- Net P&L: +$42.08 / +$0.61 per trade ✅
- YES bets: WR 32% → 43% (+11pp); calibration gap −12pp → −5pp ✅
- NO bets: WR 42% → 48% (+6pp); calibration gap unchanged at +9pp ⚠️

The drift correction substantially helped YES side. NO side improved on WR
but calibration gap (model too bearish in 30–50% probability range) is
unchanged — likely because drift only helps in trending markets, and many
NO bets fire in ranging markets where vol calibration alone drives the
decision. The NO-side residual is a separate hypothesis tracked in a new
experiment branch.

### Added
- **Drift-aware Black-Scholes (`mu_per_minute` parameter on `prob_above_strike`
  and `compute_d2`).** Calibration analysis on 46 post-Parkinson trades found
  that while the aggregate model was well-calibrated (47% predicted YES vs
  46% observed), the model was systematically anti-predictive on side
  selection — wherever it disagreed with the market, the market was right.
  The likely cause: zero-drift BS missing momentum/drift the market prices in.
  - Drift estimate is the 20-min realized log-drift per minute, sourced from
    `fetch_recent_log_drift` (reuses cached candles from `fetch_rolling_vol`).
  - Damping factor `drift_persistence = 0.15` sizes the correction to the
    observed ~10pp calibration error rather than assuming full momentum
    persistence (which would over-shift fair_prob by ~30pp on typical trends).
  - Falls back to zero drift if the candle cache isn't populated, so behavior
    is unchanged on the first poll cycle and on any data fetch failure.
  - Tunable via `CryptoProbStrategy(drift_persistence=...)`. Validation plan:
    next ~30 trades should show YES-bet observed P(YES) closer to predicted,
    and ideally a positive edge over the market on side selection.

### Fixed
- **Stale risk state in DRY_RUN mode (silent strangle bug).** In DRY_RUN,
  `_reconcile_positions` never ran, so `mark_sent` accumulated dry-run "fills"
  into `risk_state.json` indefinitely. Within ~24 hours the portfolio notional
  cap (4000c) saturated with ghost positions on already-expired markets,
  causing the bot to block otherwise-valid signals.
  - **Damage:** 157 signals blocked over 4 days, including **18 of the 20
    post-Parkinson signals** we needed for validation. The fix landed before
    the validation window had statistically meaningful data.
  - **Fix:** `RiskManager.prune_expired_markets()` drops positions on tickers
    whose Kalshi-time expiry has passed. Called every poll loop when
    `settings.dry_run` is True (live mode already handles this via
    exchange-side reconciliation). Cheap, idempotent.
  - **Recovery tool:** `kalshi_bot.recover` shadow-settles the 157 blocked
    signals from the journal and writes them to `logs/recovered_outcomes.csv`
    (kept distinct from `outcomes.csv` because they didn't actually fire).
    Confirmed post-Parkinson 50% WR (10W-10L, +$28.56) when combining real
    fills with shadow-recovered signals.

### Added
- **`kalshi_bot.tickers`** — shared ticker → expiry parser used by both
  `settle.py` (outcome resolution) and `risk.py` (expiry-based pruning).
- **`kalshi_bot.recover`** — see Fixed > Recovery tool above.

### Changed
- **Volatility estimator: close-to-close → Parkinson HLOC.**
  `fetch_rolling_vol` now uses each minute candle's high/low range instead of
  close-to-close log returns. Parkinson is ~5× more statistically efficient
  for the same number of observations because it captures intra-minute movement
  the close-to-close estimator discards.
  - **Why now:** journal analysis on 226 historical dry-run trades showed
    `sigma` was being clipped to the 0.50 floor 41% of the time. Trades fired
    at the floor won at 14% vs 37% otherwise — i.e. the floor was poisoning
    almost half of all decisions.
  - **Floor lowered:** 0.50 → 0.10. Only retained as a degenerate-data guard
    (flat candles → sigma → 0 → d2 → ∞).
  - **Backtest evidence (2026-04-24):** re-evaluating the day's 15 fired
    trades with Parkinson + existing gates produced 2W-3L (+$4.16) vs the
    actual 3W-12L (-$5.04) — a net swing of +$9.20 driven by the new sigma
    correctly rejecting OTM-YES lottery tickets the floored sigma was overpricing.
    Sample size is small; treat as a sanity check, not validation.

- **`max_d2` cap rationale (crypto_strategy.py).** Comment updated. The cap
  itself stays at 1.2 but for a different reason than originally documented:
  the old comment claimed "0% WR above 1.5", which was a sigma-floor artifact.
  With realistic sigma, d2 1.2-1.5 actually wins ~44% of the time. The cap
  remains because deep-strike trades have asymmetric payoffs (win +8c, lose
  -92c on a NO@92) that lose money even at 44% WR.

### Added
- **`kalshi_bot.settle` — outcome writeback CLI.** Reads `trade_journal.csv`,
  finds settled-but-unrecorded trades, fetches Coinbase BTC closes at expiry,
  and appends to `logs/outcomes.csv`. Append-only and idempotent.
  - **Why:** every "how have we done?" question previously required spawning
    100+ Coinbase API calls in an ad-hoc script. Now it's a `csv.DictReader`.
  - **Schema:** `(trade_ts_utc, ticker)` joins to journal rows. Outcome columns:
    `settle_price, outcome (win|loss), pnl_cents, settled_at_utc`.
  - **Usage:** `python -m kalshi_bot.settle` — manual or scheduled.

- **`CHANGELOG.md`** (this file).

### Validation plan
This branch should not merge to `main` until:
1. ≥ 30 trades have fired since the post-ship marker (`2026-04-25T17:23:21 UTC`)
2. WR is materially better than the pre-ship baseline (36% all-time on crypto_prob)
3. At-floor sigma frequency drops from 41% → < 10% (the structural fix's primary metric)

If the change is invalidated, branch is deleted; no harm to `main`.

---

## Pre-changelog history (in git log)

Prior changes are documented in commit messages. Notable points:
- `9c64c4e` Disabled `mean_reversion` to focus on `crypto_prob` (its WR was 31% vs crypto_prob's 91% over a recent overnight window).
- `1f33422` Added directional trend filter to `crypto_prob` (NO bets in uptrends were a major loss source on 2026-04-22).
- `73a34f1` Earlier sigma overconfidence guard (the d2 cap and 0.50 floor that this branch is now revising).
