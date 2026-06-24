"""Failure diagnostics for chat readiness evals."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Literal

if TYPE_CHECKING:
    from general_manager.chat.evals.runner import EvalResult

DiagnosticOwner = Literal[
    "prompt", "tool_schema", "harness", "runtime", "provider", "dataset"
]
DiagnosticSeverity = Literal["hard", "soft"]
DiagnosticFailureClass = Literal[
    "eval_or_harness",
    "tool_or_schema_retry",
    "answer_grounding",
    "model_demo_reliability",
]


@dataclass(frozen=True)
class FailureDiagnostic:
    """Actionable owner/category classification for one eval failure."""

    case: str
    owner: DiagnosticOwner
    category: str
    severity: DiagnosticSeverity
    failure_class: DiagnosticFailureClass
    message: str
    next_action: str


def classify_result(result: EvalResult) -> FailureDiagnostic | None:
    """Classify the highest-signal failure or strategy deviation for one result."""
    if result.error:
        return FailureDiagnostic(
            case=result.case.name,
            owner="provider",
            category="provider_error",
            severity="hard",
            failure_class="eval_or_harness",
            message=result.error,
            next_action=(
                "Verify the provider is reachable, the model is installed, and the "
                "readiness command uses the intended base URL."
            ),
        )

    if (
        result.answer_score is not None
        and not result.answer_score.passed
        and (result.result_score is None or result.result_score.passed)
    ):
        return _answer_quality_diagnostic(result)

    contract = result.contract_score
    if contract is not None:
        for violation in contract.violations:
            diagnostic = _classify_contract_message(
                result.case.name, violation, hard=True
            )
            if diagnostic is not None:
                return diagnostic
        for deviation in contract.strategy_deviations:
            diagnostic = _classify_contract_message(
                result.case.name, deviation, hard=False
            )
            if diagnostic is not None:
                return diagnostic

    if result.tool_score is not None and not result.tool_score.passed:
        message = "; ".join(result.tool_score.mismatches)
        return FailureDiagnostic(
            case=result.case.name,
            owner="prompt",
            category="tool_sequence_mismatch",
            severity="hard",
            failure_class="eval_or_harness",
            message=message,
            next_action=(
                "Check whether the product contract already captures this behavior. "
                "If yes, remove the legacy sequence expectation; otherwise tighten "
                "tool-decision prompt text."
            ),
        )

    if result.result_score is not None and not result.result_score.passed:
        message = (
            f"missing={result.result_score.missing}; "
            f"unexpected={result.result_score.unexpected}"
        )
        return FailureDiagnostic(
            case=result.case.name,
            owner="tool_schema",
            category="wrong_query_result",
            severity="hard",
            failure_class="tool_or_schema_retry",
            message=message,
            next_action=(
                "Inspect the trace query arguments. Prefer tool-side normalization "
                "for harmless formatting mistakes and prompt examples for semantic "
                "mistakes."
            ),
        )

    if result.answer_score is not None and not result.answer_score.passed:
        return _answer_quality_diagnostic(result)

    return None


def _answer_quality_diagnostic(result: EvalResult) -> FailureDiagnostic:
    if result.answer_score is None:
        msg = "answer score unavailable"
    else:
        msg = (
            f"missing={result.answer_score.missing}; "
            f"unexpected={result.answer_score.unexpected}"
        )
    return FailureDiagnostic(
        case=result.case.name,
        owner="prompt",
        category="answer_quality",
        severity="hard",
        failure_class="answer_grounding",
        message=msg,
        next_action=(
            "Strengthen answer rules or examples while keeping the answer "
            "grounded in tool JSON."
        ),
    )


def _classify_contract_message(
    case_name: str,
    message: str,
    *,
    hard: bool,
) -> FailureDiagnostic | None:
    severity: DiagnosticSeverity = "hard" if hard else "soft"
    if "tool call missing" in message:
        return FailureDiagnostic(
            case=case_name,
            owner="prompt",
            category="missing_required_tool" if hard else "strategy_deviation",
            severity=severity,
            failure_class="tool_or_schema_retry",
            message=message,
            next_action=(
                "Tighten tool-decision prompt or add missing-tool recovery before "
                "changing the dataset contract."
            ),
        )
    if message.startswith("Forbidden tool called: mutate"):
        return FailureDiagnostic(
            case=case_name,
            owner="runtime",
            category="forbidden_tool",
            severity=severity,
            failure_class="eval_or_harness",
            message=message,
            next_action=(
                "Add or verify mutation safety checks in the runtime harness and "
                "keep the prompt's mutation safety section explicit."
            ),
        )
    if message.startswith(("Missing result value:", "Unexpected result value:")):
        return FailureDiagnostic(
            case=case_name,
            owner="tool_schema",
            category="result_contract",
            severity=severity,
            failure_class="tool_or_schema_retry",
            message=message,
            next_action=(
                "Inspect tool calls and GraphQL results. Fix tool argument "
                "normalization or schema summaries before weakening expected values."
            ),
        )
    if message.startswith(
        (
            "Answer contradicts required result value:",
            "Answer defers after a successful query",
            "Answer includes raw query syntax after a successful query",
            "Answer omits required result value:",
            "Missing answer text:",
            "Unexpected answer text:",
        )
    ):
        return FailureDiagnostic(
            case=case_name,
            owner="prompt",
            category="answer_contract",
            severity=severity,
            failure_class="answer_grounding",
            message=message,
            next_action=(
                "Improve answer-grounding instructions or examples so returned "
                "values are copied into the final answer."
            ),
        )
    return FailureDiagnostic(
        case=case_name,
        owner="dataset",
        category="uncategorized_contract",
        severity=severity,
        failure_class="eval_or_harness",
        message=message,
        next_action=(
            "Review the product contract wording and add a classifier branch for "
            "this failure if it represents a stable production concern."
        ),
    )


def summarize_diagnostics(
    diagnostics: list[FailureDiagnostic],
) -> dict[str, dict[str, int]]:
    """Return nested counts grouped by owner and category."""
    grouped: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for diagnostic in diagnostics:
        grouped[diagnostic.owner][diagnostic.category] += 1
    return {
        owner: dict(categories)
        for owner, categories in sorted(grouped.items(), key=lambda item: item[0])
    }


def summarize_failure_classes(
    diagnostics: list[FailureDiagnostic],
) -> dict[str, int]:
    """Return hard failure counts grouped by production-readiness class."""
    grouped: defaultdict[str, int] = defaultdict(int)
    for diagnostic in diagnostics:
        if diagnostic.severity != "hard":
            continue
        grouped[diagnostic.failure_class] += 1
    return dict(sorted(grouped.items(), key=lambda item: item[0]))


def summarize_failure_class_cases(
    diagnostics: list[FailureDiagnostic],
) -> dict[str, list[str]]:
    """Return hard failure cases grouped by production-readiness class."""
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for diagnostic in diagnostics:
        if diagnostic.severity != "hard":
            continue
        cases = grouped[diagnostic.failure_class]
        if diagnostic.case not in cases:
            cases.append(diagnostic.case)
    return dict(sorted(grouped.items(), key=lambda item: item[0]))
