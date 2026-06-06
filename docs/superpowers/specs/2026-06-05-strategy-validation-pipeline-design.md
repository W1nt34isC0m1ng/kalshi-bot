# Strategy Validation Pipeline Design

**Date:** 2026-06-05  
**Status:** Approved  
**Scope:** Autonomous pre-promotion validation for any Kalshi strategy that implements `evaluate(market) -> Signal | None`

---

## Problem

Strategies can be backtested on historical journal data but there is no automated gate that prevents a strategy from going live if its shadow (dry-run) performance diverges from backtest expectations. There is also no guardrail preventing an accidental git-branch switch on the live process, and no collision detection when two strategies target the same market ticker.

---

## Solution

A `validate.py` CLI orchestrates three parallel agents, collects their results into a durable `logs/validation_report.json`, then runs pytest gates against that report. If all five gates pass, it patches `.env` to `DRY_RUN=false`. If any gate fails, `.env` is untouched and the blocking reason is printed.

---

## File Layout

```
validate.py                          # CLI entry point
src/kalshi_bot/validation/
  __init__.py
  pipeline.py                        # ValidationPipeline orchestrator
  backtest_agent.py                  # BacktestWorker
  shadow_agent.py                    # ShadowWorker
  statistics_agent.py                # StatisticsWorker
  guardrails.py                      # branch check + market collision
  report.py                          # ValidationReport dataclass + JSON I/O
tests/
  test_validation_gates.py           # pytest promotion gates (reads report JSON)
  test_backtest_agent.py
  test_shadow_agent.py
  test_statistics_agent.py
  test_guardrails.py
logs/
  shadow_journal.csv                 # ShadowWorker output (separate from live journal)
  validation_report.json             # durable artifact read by pytest gates
```

---

## Data Flow

```
validate.py --declared-floor 0.55 --min-fills 100 --strategy mean_reversion
     │
     ▼
GuardrailChecker
  ├─ assert_on_main_branch()        raises GuardrailError if not on main (hard block)
  └─ check_market_collision(...)    prints WARNING if 2+ strategies share a ticker
     │
     ▼
ThreadPoolExecutor (2 workers, run concurrently)
  ├─► BacktestWorker
  │     reads  logs/trade_journal.csv  (status=dry_run|sent|shadow_*)
  │     calls  resolve_yes_outcome()   (existing Coinbase candle logic in backtest.py)
  │     returns BacktestResult(win_rate, n_resolved, by_asset)
  │
  └─► ShadowWorker
        polls  Kalshi API (public client, no auth needed)
        calls  strategy.evaluate(market) for each open market each tick
        writes fills → logs/shadow_journal.csv
        blocks until n_fills >= min_fills
        resolves outcomes via resolve_yes_outcome() once markets expire
        returns ShadowResult(win_rate, n_fills)
     │
     ▼
StatisticsWorker
  ├─ chi_square(shadow_wins, shadow_losses, p_expected=backtest_wr)
  │     H₀: shadow WR == backtest WR  (two-sided)
  │     p_backtest > 0.05 → consistent → PASS
  └─ chi_square(shadow_wins, shadow_losses, p_expected=declared_floor)
        H₀: shadow WR >= floor  (one-sided)
        p_floor < 0.05 → beats floor → PASS
     │
     ▼
ValidationReport → logs/validation_report.json
     │
     ▼
pytest tests/test_validation_gates.py
  exit 0 → .env patched: DRY_RUN=false  +  PROMOTED printed
  exit 1 → .env untouched               +  BLOCKED + reason printed
```

---

## Components

### BacktestWorker (`backtest_agent.py`)

Wraps the existing `backtest_journal()` logic from `backtest.py`. Accepts `journal_path: str` and returns:

```python
@dataclass
class BacktestResult:
    win_rate: float
    n_resolved: int
    n_wins: int
    by_asset: dict[str, dict]   # {asset_prefix: {count, wins, win_rate}}
```

Requires `n_resolved >= 100` to be considered valid (enforced by Gate 5).

### ShadowWorker (`shadow_agent.py`)

Runs the strategy's `evaluate()` loop against live Kalshi market data. Uses the existing `MarketDataService` and public `KalshiHttpClient`. Writes fills to `logs/shadow_journal.csv` using the same schema as `TradeJournal`. Blocks until `n_fills >= min_fills`. Returns:

```python
@dataclass
class ShadowResult:
    win_rate: float
    n_fills: int
    n_wins: int
```

Outcome resolution reuses `resolve_yes_outcome()` from `backtest.py` — no second implementation.

### StatisticsWorker (`statistics_agent.py`)

