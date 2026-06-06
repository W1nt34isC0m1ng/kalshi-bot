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
