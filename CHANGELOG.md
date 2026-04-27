# Changelog

Notable changes to kalshi-bot. Loosely follows [Keep a Changelog](https://keepachangelog.com/).
Each entry should explain *why* the change was made and, where applicable, what
empirical evidence motivated it. Trading strategy changes are hypotheses — record
the data, not just the diff.

Format: dates are UTC. Sections: Added / Changed / Fixed / Removed.

---

## [Unreleased] — branch `experiment/parkinson-sigma`

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