Receives `BacktestResult`, `ShadowResult`, and `declared_floor_wr: float`. Runs:

1. `scipy.stats.chisquare` with `f_obs=[shadow_wins, shadow_losses]`, `f_exp=[backtest_wr * n, (1-backtest_wr) * n]` — two-sided goodness-of-fit test.
2. `scipy.stats.binomtest(shadow_wins, shadow_n, declared_floor_wr, alternative='greater')` — one-sided proportion test (H₀: shadow_wr ≤ floor, H₁: shadow_wr > floor). Tests whether shadow WR significantly exceeds the declared floor.

Returns:

```python
@dataclass
class StatisticsResult:
    chi2_vs_backtest: float
    p_backtest: float          # two-sided; PASS when > 0.05
    statistic_vs_floor: float  # binomtest statistic
    p_floor: float             # one-sided; PASS when < 0.05
```

### ValidationReport (`report.py`)

```python
@dataclass
class ValidationReport:
    backtest_wr: float
    backtest_n: int
    shadow_wr: float
    shadow_n: int
    wr_delta: float
    wr_delta_passes: bool          # |backtest_wr - shadow_wr| <= 0.05
    p_backtest: float
    chi2_backtest_passes: bool     # p_backtest > 0.05
    declared_floor: float
    p_floor: float
    chi2_floor_passes: bool        # p_floor < 0.05
    overall_verdict: str           # "PASS" | "FAIL"
    blocking_reason: str           # empty string if PASS
    market_collision_warnings: list[str]
```

Serialised to / deserialised from `logs/validation_report.json`.

### Guardrails (`guardrails.py`)

**`assert_on_main_branch()`**
- Runs `git rev-parse --abbrev-ref HEAD` via `subprocess`.
- Raises `GuardrailError` if result is not `"main"`.
- Runs before any workers start — no simulation is wasted on wrong branch.
- `validate.py` catches `GuardrailError`, prints the message, exits 1. `.env` never touched.

**`check_market_collision(strategies, markets)`**
- Calls `evaluate()` on every `(strategy, market)` pair.
- Collects `{ticker → [strategy_names_that_signalled]}`.
- Returns list of warning strings for tickers with 2+ strategies signalling.
- Written into `ValidationReport.market_collision_warnings`.
- Warning only — intentional multi-strategy markets are valid.

---

## pytest Gates (`tests/test_validation_gates.py`)

All five gates must pass for promotion. Tests read `logs/validation_report.json`.

| Gate | Assertion | Rationale |
|---|---|---|
| 1 | `shadow_n >= 100` | Minimum statistical power |
| 2 | `abs(shadow_wr - backtest_wr) <= 0.05` | Magnitude check catches drift chi-square misses at N=100 |
| 3 | `p_backtest > 0.05` | Strategy behaviour consistent with historical baseline |
| 4 | `p_floor < 0.05` | Strategy statistically beats the declared floor WR |
| 5 | `backtest_n >= 100` | Backtest itself must have enough resolved trades |

Gates 2 and 3 are deliberately redundant: Gate 2 catches a 4.9% drift that chi-square may not flag; Gate 3 catches statistically significant drift still under 5%.

---

## TDD Test Files

Each test file is written before its implementation.

| File | Covers |
|---|---|
| `test_backtest_agent.py` | Known journal fixtures; unresolved/future tickers handled gracefully |
| `test_shadow_agent.py` | Exits at `n_fills >= min_fills`; correct CSV schema; rejects non-dry-run fills |
| `test_statistics_agent.py` | Chi-square math with known W/L counts; edge cases (all wins, 50/50) |
| `test_guardrails.py` | Branch-check raises on non-main; collision detection finds overlapping tickers |
| `test_validation_gates.py` | PASS report → all 5 gates green; each failure mode → exactly the right gate fails |

---

## CLI Interface

```
python validate.py \
  --strategy mean_reversion \
  --declared-floor 0.55 \
  --min-fills 100 \
  --journal logs/trade_journal.csv \
  --shadow-journal logs/shadow_journal.csv \
  --report logs/validation_report.json
```

Exit codes: `0` = promoted, `1` = blocked or guardrail tripped.

---

## Constraints & Non-Goals

- Pipeline **never** runs `git checkout`, `git branch`, or `git push`. The branch guardrail is read-only (`git rev-parse`).
- Pipeline **never** modifies `.env` unless all five pytest gates pass.
- Shadow accumulation may take hours or days depending on market activity — this is expected. The pipeline is designed to be left running.
- NBA markets and any future strategy categories are supported without pipeline changes, as long as the strategy implements `evaluate(market) -> Signal | None`.
- No UI dashboard — verdict is terminal output + JSON report.
