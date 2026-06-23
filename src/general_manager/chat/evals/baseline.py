"""Baseline comparison for chat readiness loops."""

from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path


@dataclass(frozen=True)
class ReadinessSummary:
    """Machine-readable summary for one readiness loop run."""

    run_hash: str
    gate: str
    provider: str
    model: str
    fixture: str
    datasets: list[str]
    tier: int | None
    total: int
    passed: int
    product_contract_total: int
    product_contract_passed: int
    diagnostics: dict[str, dict[str, int]]
    native_passed: int = 0
    recovered_passed: int = 0
    recovery_total: int = 0
    recovered_cases: list[str] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        """Return the overall pass rate, treating empty runs as fully passing."""
        return 1.0 if self.total == 0 else self.passed / self.total


@dataclass(frozen=True)
class BaselineComparison:
    """Regression status relative to a previous accepted run."""

    regressed: bool
    pass_rate_delta: float
    messages: list[str]


def compare_to_baseline(
    current: ReadinessSummary,
    baseline: ReadinessSummary,
) -> BaselineComparison:
    """Compare current readiness to a previous accepted summary."""
    messages: list[str] = []
    delta = current.pass_rate - baseline.pass_rate
    if delta < 0:
        messages.append(
            "Overall pass rate regressed from "
            f"{_pct(baseline.pass_rate)} to {_pct(current.pass_rate)}."
        )

    if delta >= 0:
        baseline_counts = _flatten_diagnostics(baseline.diagnostics)
        current_counts = _flatten_diagnostics(current.diagnostics)
        for key, count in sorted(current_counts.items()):
            previous = baseline_counts.get(key, 0)
            if previous == 0 and count > 0:
                messages.append(f"New diagnostic category {key} appeared {count} time.")

    return BaselineComparison(
        regressed=bool(messages),
        pass_rate_delta=round(delta, 6),
        messages=messages,
    )


def load_summary(path: Path | str) -> ReadinessSummary:
    """Load a readiness summary JSON file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ReadinessSummary(**data)


def write_summary(path: Path | str, summary: ReadinessSummary) -> None:
    """Write a readiness summary JSON file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(asdict(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _flatten_diagnostics(diagnostics: dict[str, dict[str, int]]) -> dict[str, int]:
    output: dict[str, int] = {}
    for owner, categories in diagnostics.items():
        for category, count in categories.items():
            output[f"{owner}/{category}"] = count
    return output


def _pct(value: float) -> str:
    return f"{value:.0%}"
