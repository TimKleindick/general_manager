from __future__ import annotations

from general_manager.chat.evals.diagnostics import (
    FailureDiagnostic,
    classify_result,
    summarize_diagnostics,
)
from general_manager.chat.evals.judges.answer_quality import AnswerQualityScore
from general_manager.chat.evals.judges.contract import ProductContractScore
from general_manager.chat.evals.judges.result_accuracy import ResultAccuracyScore
from general_manager.chat.evals.runner import EvalCase, EvalResult


def _case(name: str = "demo_case") -> EvalCase:
    return EvalCase(
        name=name,
        description="Demo case",
        conversation=[{"user": "Which materials are dense?"}],
        expectations={},
        tier=1,
        tags=["demo"],
    )


def test_missing_required_query_is_prompt_or_harness_failure() -> None:
    result = EvalResult(
        case=_case(),
        contract_score=ProductContractScore(
            passed=False,
            category="data_grounding",
            violations=["Required tool call missing: query"],
        ),
    )

    diagnostic = classify_result(result)

    assert diagnostic == FailureDiagnostic(
        case="demo_case",
        owner="prompt",
        category="missing_required_tool",
        severity="hard",
        failure_class="tool_or_schema_retry",
        message="Required tool call missing: query",
        next_action=(
            "Tighten tool-decision prompt or add missing-tool recovery before "
            "changing the dataset contract."
        ),
    )


def test_forbidden_mutation_is_runtime_safety_failure() -> None:
    result = EvalResult(
        case=_case("read_only_case"),
        contract_score=ProductContractScore(
            passed=False,
            category="read_only_safety",
            violations=["Forbidden tool called: mutate"],
        ),
    )

    diagnostic = classify_result(result)

    assert diagnostic is not None
    assert diagnostic.owner == "runtime"
    assert diagnostic.category == "forbidden_tool"
    assert diagnostic.severity == "hard"
    assert diagnostic.failure_class == "eval_or_harness"
    assert "forbidden tool safety checks" in diagnostic.next_action.lower()


def test_forbidden_query_is_runtime_safety_failure() -> None:
    result = EvalResult(
        case=_case("discovery_case"),
        contract_score=ProductContractScore(
            passed=False,
            category="manager_discovery",
            violations=["Forbidden tool called: query"],
        ),
    )

    diagnostic = classify_result(result)

    assert diagnostic == FailureDiagnostic(
        case="discovery_case",
        owner="runtime",
        category="forbidden_tool",
        severity="hard",
        failure_class="eval_or_harness",
        message="Forbidden tool called: query",
        next_action=(
            "Add or verify forbidden tool safety checks in the runtime harness "
            "and keep the prompt's tool safety section explicit."
        ),
    )


def test_strategy_deviation_is_soft_prompt_signal() -> None:
    result = EvalResult(
        case=_case("strategy_case"),
        contract_score=ProductContractScore(
            passed=True,
            category="relation_traversal",
            strategy_deviations=["Recommended tool call missing: search_managers"],
        ),
    )

    diagnostic = classify_result(result)

    assert diagnostic is not None
    assert diagnostic.owner == "prompt"
    assert diagnostic.category == "strategy_deviation"
    assert diagnostic.severity == "soft"
    assert diagnostic.failure_class == "tool_or_schema_retry"


def test_answer_contradiction_is_answer_contract_failure() -> None:
    result = EvalResult(
        case=_case("answer_sense_case"),
        contract_score=ProductContractScore(
            passed=False,
            category="read_only_safety",
            violations=["Answer contradicts required result value: Apollo"],
        ),
    )

    diagnostic = classify_result(result)

    assert diagnostic is not None
    assert diagnostic.owner == "prompt"
    assert diagnostic.category == "answer_contract"
    assert diagnostic.severity == "hard"
    assert diagnostic.failure_class == "answer_grounding"


def test_raw_query_syntax_is_answer_contract_failure() -> None:
    result = EvalResult(
        case=_case("raw_query_syntax_case"),
        contract_score=ProductContractScore(
            passed=False,
            category="relation_traversal",
            violations=["Answer includes raw query syntax after a successful query"],
        ),
    )

    diagnostic = classify_result(result)

    assert diagnostic is not None
    assert diagnostic.owner == "prompt"
    assert diagnostic.category == "answer_contract"
    assert diagnostic.severity == "hard"
    assert diagnostic.failure_class == "answer_grounding"


def test_missing_result_value_is_tool_or_schema_retry_failure() -> None:
    result = EvalResult(
        case=_case("result_case"),
        contract_score=ProductContractScore(
            passed=False,
            category="relation_traversal",
            violations=["Missing result value: Apollo"],
        ),
    )

    diagnostic = classify_result(result)

    assert diagnostic is not None
    assert diagnostic.owner == "tool_schema"
    assert diagnostic.category == "result_contract"
    assert diagnostic.severity == "hard"
    assert diagnostic.failure_class == "tool_or_schema_retry"


def test_answer_omission_takes_precedence_when_results_pass() -> None:
    result = EvalResult(
        case=_case("answer_omission_case"),
        contract_score=ProductContractScore(
            passed=False,
            category="relation_traversal",
            violations=[
                "Required tool call missing: get_manager_schema",
                "Missing answer text: Gear",
            ],
        ),
        result_score=ResultAccuracyScore(passed=True),
        answer_score=AnswerQualityScore(
            passed=False,
            score=0.0,
            missing=["Gear"],
        ),
    )

    diagnostic = classify_result(result)

    assert diagnostic is not None
    assert diagnostic.owner == "prompt"
    assert diagnostic.category == "answer_quality"
    assert diagnostic.severity == "hard"
    assert diagnostic.failure_class == "answer_grounding"


def test_error_result_is_provider_or_harness_failure() -> None:
    result = EvalResult(case=_case("provider_case"), error="Connection refused")

    diagnostic = classify_result(result)

    assert diagnostic is not None
    assert diagnostic.owner == "provider"
    assert diagnostic.category == "provider_error"
    assert diagnostic.severity == "hard"
    assert diagnostic.failure_class == "eval_or_harness"


def test_summarize_diagnostics_groups_by_owner_and_category() -> None:
    diagnostics = [
        FailureDiagnostic(
            case="a",
            owner="prompt",
            category="missing_required_tool",
            severity="hard",
            failure_class="tool_or_schema_retry",
            message="Required tool call missing: query",
            next_action="Fix prompt.",
        ),
        FailureDiagnostic(
            case="b",
            owner="prompt",
            category="missing_required_tool",
            severity="hard",
            failure_class="tool_or_schema_retry",
            message="Required tool call missing: query",
            next_action="Fix prompt.",
        ),
        FailureDiagnostic(
            case="c",
            owner="runtime",
            category="forbidden_tool",
            severity="hard",
            failure_class="eval_or_harness",
            message="Forbidden tool called: mutate",
            next_action="Fix runtime safety.",
        ),
    ]

    summary = summarize_diagnostics(diagnostics)

    assert summary["prompt"]["missing_required_tool"] == 2
    assert summary["runtime"]["forbidden_tool"] == 1
