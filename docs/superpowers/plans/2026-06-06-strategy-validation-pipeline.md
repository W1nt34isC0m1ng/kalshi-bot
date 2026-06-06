# Strategy Validation Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous strategy validation pipeline that runs three parallel agents (backtest, shadow-trading, statistics), blocks promotion unless five pytest gates pass, and hard-guardrails git branch switches and market collisions.

**Architecture:** A `validate.py` CLI orchestrates `ValidationPipeline`, which fans out `BacktestWorker` and `ShadowWorker` via `ThreadPoolExecutor`, then feeds results to `StatisticsWorker`. Results serialise to `logs/validation_report.json` which five pytest gate tests assert against. Promotion writes `DRY_RUN=false` to `.env` only on clean pytest exit.

**Tech Stack:** Python 3.11+, scipy≥1.7 (chi-square + binomtest), pytest, concurrent.futures, existing `backtest.py` utilities (`resolve_yes_outcome`, `pnl_for_trade`), Kalshi public HTTP client for shadow market data.

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `src/kalshi_bot/validation/__init__.py` | Package marker |
| Create | `src/kalshi_bot/validation/report.py` | `ValidationReport` dataclass + JSON I/O |
| Create | `src/kalshi_bot/validation/guardrails.py` | Branch check + market collision |
| Create | `src/kalshi_bot/validation/backtest_agent.py` | `BacktestWorker` wrapping `backtest_journal()` |
| Create | `src/kalshi_bot/validation/statistics_agent.py` | `StatisticsWorker` (chi-square + binomtest) |
| Create | `src/kalshi_bot/validation/shadow_agent.py` | `ShadowWorker` (live dry-run accumulator) |
| Create | `src/kalshi_bot/validation/pipeline.py` | `ValidationPipeline` orchestrator |
| Create | `validate.py` | CLI entry point |
| Create | `tests/test_report.py` | Unit tests for report serialisation |
| Create | `tests/test_guardrails.py` | Unit tests for guardrails |
| Create | `tests/test_backtest_agent.py` | Unit tests for BacktestWorker |
| Create | `tests/test_statistics_agent.py` | Unit tests for StatisticsWorker |
| Create | `tests/test_shadow_agent.py` | Unit tests for ShadowWorker |
| Create | `tests/test_pipeline.py` | Unit tests for ValidationPipeline |
| Create | `tests/test_validation_gates.py` | **Promotion gate tests** (read report JSON) |
| Modify | `requirements.txt` | Add scipy, pytest |

---

## Task 1: Dependencies + Package Scaffold

**Files:**
- Modify: `requirements.txt`
- Create: `src/kalshi_bot/validation/__init__.py`

- [ ] **Step 1: Add dependencies to requirements.txt**

Append these two lines (pandas is already used by `backtest.py` and assumed installed):

```
scipy>=1.7.0
pytest>=7.0
```

Final `requirements.txt`:
```
requests>=2.32.0
websocket-client>=1.8.0
python-dotenv>=1.0.1
cryptography>=42.0.0
orjson>=3.10.0
pydantic>=2.8.0
rich>=13.7.0
scipy>=1.7.0
pytest>=7.0
```

- [ ] **Step 2: Install new dependencies**

```bash
pip install scipy pytest
```

Expected: `Successfully installed scipy-...`

- [ ] **Step 3: Create the validation package**

```bash
mkdir -p src/kalshi_bot/validation tests
touch src/kalshi_bot/validation/__init__.py
```

- [ ] **Step 4: Verify importable**

```bash
python -c "from src.kalshi_bot.validation import __name__; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt src/kalshi_bot/validation/__init__.py tests/
git commit -m "chore: add scipy/pytest deps and validation package scaffold"
```

---

## Task 2: ValidationReport Dataclass

**Files:**
- Create: `src/kalshi_bot/validation/report.py`
- Create: `tests/test_report.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_report.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.validation.report import ValidationReport


def _pass_report() -> ValidationReport:
    return ValidationReport(
        backtest_wr=0.61,
        backtest_n=142,
        shadow_wr=0.60,
        shadow_n=105,
        wr_delta=0.01,
        wr_delta_passes=True,
        p_backtest=0.80,
        chi2_backtest_passes=True,
        declared_floor=0.55,
        p_floor=0.02,
        chi2_floor_passes=True,
        overall_verdict="PASS",
        blocking_reason="",
        market_collision_warnings=[],
    )


def test_round_trip_json(tmp_path):
    report = _pass_report()
    path = str(tmp_path / "report.json")
    report.to_json(path)
    loaded = ValidationReport.from_json(path)
    assert loaded == report


def test_to_json_creates_parent_dirs(tmp_path):
    report = _pass_report()
    nested = str(tmp_path / "a" / "b" / "report.json")
    report.to_json(nested)
    assert Path(nested).exists()


def test_json_contains_expected_keys(tmp_path):
    report = _pass_report()
    path = str(tmp_path / "report.json")
    report.to_json(path)
    data = json.loads(Path(path).read_text())
    for key in [
        "backtest_wr", "backtest_n", "shadow_wr", "shadow_n",
        "wr_delta", "wr_delta_passes", "p_backtest", "chi2_backtest_passes",
        "declared_floor", "p_floor", "chi2_floor_passes",
        "overall_verdict", "blocking_reason", "market_collision_warnings",
    ]:
        assert key in data, f"Missing key: {key}"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_report.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `report.py` does not exist yet.

- [ ] **Step 3: Implement `src/kalshi_bot/validation/report.py`**

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ValidationReport:
    backtest_wr: float
    backtest_n: int
    shadow_wr: float
    shadow_n: int
    wr_delta: float
    wr_delta_passes: bool
    p_backtest: float
    chi2_backtest_passes: bool
    declared_floor: float
    p_floor: float
    chi2_floor_passes: bool
    overall_verdict: str          # "PASS" | "FAIL"
    blocking_reason: str
    market_collision_warnings: list[str]

    def to_json(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def from_json(cls, path: str) -> "ValidationReport":
        data = json.loads(Path(path).read_text())
        return cls(**data)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_report.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/kalshi_bot/validation/report.py tests/test_report.py
git commit -m "feat: add ValidationReport dataclass with JSON round-trip"
```

---

## Task 3: Guardrails

