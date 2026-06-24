"""Production-readiness loop helpers for chat evals."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
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
from general_manager.chat.evals.diagnostics import FailureDiagnostic
from general_manager.chat.evals.diagnostics import summarize_failure_class_cases
from general_manager.chat.evals.diagnostics import summarize_failure_classes
from general_manager.chat.evals.diagnostics import summarize_diagnostics
from general_manager.chat.evals.runner import EvalResult, is_forbidden_recovery_event

_ANSWER_PROMPT_RECOVERY_EVENTS = frozenset(
    {
        "answer_without_query",
        "empty_after_tool",
        "failed_query",
        "missing_tool_call",
        "tool_bridge_answer",
        "unavailable_manager_echo",
        "unavailable_manager_echo_final_retry",
        "unavailable_manager_echo_minimal_retry",
        "unavailable_manager_echo_retry",
    }
)


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
    diagnostics = _classify_results(results, include_passing=True)
    failure_diagnostics = _classify_results(results, include_passing=False)
    contract_total = sum(1 for result in results if result.contract_score is not None)
    contract_passed = sum(
        1
        for result in results
        if result.contract_score is not None and result.contract_score.passed
    )
    recovered_results = [result for result in results if result.recovery_events]
    forbidden_recovery_by_case = {
        result.case.name: [
            event
            for event in result.recovery_events
            if is_forbidden_recovery_event(event)
        ]
        for result in results
    }
    forbidden_recovery_by_case = {
        case: events for case, events in forbidden_recovery_by_case.items() if events
    }
    forbidden_recovery_events = [
        event for events in forbidden_recovery_by_case.values() for event in events
    ]
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
        failure_classes=summarize_failure_classes(failure_diagnostics),
        failure_class_cases=summarize_failure_class_cases(failure_diagnostics),
        native_passed=sum(
            1 for result in results if result.passed and not result.recovery_events
        ),
        recovered_passed=sum(1 for result in recovered_results if result.passed),
        recovery_total=len(recovered_results),
        recovered_cases=[result.case.name for result in recovered_results],
        forbidden_recovery_events=sorted(dict.fromkeys(forbidden_recovery_events)),
        forbidden_recovery_total=len(forbidden_recovery_events),
        forbidden_recovered_cases=list(forbidden_recovery_by_case),
    )


def _classify_results(
    results: list[EvalResult],
    *,
    include_passing: bool,
) -> list[FailureDiagnostic]:
    diagnostics: list[FailureDiagnostic] = []
    for result in results:
        if (include_passing or not result.passed) and (
            diagnostic := classify_result(result)
        ):
            diagnostics.append(_promote_repeated_answer_grounding(diagnostic, result))
        forbidden_events = [
            event
            for event in result.recovery_events
            if is_forbidden_recovery_event(event)
        ]
        if forbidden_events:
            diagnostics.append(
                FailureDiagnostic(
                    case=result.case.name,
                    owner="harness",
                    category="forbidden_recovery",
                    severity="hard",
                    failure_class="eval_or_harness",
                    message=", ".join(forbidden_events),
                    next_action=(
                        "Remove demo-only recovery or answer rewriting. Production "
                        "gates must fail instead of overriding the LLM answer."
                    ),
                )
            )
    return diagnostics


def _promote_repeated_answer_grounding(
    diagnostic: FailureDiagnostic,
    result: EvalResult,
) -> FailureDiagnostic:
    if diagnostic.failure_class != "answer_grounding":
        return diagnostic
    prompt_attempts = sum(
        1 for event in result.recovery_events if event in _ANSWER_PROMPT_RECOVERY_EVENTS
    )
    if prompt_attempts < 2:
        return diagnostic
    return replace(
        diagnostic,
        category="model_demo_reliability",
        failure_class="model_demo_reliability",
        next_action=(
            "Treat this as local-model demo reliability after repeated prompt-only "
            "attempts. Do not add harness answer overrides; use a stronger demo "
            "model or simplify the demo question."
        ),
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
    if summary.forbidden_recovery_total:
        report_lines.append("")
        report_lines.append("Forbidden recovery:")
        report_lines.append(
            "- Forbidden events: " + ", ".join(summary.forbidden_recovery_events)
        )
        report_lines.append(
            "- Forbidden cases: " + ", ".join(summary.forbidden_recovered_cases)
        )
    if summary.failure_classes:
        report_lines.append("")
        report_lines.append("Failure classes:")
        for failure_class, count in summary.failure_classes.items():
            cases = ", ".join(summary.failure_class_cases.get(failure_class, []))
            report_lines.append(f"- {failure_class}: {count} ({cases})")
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
    if summary.forbidden_recovery_total:
        return 1
    if fail_on_regression and comparison is not None and comparison.regressed:
        return 1
    return 0
