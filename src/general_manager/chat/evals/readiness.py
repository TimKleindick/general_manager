"""Production-readiness loop helpers for chat evals."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from general_manager.chat.evals.baseline import (
    BaselineComparison,
    ReadinessSummary,
    compare_to_baseline,
    load_summary,
    write_summary,
)
from general_manager.chat.evals.diagnostics import classify_result
from general_manager.chat.evals.diagnostics import summarize_diagnostics
from general_manager.chat.evals.runner import EvalResult


@dataclass(frozen=True)
class ReadinessConfig:
    """Configuration for one readiness loop run."""

    gate: str
    provider: str
    model: str
    fixture: str
    datasets: list[str]
    tier: int | None
    tags: list[str]
    output_dir: Path
    baseline_json: Path | None
    fail_on_regression: bool
    live: bool


def build_summary(
    *,
    gate: str,
    run_metadata: dict[str, Any],
    results: list[EvalResult],
) -> ReadinessSummary:
    """Build a machine-readable readiness summary from eval results."""
    diagnostics = [diagnostic for r in results if (diagnostic := classify_result(r))]
    contract_total = sum(1 for result in results if result.contract_score is not None)
    contract_passed = sum(
        1
        for result in results
        if result.contract_score is not None and result.contract_score.passed
    )
    recovered_results = [result for result in results if result.recovery_events]
    return ReadinessSummary(
        run_hash=str(run_metadata["run_hash"]),
        gate=gate,
        provider=str(run_metadata["provider"]),
        model=str(run_metadata["model"]),
        fixture=str(run_metadata["fixture"]),
        datasets=[str(item) for item in run_metadata["datasets"]],
        tier=run_metadata.get("tier"),
        total=len(results),
        passed=sum(1 for result in results if result.passed),
        product_contract_total=contract_total,
        product_contract_passed=contract_passed,
        diagnostics=summarize_diagnostics(diagnostics),
        native_passed=sum(
            1 for result in results if result.passed and not result.recovery_events
        ),
        recovered_passed=sum(1 for result in recovered_results if result.passed),
        recovery_total=len(recovered_results),
        recovered_cases=[result.case.name for result in recovered_results],
    )


def compare_summary_if_requested(
    *,
    config: ReadinessConfig,
    summary: ReadinessSummary,
) -> BaselineComparison | None:
    """Compare the current summary to a baseline when one exists."""
    if config.baseline_json is None or not config.baseline_json.exists():
        return None
    return compare_to_baseline(summary, load_summary(config.baseline_json))


def write_readiness_artifacts(
    *,
    config: ReadinessConfig,
    summary: ReadinessSummary,
    report: str,
    comparison: BaselineComparison | None,
) -> None:
    """Write readiness summary and markdown report artifacts."""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    write_summary(config.output_dir / "summary.json", summary)
    report_lines = [report]
    if summary.recovery_total:
        report_lines.append("")
        report_lines.append("Harness recovery:")
        report_lines.append(f"- Native passes: {summary.native_passed}")
        report_lines.append(
            f"- Recovered passes: {summary.recovered_passed} / {summary.recovery_total}"
        )
        report_lines.append("- Recovered cases: " + ", ".join(summary.recovered_cases))
    if comparison is not None:
        report_lines.append("")
        report_lines.append("Baseline comparison:")
        if comparison.messages:
            report_lines.extend(f"- {message}" for message in comparison.messages)
        else:
            report_lines.append("- No baseline regression detected.")
    (config.output_dir / "report.md").write_text(
        "\n".join(report_lines).rstrip() + "\n",
        encoding="utf-8",
    )


def readiness_exit_code(
    *,
    summary: ReadinessSummary,
    comparison: BaselineComparison | None,
    fail_on_regression: bool,
) -> int:
    """Return the readiness process exit code for a completed run."""
    if summary.passed != summary.total:
        return 1
    if fail_on_regression and comparison is not None and comparison.regressed:
        return 1
    return 0