**Files:**
- Create: `src/kalshi_bot/validation/guardrails.py`
- Create: `tests/test_guardrails.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_guardrails.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.models import Market, Signal
from kalshi_bot.validation.guardrails import (
    GuardrailError,
    assert_on_main_branch,
    check_market_collision,
)


def _market(ticker: str) -> Market:
    return Market(
        ticker=ticker, title="test", category="Crypto",
        yes_bid=45, yes_ask=55, no_bid=45, no_ask=55,
        last_price=50, volume_24h=1000.0, liquidity_cents=5000, open_interest=100.0,
    )


def _signal(ticker: str) -> Signal:
    return Signal(
        ticker=ticker, title="test", side="yes",
        price=45, edge_cents=10, spread_cents=5, score=8.0, reason="test",
    )


# --- branch guardrail ---

def test_assert_on_main_branch_passes_on_main():
    mock_proc = MagicMock()
    mock_proc.stdout = "main\n"
    with patch("kalshi_bot.validation.guardrails.subprocess.run", return_value=mock_proc):
        assert_on_main_branch()  # must not raise


def test_assert_on_main_branch_raises_on_feature_branch():
    mock_proc = MagicMock()
    mock_proc.stdout = "feature/nba-strategy\n"
    with patch("kalshi_bot.validation.guardrails.subprocess.run", return_value=mock_proc):
        with pytest.raises(GuardrailError) as exc_info:
            assert_on_main_branch()
    assert "feature/nba-strategy" in str(exc_info.value)
    assert "BLOCKED" in str(exc_info.value)


def test_assert_on_main_branch_raises_on_detached_head():
    mock_proc = MagicMock()
    mock_proc.stdout = "HEAD\n"
    with patch("kalshi_bot.validation.guardrails.subprocess.run", return_value=mock_proc):
        with pytest.raises(GuardrailError):
            assert_on_main_branch()


# --- market collision ---

def test_no_collision_when_strategies_pick_different_markets():
    m_a = _market("KXBTC15M-26JAN011200-00")
    m_b = _market("KXBTC15M-26JAN011215-15")

    strat1 = MagicMock()
    strat2 = MagicMock()
    strat1.evaluate.side_effect = lambda m: _signal(m.ticker) if m.ticker == m_a.ticker else None
    strat2.evaluate.side_effect = lambda m: _signal(m.ticker) if m.ticker == m_b.ticker else None

    warnings = check_market_collision({"strat1": strat1, "strat2": strat2}, [m_a, m_b])
    assert warnings == []


def test_collision_detected_when_two_strategies_share_ticker():
    m = _market("KXBTC15M-26JAN011200-00")

    strat1 = MagicMock()
    strat2 = MagicMock()
    strat1.evaluate.return_value = _signal(m.ticker)
    strat2.evaluate.return_value = _signal(m.ticker)

    warnings = check_market_collision({"strat1": strat1, "strat2": strat2}, [m])
    assert len(warnings) == 1
    assert "KXBTC15M-26JAN011200-00" in warnings[0]
    assert "strat1" in warnings[0]
    assert "strat2" in warnings[0]


def test_no_collision_when_strategies_return_none():
    m = _market("KXBTC15M-26JAN011200-00")
    strat1 = MagicMock()
    strat2 = MagicMock()
    strat1.evaluate.return_value = None
    strat2.evaluate.return_value = None

    warnings = check_market_collision({"strat1": strat1, "strat2": strat2}, [m])
    assert warnings == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_guardrails.py -v
```

Expected: `ImportError` — `guardrails.py` does not exist yet.

- [ ] **Step 3: Implement `src/kalshi_bot/validation/guardrails.py`**

```python
from __future__ import annotations

import subprocess


class GuardrailError(Exception):
    pass


def assert_on_main_branch() -> None:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    branch = result.stdout.strip()
    if branch != "main":
        raise GuardrailError(
            f"BLOCKED: refusing to promote from branch '{branch}'. "
            "Checkout main before running validate.py."
        )


def check_market_collision(
    strategies: dict[str, object],
    markets: list,
) -> list[str]:
    ticker_to_names: dict[str, list[str]] = {}
    for name, strategy in strategies.items():
        for market in markets:
            sig = strategy.evaluate(market)
            if sig is not None:
                ticker_to_names.setdefault(sig.ticker, []).append(name)

    warnings = []
    for ticker, names in ticker_to_names.items():
        if len(names) >= 2:
            quoted = " and ".join(repr(n) for n in names)
            warnings.append(
                f"WARNING: market collision on {ticker} — "
                f"Both {quoted} are signalling this ticker. "
                "They will compete for the same fill. Verify this is intentional."
            )
    return warnings
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_guardrails.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/kalshi_bot/validation/guardrails.py tests/test_guardrails.py
git commit -m "feat: add guardrails (branch lock + market collision detection)"
```

---

## Task 4: BacktestWorker

**Files:**
- Create: `src/kalshi_bot/validation/backtest_agent.py`
- Create: `tests/test_backtest_agent.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_backtest_agent.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.validation.backtest_agent import BacktestResult, BacktestWorker


def _resolved_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal DataFrame as backtest_journal() would return."""
    defaults = {"status_bt": "resolved", "pnl_cents": 55, "ticker": "KXBTC15M-26JAN011200-00"}
    return pd.DataFrame([{**defaults, **r} for r in rows])


def test_backtest_worker_win_rate(tmp_path):
    journal = tmp_path / "journal.csv"
    journal.write_text("ts_utc,ticker\n")  # content doesn't matter; backtest_journal is mocked

    df = _resolved_df([
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011200-00"},   # win
        {"pnl_cents": -45, "ticker": "KXBTC15M-26JAN011215-15"},  # loss
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011230-30"},   # win
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011245-45"},   # win
    ])

    with patch("kalshi_bot.validation.backtest_agent.backtest_journal", return_value=df):
        result = BacktestWorker(journal_path=str(journal)).run()

    assert isinstance(result, BacktestResult)
    assert result.n_resolved == 4
    assert result.n_wins == 3
    assert result.win_rate == pytest.approx(0.75)


def test_backtest_worker_empty_resolved(tmp_path):
    journal = tmp_path / "journal.csv"
    journal.write_text("ts_utc,ticker\n")

    empty_df = pd.DataFrame(columns=["status_bt", "pnl_cents", "ticker"])

    with patch("kalshi_bot.validation.backtest_agent.backtest_journal", return_value=empty_df):
        result = BacktestWorker(journal_path=str(journal)).run()

    assert result.n_resolved == 0
    assert result.win_rate == 0.0


def test_backtest_worker_skips_unresolved(tmp_path):
    journal = tmp_path / "journal.csv"
    journal.write_text("ts_utc,ticker\n")

    df = pd.DataFrame([
        {"status_bt": "resolved", "pnl_cents": 55, "ticker": "KXBTC15M-26JAN011200-00"},
        {"status_bt": "skipped_future", "pnl_cents": None, "ticker": "KXBTC15M-26JAN021200-00"},
        {"status_bt": "error", "pnl_cents": None, "ticker": "KXBTC15M-26JAN031200-00"},
    ])

    with patch("kalshi_bot.validation.backtest_agent.backtest_journal", return_value=df):
        result = BacktestWorker(journal_path=str(journal)).run()

    assert result.n_resolved == 1


def test_backtest_worker_by_asset_grouping(tmp_path):
    journal = tmp_path / "journal.csv"
    journal.write_text("ts_utc,ticker\n")

    df = _resolved_df([
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011200-00"},
        {"pnl_cents": 55, "ticker": "KXBTC15M-26JAN011215-15"},
        {"pnl_cents": -45, "ticker": "KXETH15M-26JAN011200-00"},
    ])

    with patch("kalshi_bot.validation.backtest_agent.backtest_journal", return_value=df):
        result = BacktestWorker(journal_path=str(journal)).run()

    assert "KXBTC15M" in result.by_asset
    assert result.by_asset["KXBTC15M"]["count"] == 2
    assert result.by_asset["KXBTC15M"]["wins"] == 2
    assert result.by_asset["KXETH15M"]["wins"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_backtest_agent.py -v
```

