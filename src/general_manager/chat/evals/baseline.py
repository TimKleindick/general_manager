"""Baseline comparison for chat readiness loops."""

from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from general_manager.chat.evals.fingerprints import selection_manifest_is_valid


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
    failure_classes: dict[str, int] = field(default_factory=dict)
    failure_class_cases: dict[str, list[str]] = field(default_factory=dict)
    native_passed: int = 0
    recovered_passed: int = 0
    recovery_total: int = 0
    recovered_cases: list[str] = field(default_factory=list)
    forbidden_recovery_events: list[str] = field(default_factory=list)
    forbidden_recovery_total: int = 0
    forbidden_recovered_cases: list[str] = field(default_factory=list)
    # Missing in historical JSON: retain its actual legacy semantics on load.
    evaluation_contract: str = "chat-evaluation/1"
    failed: int = 0
    unscored: int = 0
    selection_manifest: dict[str, Any] | None = None
    run_status: str = "completed"

    @property
    def pass_rate(self) -> float:
        """Return the fixed-denominator pass rate; an empty run is not a pass."""
        return 0.0 if self.total == 0 else self.passed / self.total


@dataclass(frozen=True)
class BaselineComparison:
    """Regression status relative to a previous accepted run."""

    regressed: bool
    pass_rate_delta: float
    messages: list[str]
    comparable: bool = True


_SOFT_DIAGNOSTIC_ALLOWLIST = frozenset({"prompt/strategy_deviation"})


def compare_to_baseline(
    current: ReadinessSummary,
    baseline: ReadinessSummary,
) -> BaselineComparison:
    """Compare current readiness to a previous accepted summary."""
    messages: list[str] = []
    delta = current.pass_rate - baseline.pass_rate
    if current.evaluation_contract != baseline.evaluation_contract:
        return BaselineComparison(
            regressed=False,
            pass_rate_delta=round(delta, 6),
            messages=[
                "Evaluation contracts differ; establish a new baseline without rewriting the historical report."
            ],
            comparable=False,
        )
    for summary in (current, baseline):
        manifest = summary.selection_manifest
        if (
            summary.total <= 0
            or not selection_manifest_is_valid(manifest)
            or len(manifest["cases"]) != summary.total
            or sorted({case["dataset"] for case in manifest["cases"]})
            != sorted(summary.datasets)
        ):
            return BaselineComparison(
                regressed=False,
                pass_rate_delta=round(delta, 6),
                messages=[
                    "Evaluation selection is empty or lacks verified cohort metadata; establish a bound baseline without rewriting history."
                ],
                comparable=False,
            )
    if (
        current.selection_manifest != baseline.selection_manifest
        or current.fixture != baseline.fixture
        or current.gate != baseline.gate
    ):
        return BaselineComparison(
            regressed=False,
            pass_rate_delta=round(delta, 6),
            messages=[
                "Evaluation cohorts, fixtures or gates differ; these runs are not comparable."
            ],
            comparable=False,
        )
    if delta < 0:
        messages.append(
            "Overall pass rate regressed from "
            f"{_pct(baseline.pass_rate)} to {_pct(current.pass_rate)}."
        )

    if delta >= 0:
        allow_soft_diagnostics = baseline.pass_rate == 1.0 and current.pass_rate == 1.0
        baseline_counts = _flatten_diagnostics(baseline.diagnostics)
        current_counts = _flatten_diagnostics(current.diagnostics)
        for key, count in sorted(current_counts.items()):
            previous = baseline_counts.get(key, 0)
            if (
                previous == 0
                and count > 0
                and not (allow_soft_diagnostics and key in _SOFT_DIAGNOSTIC_ALLOWLIST)
            ):
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