Expected: `ImportError` — `backtest_agent.py` does not exist yet.

- [ ] **Step 3: Implement `src/kalshi_bot/validation/backtest_agent.py`**

```python
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# backtest.py lives at project root (not inside src/)
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest import backtest_journal  # noqa: E402


@dataclass
class BacktestResult:
    win_rate: float
    n_resolved: int
    n_wins: int
    by_asset: dict[str, dict] = field(default_factory=dict)


class BacktestWorker:
    def __init__(self, journal_path: str):
        self.journal_path = journal_path

    def run(self) -> BacktestResult:
        df = backtest_journal(self.journal_path)
        resolved = df[df["status_bt"] == "resolved"].copy()
        n_resolved = len(resolved)

        if n_resolved == 0:
            return BacktestResult(win_rate=0.0, n_resolved=0, n_wins=0)

        n_wins = int((resolved["pnl_cents"] > 0).sum())
        win_rate = n_wins / n_resolved

        resolved["asset"] = resolved["ticker"].str.extract(r"^(KX[A-Z0-9]+15M)")
        by_asset: dict[str, dict] = {}
        for asset, group in resolved.dropna(subset=["asset"]).groupby("asset"):
            wins = int((group["pnl_cents"] > 0).sum())
            by_asset[str(asset)] = {
                "count": len(group),
                "wins": wins,
                "win_rate": wins / len(group),
            }

        return BacktestResult(
            win_rate=win_rate,
            n_resolved=n_resolved,
            n_wins=n_wins,
            by_asset=by_asset,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_backtest_agent.py -v
```

Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add src/kalshi_bot/validation/backtest_agent.py tests/test_backtest_agent.py
git commit -m "feat: add BacktestWorker wrapping backtest_journal()"
```

---

## Task 5: StatisticsWorker

**Files:**
- Create: `src/kalshi_bot/validation/statistics_agent.py`
- Create: `tests/test_statistics_agent.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_statistics_agent.py`:

```python
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.validation.statistics_agent import StatisticsResult, StatisticsWorker


def test_consistent_shadow_passes_chi2_backtest():
    # shadow WR matches backtest WR → p should be high (fail to reject H₀)
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=60, shadow_n=100, backtest_wr=0.60)

    assert isinstance(result, StatisticsResult)
    assert result.p_backtest > 0.05


def test_divergent_shadow_fails_chi2_backtest():
    # shadow WR=30% vs backtest WR=70% → clearly inconsistent
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=30, shadow_n=100, backtest_wr=0.70)

    assert result.p_backtest < 0.05


def test_strong_shadow_passes_floor():
    # shadow WR=70% clearly beats floor of 0.50
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=70, shadow_n=100, backtest_wr=0.60)

    assert result.p_floor < 0.05


def test_weak_shadow_fails_floor():
    # shadow WR=45% does not beat floor of 0.55
    worker = StatisticsWorker(declared_floor_wr=0.55)
    result = worker.run(shadow_wins=45, shadow_n=100, backtest_wr=0.60)

    assert result.p_floor >= 0.05


def test_degenerate_backtest_wr_zero_does_not_crash():
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=0, shadow_n=100, backtest_wr=0.0)
    # degenerate backtest → treated as maximally inconsistent
    assert result.p_backtest == 0.0
    assert math.isinf(result.chi2_vs_backtest)


def test_degenerate_backtest_wr_one_does_not_crash():
    worker = StatisticsWorker(declared_floor_wr=0.50)
    result = worker.run(shadow_wins=100, shadow_n=100, backtest_wr=1.0)
    assert result.p_backtest == 0.0


def test_result_fields_are_floats():
    worker = StatisticsWorker(declared_floor_wr=0.55)
    result = worker.run(shadow_wins=60, shadow_n=100, backtest_wr=0.60)
    assert isinstance(result.chi2_vs_backtest, float)
    assert isinstance(result.p_backtest, float)
    assert isinstance(result.statistic_vs_floor, float)
    assert isinstance(result.p_floor, float)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_statistics_agent.py -v
```

Expected: `ImportError` — `statistics_agent.py` does not exist yet.

- [ ] **Step 3: Implement `src/kalshi_bot/validation/statistics_agent.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from scipy import stats


@dataclass
class StatisticsResult:
    chi2_vs_backtest: float
    p_backtest: float          # two-sided; PASS when > 0.05
    statistic_vs_floor: float  # binomtest proportion (shadow_wins / shadow_n)
    p_floor: float             # one-sided; PASS when < 0.05


class StatisticsWorker:
    def __init__(self, declared_floor_wr: float):
        self.declared_floor_wr = declared_floor_wr

    def run(
        self,
        shadow_wins: int,
        shadow_n: int,
        backtest_wr: float,
    ) -> StatisticsResult:
        shadow_losses = shadow_n - shadow_wins

        # Test 1: two-sided chi-square goodness-of-fit (shadow vs backtest distribution)
        f_exp_wins = backtest_wr * shadow_n
        f_exp_losses = (1.0 - backtest_wr) * shadow_n

        if f_exp_wins < 1e-10 or f_exp_losses < 1e-10:
            # Degenerate expected distribution — treat as maximally inconsistent
            chi2_stat = float("inf")
            p_backtest = 0.0
        else:
            chi2_result = stats.chisquare(
                f_obs=[shadow_wins, shadow_losses],
                f_exp=[f_exp_wins, f_exp_losses],
            )
            chi2_stat = float(chi2_result.statistic)
            p_backtest = float(chi2_result.pvalue)

        # Test 2: one-sided binomial test (shadow beats declared floor)
        # H₀: shadow_wr ≤ floor  H₁: shadow_wr > floor
        # p < 0.05 → evidence shadow beats floor → PASS
        binom_result = stats.binomtest(
            shadow_wins, shadow_n, self.declared_floor_wr, alternative="greater"
        )

        return StatisticsResult(
            chi2_vs_backtest=chi2_stat,
            p_backtest=p_backtest,
            statistic_vs_floor=float(binom_result.statistic),
            p_floor=float(binom_result.pvalue),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_statistics_agent.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/kalshi_bot/validation/statistics_agent.py tests/test_statistics_agent.py
git commit -m "feat: add StatisticsWorker (chi-square + one-sided binomtest)"
```

---

## Task 6: ShadowWorker

**Files:**
- Create: `src/kalshi_bot/validation/shadow_agent.py`
- Create: `tests/test_shadow_agent.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_shadow_agent.py`:

```python
from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.models import Market, Signal
from kalshi_bot.validation.shadow_agent import PendingFill, ShadowResult, ShadowWorker


def _market(ticker: str = "KXBTC15M-26JAN011200-00") -> Market:
    return Market(
        ticker=ticker, title="BTC 15m", category="Crypto",
        yes_bid=45, yes_ask=55, no_bid=45, no_ask=55,
        last_price=50, volume_24h=1000.0, liquidity_cents=5000, open_interest=100.0,
    )


def _signal(ticker: str = "KXBTC15M-26JAN011200-00") -> Signal:
    return Signal(
        ticker=ticker, title="BTC 15m", side="yes",
        price=45, edge_cents=10, spread_cents=5, score=8.0, reason="test",
    )


def _make_worker(tmp_path, strategy, market_data, min_fills=1):
    return ShadowWorker(
        strategy=strategy,
        market_data=market_data,
        shadow_journal_path=str(tmp_path / "shadow.csv"),
        min_fills=min_fills,
        poll_interval_seconds=0,
    )


def test_shadow_worker_exits_at_min_fills(tmp_path):
    market = _market()
    strategy = MagicMock()
    strategy.evaluate.return_value = _signal()
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market]

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)

    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(95000.0, 96000.0, 1)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        result = worker.run()

    assert isinstance(result, ShadowResult)
    assert result.n_fills == 1
    assert result.n_wins == 1
    assert result.win_rate == pytest.approx(1.0)


def test_shadow_worker_counts_losses(tmp_path):
    market = _market()
    strategy = MagicMock()
    strategy.evaluate.return_value = _signal()
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market]

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)

    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(96000.0, 95000.0, 0)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        result = worker.run()

    assert result.n_fills == 1
    assert result.n_wins == 0
    assert result.win_rate == pytest.approx(0.0)


def test_shadow_worker_skips_none_signals(tmp_path):
    market_a = _market("KXBTC15M-26JAN011200-00")
    market_b = _market("KXBTC15M-26JAN011215-15")

    strategy = MagicMock()
    strategy.evaluate.side_effect = lambda m: (
        _signal(m.ticker) if m.ticker == market_a.ticker else None
    )
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market_a, market_b]

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)
    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(95000.0, 96000.0, 1)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        result = worker.run()

    # Only market_a generated a signal → only 1 fill
    assert result.n_fills == 1


def test_shadow_worker_does_not_double_count_same_ticker(tmp_path):
    market = _market()
    strategy = MagicMock()
    strategy.evaluate.return_value = _signal()
    # Return same market twice in first call, then empty to stop accumulation
    # (won't actually get a second call because min_fills=1 exits after first resolve)
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market, market]  # duplicate

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)
    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(95000.0, 96000.0, 1)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        result = worker.run()

    assert result.n_fills == 1  # not 2


def test_shadow_worker_writes_journal_header(tmp_path):
    market = _market()
    strategy = MagicMock()
    strategy.evaluate.return_value = _signal()
    market_data = MagicMock()
    market_data.iter_open_markets.return_value = [market]

    expiry = datetime.now(timezone.utc) - timedelta(minutes=30)
    worker = _make_worker(tmp_path, strategy, market_data, min_fills=1)

    with patch("kalshi_bot.validation.shadow_agent.parse_market_ticker",
               return_value=("KXBTC15M", expiry, "00")), \
         patch("kalshi_bot.validation.shadow_agent.resolve_yes_outcome",
               return_value=(95000.0, 96000.0, 1)), \
         patch("kalshi_bot.validation.shadow_agent.asset_prefix_from_ticker",
               return_value="KXBTC15M"), \
         patch("kalshi_bot.validation.shadow_agent.COINBASE_PRODUCTS",
               {"KXBTC15M": "BTC-USD"}):
        worker.run()

    journal_path = tmp_path / "shadow.csv"
    assert journal_path.exists()
    with journal_path.open() as f:
        reader = csv.DictReader(f)
        assert "ticker" in (reader.fieldnames or [])
        assert "status" in (reader.fieldnames or [])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_shadow_agent.py -v
```

Expected: `ImportError` — `shadow_agent.py` does not exist yet.

- [ ] **Step 3: Implement `src/kalshi_bot/validation/shadow_agent.py`**

```python
from __future__ import annotations

import csv
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest import (  # noqa: E402
    COINBASE_PRODUCTS,
    asset_prefix_from_ticker,
    parse_market_ticker,
    pnl_for_trade,
    resolve_yes_outcome,
)

from ..models import Market, Signal


class StrategyProtocol(Protocol):
    def evaluate(self, market: Market) -> Signal | None: ...


_FIELDNAMES = [
    "ts_utc", "ticker", "side", "price", "expiry_time",
    "product", "status", "yes_outcome", "won", "pnl_cents",
]


@dataclass
class PendingFill:
    ticker: str
    side: str
    price: int
    expiry_time: datetime
    product: str


@dataclass
class ShadowResult:
    win_rate: float
    n_fills: int
    n_wins: int


class ShadowWorker:
    def __init__(
        self,
        strategy: StrategyProtocol,
        market_data,
        shadow_journal_path: str,
        min_fills: int = 100,
        poll_interval_seconds: float = 2.0,
    ):
        self.strategy = strategy
        self.market_data = market_data
        self.shadow_journal_path = Path(shadow_journal_path)
        self.min_fills = min_fills
        self.poll_interval_seconds = poll_interval_seconds
        self._pending: list[PendingFill] = []
        self._resolved_n = 0
        self._resolved_wins = 0
        self._ensure_journal()

    def _ensure_journal(self) -> None:
        self.shadow_journal_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.shadow_journal_path.exists() or self.shadow_journal_path.stat().st_size == 0:
            with self.shadow_journal_path.open("w", newline="") as f:
                csv.DictWriter(f, fieldnames=_FIELDNAMES).writeheader()

    def _append(self, row: dict) -> None:
        with self.shadow_journal_path.open("a", newline="") as f:
            csv.DictWriter(f, fieldnames=_FIELDNAMES, extrasaction="ignore").writerow(row)

    def _record_pending(self, fill: PendingFill) -> None:
        self._append({
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "ticker": fill.ticker, "side": fill.side, "price": fill.price,
            "expiry_time": fill.expiry_time.isoformat(), "product": fill.product,
            "status": "shadow_pending", "yes_outcome": "", "won": "", "pnl_cents": "",
        })

    def _try_resolve(self) -> None:
        now = datetime.now(timezone.utc)
        still_pending: list[PendingFill] = []
        for fill in self._pending:
            if fill.expiry_time > now:
                still_pending.append(fill)
                continue
            try:
                _, _, yes_outcome = resolve_yes_outcome(
                    ticker=fill.ticker,
                    product=fill.product,
                    expiry_time=fill.expiry_time,
                )
                won, pnl_cents = pnl_for_trade(fill.side, fill.price, yes_outcome)
                self._resolved_n += 1
                if won:
                    self._resolved_wins += 1
                self._append({
                    "ts_utc": datetime.now(timezone.utc).isoformat(),
                    "ticker": fill.ticker, "side": fill.side, "price": fill.price,
                    "expiry_time": fill.expiry_time.isoformat(), "product": fill.product,
                    "status": "shadow_win" if won else "shadow_loss",
                    "yes_outcome": yes_outcome, "won": str(won), "pnl_cents": pnl_cents,
                })
                logging.info(
                    "shadow: resolved %s won=%s n=%d/%d",
                    fill.ticker, won, self._resolved_n, self.min_fills,
                )
            except Exception as exc:
                logging.warning("shadow: could not resolve %s: %s", fill.ticker, exc)
                still_pending.append(fill)
        self._pending = still_pending

    def run(self) -> ShadowResult:
        seen_tickers: set[str] = set()

        while self._resolved_n < self.min_fills:
            try:
                markets = list(self.market_data.iter_open_markets(limit_per_page=200))
            except Exception as exc:
                logging.warning("shadow: market fetch failed: %s", exc)
                time.sleep(self.poll_interval_seconds)
                continue

            for market in markets:
                if market.ticker in seen_tickers:
                    continue
                sig = self.strategy.evaluate(market)
                if sig is None:
                    continue
                prefix = asset_prefix_from_ticker(market.ticker)
                if prefix is None:
                    continue
                try:
                    _, expiry_time, _ = parse_market_ticker(market.ticker)
                except Exception:
                    continue
                product = COINBASE_PRODUCTS.get(prefix)
                if not product:
                    continue
                fill = PendingFill(
                    ticker=market.ticker, side=sig.side, price=sig.price,
                    expiry_time=expiry_time, product=product,
                )
                self._pending.append(fill)
                seen_tickers.add(market.ticker)
                self._record_pending(fill)
                logging.info(
                    "shadow: recorded %s side=%s (pending=%d resolved=%d/%d)",
                    fill.ticker, fill.side, len(self._pending),
                    self._resolved_n, self.min_fills,
                )

            self._try_resolve()
            if self._resolved_n < self.min_fills:
                time.sleep(self.poll_interval_seconds)

        wr = self._resolved_wins / self._resolved_n if self._resolved_n else 0.0
        return ShadowResult(win_rate=wr, n_fills=self._resolved_n, n_wins=self._resolved_wins)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_shadow_agent.py -v
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/kalshi_bot/validation/shadow_agent.py tests/test_shadow_agent.py
git commit -m "feat: add ShadowWorker (live dry-run fill accumulator)"
```

---

## Task 7: ValidationPipeline

**Files:**
- Create: `src/kalshi_bot/validation/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipeline.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kalshi_bot.validation.backtest_agent import BacktestResult
from kalshi_bot.validation.guardrails import GuardrailError
from kalshi_bot.validation.pipeline import ValidationPipeline
from kalshi_bot.validation.report import ValidationReport
from kalshi_bot.validation.shadow_agent import ShadowResult
from kalshi_bot.validation.statistics_agent import StatisticsResult


def _make_pipeline(tmp_path, backtest_wr=0.61, shadow_wr=0.60,
                   shadow_n=105, backtest_n=142,
                   p_backtest=0.80, p_floor=0.02, declared_floor=0.55):
    bt_worker = MagicMock()
    bt_worker.run.return_value = BacktestResult(
        win_rate=backtest_wr, n_resolved=backtest_n,
        n_wins=int(backtest_wr * backtest_n),
    )
    sh_worker = MagicMock()
    sh_worker.run.return_value = ShadowResult(
        win_rate=shadow_wr, n_fills=shadow_n,
        n_wins=int(shadow_wr * shadow_n),
    )
    sh_worker.min_fills = 100

    stats_worker = MagicMock()
    stats_worker.declared_floor_wr = declared_floor
    stats_worker.run.return_value = StatisticsResult(
        chi2_vs_backtest=0.5, p_backtest=p_backtest,
        statistic_vs_floor=shadow_wr, p_floor=p_floor,
    )

    return ValidationPipeline(
        backtest_worker=bt_worker,
        shadow_worker=sh_worker,
        stats_worker=stats_worker,
        report_path=str(tmp_path / "report.json"),
        env_path=str(tmp_path / ".env"),
        check_branch=False,
    )


def test_pipeline_produces_pass_verdict(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    report = pipeline.run()
    assert report.overall_verdict == "PASS"
    assert report.blocking_reason == ""


def test_pipeline_fails_when_wr_delta_exceeds_tolerance(tmp_path):
    pipeline = _make_pipeline(tmp_path, backtest_wr=0.70, shadow_wr=0.60)
    report = pipeline.run()
    assert report.overall_verdict == "FAIL"
    assert "WR delta" in report.blocking_reason


def test_pipeline_fails_when_shadow_n_below_min(tmp_path):
    pipeline = _make_pipeline(tmp_path, shadow_n=50)
    report = pipeline.run()
    assert report.overall_verdict == "FAIL"
    assert "50" in report.blocking_reason


def test_pipeline_fails_when_chi2_backtest_fails(tmp_path):
    pipeline = _make_pipeline(tmp_path, p_backtest=0.01)
    report = pipeline.run()
    assert report.overall_verdict == "FAIL"
    assert "p_backtest" in report.blocking_reason or "consistent" in report.blocking_reason


def test_pipeline_fails_when_chi2_floor_fails(tmp_path):
    pipeline = _make_pipeline(tmp_path, p_floor=0.20)
    report = pipeline.run()
    assert report.overall_verdict == "FAIL"
    assert "floor" in report.blocking_reason


def test_pipeline_writes_report_json(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    pipeline.run()
    report_path = tmp_path / "report.json"
    assert report_path.exists()
    data = json.loads(report_path.read_text())
    assert data["overall_verdict"] == "PASS"


def test_maybe_promote_patches_dry_run(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("KALSHI_ENV=demo\nDRY_RUN=true\n")

    pipeline = _make_pipeline(tmp_path)
    pipeline.env_path = str(env_path)
    report = pipeline.run()

    promoted = pipeline.maybe_promote(report)
    assert promoted is True
    assert "DRY_RUN=false" in env_path.read_text()
    assert "DRY_RUN=true" not in env_path.read_text()


def test_maybe_promote_does_not_touch_env_on_fail(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("DRY_RUN=true\n")

    pipeline = _make_pipeline(tmp_path, shadow_n=50)  # forces FAIL
    pipeline.env_path = str(env_path)
    report = pipeline.run()

    promoted = pipeline.maybe_promote(report)
    assert promoted is False
    assert "DRY_RUN=true" in env_path.read_text()


def test_pipeline_raises_guardrail_error_on_wrong_branch(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    pipeline.check_branch = True

    mock_proc = MagicMock()
    mock_proc.stdout = "feature/test\n"
    with patch("kalshi_bot.validation.guardrails.subprocess.run", return_value=mock_proc):
        with pytest.raises(GuardrailError):
            pipeline.run()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_pipeline.py -v
```

Expected: `ImportError` — `pipeline.py` does not exist yet.

- [ ] **Step 3: Implement `src/kalshi_bot/validation/pipeline.py`**

```python
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from .backtest_agent import BacktestResult, BacktestWorker
from .guardrails import assert_on_main_branch, check_market_collision
from .report import ValidationReport
from .shadow_agent import ShadowResult, ShadowWorker
from .statistics_agent import StatisticsWorker

_WR_TOLERANCE = 0.05


class ValidationPipeline:
    def __init__(
        self,
        backtest_worker: BacktestWorker,
        shadow_worker: ShadowWorker,
        stats_worker: StatisticsWorker,
        report_path: str,
        env_path: str = ".env",
        check_branch: bool = True,
        collision_strategies: dict | None = None,
        collision_markets: list | None = None,
    ):
        self.backtest_worker = backtest_worker
        self.shadow_worker = shadow_worker
        self.stats_worker = stats_worker
        self.report_path = report_path
        self.env_path = env_path
        self.check_branch = check_branch
        self.collision_strategies = collision_strategies or {}
        self.collision_markets = collision_markets or []

    def run(self) -> ValidationReport:
        if self.check_branch:
            assert_on_main_branch()

        collision_warnings = check_market_collision(
            self.collision_strategies, self.collision_markets
        )
        for w in collision_warnings:
            logging.warning(w)

        backtest_result: BacktestResult | None = None
        shadow_result: ShadowResult | None = None

        with ThreadPoolExecutor(max_workers=2) as pool:
            bt_future = pool.submit(self.backtest_worker.run)
            sh_future = pool.submit(self.shadow_worker.run)
            for future in as_completed([bt_future, sh_future]):
                result = future.result()
                if future is bt_future:
                    backtest_result = result
                    logging.info("backtest: wr=%.3f n=%d", result.win_rate, result.n_resolved)
                else:
                    shadow_result = result
                    logging.info("shadow: wr=%.3f n=%d", result.win_rate, result.n_fills)

        stats_result = self.stats_worker.run(
            shadow_wins=shadow_result.n_wins,
            shadow_n=shadow_result.n_fills,
            backtest_wr=backtest_result.win_rate,
        )

        wr_delta = abs(shadow_result.win_rate - backtest_result.win_rate)
        wr_delta_passes = wr_delta <= _WR_TOLERANCE
        chi2_backtest_passes = stats_result.p_backtest > 0.05
        chi2_floor_passes = stats_result.p_floor < 0.05

        gates = [
            (
                shadow_result.n_fills >= self.shadow_worker.min_fills,
                f"shadow_n ({shadow_result.n_fills}) < min_fills ({self.shadow_worker.min_fills})",
            ),
            (
                wr_delta_passes,
                f"WR delta ({wr_delta:.3f}) > tolerance ({_WR_TOLERANCE})",
            ),
            (
                chi2_backtest_passes,
                f"shadow WR not consistent with backtest WR (p_backtest={stats_result.p_backtest:.4f} <= 0.05)",
            ),
            (
                chi2_floor_passes,
                f"shadow WR ({shadow_result.win_rate:.3f}) does not beat declared floor "
                f"({self.stats_worker.declared_floor_wr:.3f}) at p<0.05 "
                f"(p_floor={stats_result.p_floor:.4f})",
            ),
            (
                backtest_result.n_resolved >= 100,
                f"backtest_n ({backtest_result.n_resolved}) < 100",
            ),
        ]

        failed = [msg for passed, msg in gates if not passed]
        verdict = "PASS" if not failed else "FAIL"

        report = ValidationReport(
            backtest_wr=backtest_result.win_rate,
            backtest_n=backtest_result.n_resolved,
            shadow_wr=shadow_result.win_rate,
            shadow_n=shadow_result.n_fills,
            wr_delta=wr_delta,
            wr_delta_passes=wr_delta_passes,
            p_backtest=stats_result.p_backtest,
            chi2_backtest_passes=chi2_backtest_passes,
            declared_floor=self.stats_worker.declared_floor_wr,
            p_floor=stats_result.p_floor,
            chi2_floor_passes=chi2_floor_passes,
            overall_verdict=verdict,
            blocking_reason="; ".join(failed),
            market_collision_warnings=collision_warnings,
        )
        report.to_json(self.report_path)
        return report

    def maybe_promote(self, report: ValidationReport) -> bool:
        if report.overall_verdict != "PASS":
            return False
        from pathlib import Path
        env_path = Path(self.env_path)
        if env_path.exists():
            lines = env_path.read_text().splitlines(keepends=True)
            new_lines, found = [], False
            for line in lines:
                if line.startswith("DRY_RUN="):
                    new_lines.append("DRY_RUN=false\n")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append("DRY_RUN=false\n")
            env_path.write_text("".join(new_lines))
        else:
            env_path.write_text("DRY_RUN=false\n")
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pipeline.py -v
```

Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add src/kalshi_bot/validation/pipeline.py tests/test_pipeline.py
git commit -m "feat: add ValidationPipeline orchestrator with ThreadPoolExecutor"
```

---

## Task 8: Pytest Promotion Gates

**Files:**
- Create: `tests/test_validation_gates.py`

These are the actual promotion-blocking tests. They read from `VALIDATION_REPORT_PATH` env var (set by `validate.py` when invoking pytest) and skip if no report is present. The TDD steps below test the gate logic directly by writing report fixtures to `tmp_path`.

- [ ] **Step 1: Write the gate tests (these are both the TDD test and the production gate)**

Create `tests/test_validation_gates.py`:

```python
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _load_report() -> dict:
    report_path = os.environ.get("VALIDATION_REPORT_PATH", "logs/validation_report.json")
    path = Path(report_path)
    if not path.exists():
        pytest.skip(f"No validation report at {report_path}")
    return json.loads(path.read_text())


# ── Gate 1 ──────────────────────────────────────────────────────────────────

def test_min_shadow_fills():
    r = _load_report()
    assert r["shadow_n"] >= 100, (
        f"Insufficient shadow fills: {r['shadow_n']} < 100"
    )


# ── Gate 2 ──────────────────────────────────────────────────────────────────

def test_wr_delta_within_tolerance():
    r = _load_report()
    delta = abs(r["shadow_wr"] - r["backtest_wr"])
    assert delta <= 0.05, (
        f"WR delta {delta:.3f} exceeds 5% tolerance "
        f"(shadow={r['shadow_wr']:.3f}, backtest={r['backtest_wr']:.3f})"
    )


# ── Gate 3 ──────────────────────────────────────────────────────────────────

def test_chi2_shadow_vs_backtest():
    r = _load_report()
    assert r["p_backtest"] > 0.05, (
        f"Shadow WR is statistically inconsistent with backtest WR "
        f"(p={r['p_backtest']:.4f} ≤ 0.05)"
    )


# ── Gate 4 ──────────────────────────────────────────────────────────────────

def test_chi2_shadow_vs_floor():
    r = _load_report()
    assert r["p_floor"] < 0.05, (
        f"Shadow WR does not beat declared floor {r['declared_floor']} "
        f"(p={r['p_floor']:.4f} ≥ 0.05)"
    )


# ── Gate 5 ──────────────────────────────────────────────────────────────────

def test_backtest_min_resolved():
    r = _load_report()
    assert r["backtest_n"] >= 100, (
        f"Backtest resolved fewer than 100 trades: {r['backtest_n']}"
    )
```

- [ ] **Step 2: Verify gates skip gracefully when no report exists**

```bash
pytest tests/test_validation_gates.py -v
```

Expected: `5 skipped` (no report file present yet).

- [ ] **Step 3: Write a PASS report fixture and verify all 5 gates pass**

```bash
python - <<'EOF'
import json, pathlib
pathlib.Path("logs").mkdir(exist_ok=True)
data = {
    "backtest_wr": 0.61, "backtest_n": 142,
    "shadow_wr": 0.60, "shadow_n": 105,
    "wr_delta": 0.01, "wr_delta_passes": True,
    "p_backtest": 0.80, "chi2_backtest_passes": True,
    "declared_floor": 0.55, "p_floor": 0.02, "chi2_floor_passes": True,
    "overall_verdict": "PASS", "blocking_reason": "",
    "market_collision_warnings": []
}
pathlib.Path("logs/validation_report.json").write_text(json.dumps(data, indent=2))
print("wrote pass report")
EOF
VALIDATION_REPORT_PATH=logs/validation_report.json pytest tests/test_validation_gates.py -v
```

Expected: `5 passed`

- [ ] **Step 4: Verify each gate fails on a bad report**

```bash
python - <<'EOF'
import json, pathlib
data = {
    "backtest_wr": 0.61, "backtest_n": 142,
    "shadow_wr": 0.50, "shadow_n": 50,       # fails gates 1, 2
    "wr_delta": 0.11, "wr_delta_passes": False,
    "p_backtest": 0.03, "chi2_backtest_passes": False,  # fails gate 3
    "declared_floor": 0.55, "p_floor": 0.20, "chi2_floor_passes": False,  # fails gate 4
    "overall_verdict": "FAIL", "blocking_reason": "...",
    "market_collision_warnings": []
}
pathlib.Path("logs/validation_report.json").write_text(json.dumps(data, indent=2))
print("wrote fail report")
EOF
VALIDATION_REPORT_PATH=logs/validation_report.json pytest tests/test_validation_gates.py -v
```

Expected: `5 failed` — each gate failure is isolated to the right test.

- [ ] **Step 5: Clean up the test report**

```bash
rm logs/validation_report.json
```

- [ ] **Step 6: Commit**

```bash
git add tests/test_validation_gates.py
git commit -m "feat: add pytest promotion gates (5 gates, reads VALIDATION_REPORT_PATH)"
```

---

## Task 9: CLI Entry Point

**Files:**
- Create: `validate.py`

- [ ] **Step 1: Write validate.py**

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

# Ensure src/ is importable when running from project root
_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kalshi_bot.client import KalshiHttpClient
from kalshi_bot.config import Settings
from kalshi_bot.crypto_strategy import CryptoProbStrategy
from kalshi_bot.market_data import MarketDataService
from kalshi_bot.mean_reversion_strategy import MeanReversionStrategy
from kalshi_bot.validation.backtest_agent import BacktestWorker
from kalshi_bot.validation.guardrails import GuardrailError
from kalshi_bot.validation.pipeline import ValidationPipeline
from kalshi_bot.validation.shadow_agent import ShadowWorker
from kalshi_bot.validation.statistics_agent import StatisticsWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _build_strategy(name: str, settings: Settings, client: KalshiHttpClient):
    if name == "mean_reversion":
        return MeanReversionStrategy()
    if name == "crypto_prob":
        return CryptoProbStrategy(
            client,
            min_edge_cents=settings.crypto_min_edge_cents,
            max_spread_cents=settings.crypto_max_spread_cents,
            min_score=settings.crypto_min_score,
            momentum_scaling_factor=settings.momentum_scaling_factor,
        )
    raise ValueError(f"Unknown strategy: {name!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a trading strategy before going live."
    )
    parser.add_argument(
        "--strategy", required=True,
        choices=["mean_reversion", "crypto_prob"],
        help="Strategy to validate",
    )
    parser.add_argument(
        "--declared-floor", type=float, default=0.55,
        help="Declared win rate floor the strategy must beat (default: 0.55)",
    )
    parser.add_argument(
        "--min-fills", type=int, default=100,
        help="Minimum shadow fills required before gates run (default: 100)",
    )
    parser.add_argument(
        "--journal", default="logs/trade_journal.csv",
        help="Path to trade journal for backtest agent",
    )
    parser.add_argument(
        "--shadow-journal", default="logs/shadow_journal.csv",
        help="Path for shadow agent output journal",
    )
    parser.add_argument(
        "--report", default="logs/validation_report.json",
        help="Path to write validation report JSON",
    )
    parser.add_argument(
        "--env", default=".env",
        help="Path to .env file (patched on promotion)",
    )
    parser.add_argument(
        "--no-branch-check", action="store_true",
        help="Skip git branch guardrail (testing only)",
    )
    args = parser.parse_args()

    settings = Settings()
    public_client = KalshiHttpClient(settings.base_url)
    market_data = MarketDataService(public_client, markets_per_event=settings.markets_per_event)
    strategy = _build_strategy(args.strategy, settings, public_client)

    pipeline = ValidationPipeline(
        backtest_worker=BacktestWorker(journal_path=args.journal),
        shadow_worker=ShadowWorker(
            strategy=strategy,
            market_data=market_data,
            shadow_journal_path=args.shadow_journal,
            min_fills=args.min_fills,
        ),
        stats_worker=StatisticsWorker(declared_floor_wr=args.declared_floor),
        report_path=args.report,
        env_path=args.env,
        check_branch=not args.no_branch_check,
    )

    try:
        report = pipeline.run()
    except GuardrailError as exc:
        print(str(exc))
        return 1

    print("\n" + "=" * 60)
    print(f"  Backtest  WR: {report.backtest_wr:.1%}  (n={report.backtest_n})")
    print(f"  Shadow    WR: {report.shadow_wr:.1%}  (n={report.shadow_n})")
    print(f"  WR delta:     {report.wr_delta:.1%}  [≤5% required]")
    print(f"  p_backtest:   {report.p_backtest:.4f}  [>0.05 required]")
    print(f"  p_floor:      {report.p_floor:.4f}  [<0.05 required]")
    print(f"  Floor:        {report.declared_floor:.1%}")
    for w in report.market_collision_warnings:
        print(f"  {w}")
    print("=" * 60)

    report_path_abs = str(Path(args.report).resolve())
    pytest_result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_validation_gates.py", "-v", "--tb=short"],
        env={**os.environ, "VALIDATION_REPORT_PATH": report_path_abs},
        cwd=str(Path(__file__).parent),
    )

    if pytest_result.returncode == 0:
        pipeline.maybe_promote(report)
        print("\n✓  PROMOTED — DRY_RUN=false written to .env")
        return 0
    else:
        print(f"\n✗  BLOCKED — {report.blocking_reason}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify the CLI help works**

```bash
python validate.py --help
```

Expected: prints usage with all flags, no ImportError.

- [ ] **Step 3: Dry-run the CLI with no-branch-check and a pre-written PASS report to confirm gate invocation**

First write a PASS report:
```bash
python - <<'EOF'
import json, pathlib
pathlib.Path("logs").mkdir(exist_ok=True)
data = {
    "backtest_wr": 0.61, "backtest_n": 142,
    "shadow_wr": 0.60, "shadow_n": 105,
    "wr_delta": 0.01, "wr_delta_passes": True,
    "p_backtest": 0.80, "chi2_backtest_passes": True,
    "declared_floor": 0.55, "p_floor": 0.02, "chi2_floor_passes": True,
    "overall_verdict": "PASS", "blocking_reason": "",
    "market_collision_warnings": []
}
pathlib.Path("logs/validation_report.json").write_text(json.dumps(data, indent=2))
EOF
```

Then verify the gate runner works (bypass the long-running pipeline by pointing at the pre-written report):
```bash
VALIDATION_REPORT_PATH=logs/validation_report.json \
  python -m pytest tests/test_validation_gates.py -v
```

Expected: `5 passed`

- [ ] **Step 4: Clean up the test report**

```bash
rm logs/validation_report.json
```

- [ ] **Step 5: Run the full unit test suite to confirm nothing is broken**

```bash
pytest tests/ -v --ignore=tests/test_validation_gates.py
```

Expected: all tests pass (gates are skipped because no report exists).

- [ ] **Step 6: Commit**

```bash
git add validate.py
git commit -m "feat: add validate.py CLI (strategy validation pipeline entry point)"
```

---

## Final Smoke Test

- [ ] **Verify the complete test suite passes**

```bash
pytest tests/ -v --ignore=tests/test_validation_gates.py
```

Expected: all tests pass.

- [ ] **Verify CLI is importable and shows help**

```bash
python validate.py --help
```

Expected: clean usage output.

- [ ] **Verify the branch guardrail fires on a non-main branch (optional, if on a feature branch)**

```bash
git checkout -b test-guardrail-branch
python validate.py --strategy mean_reversion 2>&1 | head -5
git checkout main
git branch -d test-guardrail-branch
```

Expected first line: `BLOCKED: refusing to promote from branch 'test-guardrail-branch'...`
