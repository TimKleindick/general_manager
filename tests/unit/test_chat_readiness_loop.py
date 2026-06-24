from __future__ import annotations

from pathlib import Path

from general_manager.chat.evals.baseline import ReadinessSummary
from general_manager.chat.evals.judges.contract import ProductContractScore
from general_manager.chat.evals.judges.tool_sequence import ToolSequenceScore
from general_manager.chat.evals.readiness import (
    ReadinessConfig,
    build_summary,
    readiness_exit_code,
    write_readiness_artifacts,
)
from general_manager.chat.evals.runner import EvalCase, EvalResult


def test_build_summary_counts_contract_and_overall_passes() -> None:
    case = EvalCase(
        name="demo",
        description="Demo",
        conversation=[{"user": "List parts"}],
        expectations={},
        tier=1,
        tags=["demo"],
    )
    results = [
        EvalResult(
            case=case,
            contract_score=ProductContractScore(
                passed=False,
                category="data_grounding",
                violations=["Required tool call missing: query"],
            ),
        )
    ]

    summary = build_summary(
        gate="demo",
        run_metadata={
            "run_hash": "hash1",
            "provider": "OllamaProvider",
            "model": "glm-4.7-flash:q4_K_M",
            "fixture": "toy",
            "datasets": ["demo_readiness"],
            "tier": 1,
        },
        results=results,
    )

    assert summary == ReadinessSummary(
        run_hash="hash1",
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        total=1,
        passed=0,
        product_contract_total=1,
        product_contract_passed=0,
        native_passed=0,
        recovered_passed=0,
        recovery_total=0,
        recovered_cases=[],
        failure_classes={"tool_or_schema_retry": 1},
        failure_class_cases={"tool_or_schema_retry": ["demo"]},
        diagnostics={"prompt": {"missing_required_tool": 1}},
    )


def test_build_summary_exposes_recovery_assisted_passes() -> None:
    native_case = EvalCase(
        name="native",
        description="Native pass",
        conversation=[{"user": "List parts"}],
        expectations={},
        tier=1,
        tags=["demo"],
    )
    recovered_case = EvalCase(
        name="recovered",
        description="Recovered pass",
        conversation=[{"user": "List parts"}],
        expectations={},
        tier=1,
        tags=["demo"],
    )
    results = [
        EvalResult(
            case=native_case,
            contract_score=ProductContractScore(
                passed=True,
                category="data_grounding",
            ),
        ),
        EvalResult(
            case=recovered_case,
            contract_score=ProductContractScore(
                passed=True,
                category="data_grounding",
            ),
            recovery_events=["inject_missing_relation_query"],
        ),
    ]

    summary = build_summary(
        gate="demo",
        run_metadata={
            "run_hash": "hash1",
            "provider": "OllamaProvider",
            "model": "glm-4.7-flash:q4_K_M",
            "fixture": "toy",
            "datasets": ["demo_readiness"],
            "tier": 1,
        },
        results=results,
    )

    assert summary.native_passed == 1
    assert summary.recovered_passed == 1
    assert summary.recovery_total == 1
    assert summary.recovered_cases == ["recovered"]


def test_build_summary_does_not_count_passing_diagnostics_as_failure_classes() -> None:
    case = EvalCase(
        name="passing_with_legacy_mismatch",
        description="Product contract passes despite legacy sequence mismatch",
        conversation=[{"user": "List parts"}],
        expectations={},
        tier=1,
        tags=["demo"],
    )
    results = [
        EvalResult(
            case=case,
            contract_score=ProductContractScore(
                passed=True,
                category="data_grounding",
            ),
            tool_score=ToolSequenceScore(
                passed=False,
                expected=[{"name": "search_managers"}],
                actual=[],
                mismatches=["Expected tool 'search_managers' not found in sequence"],
            ),
        )
    ]

    summary = build_summary(
        gate="demo",
        run_metadata={
            "run_hash": "hash1",
            "provider": "OllamaProvider",
            "model": "glm-4.7-flash:q4_K_M",
            "fixture": "toy",
            "datasets": ["demo_readiness"],
            "tier": 1,
        },
        results=results,
    )

    assert summary.passed == 1
    assert summary.diagnostics == {"prompt": {"tool_sequence_mismatch": 1}}
    assert summary.failure_classes == {}
    assert summary.failure_class_cases == {}


def test_build_summary_exposes_forbidden_recovery_events() -> None:
    case = EvalCase(
        name="recovered",
        description="Recovered pass",
        conversation=[{"user": "List parts"}],
        expectations={},
        tier=1,
        tags=["demo"],
    )
    results = [
        EvalResult(
            case=case,
            contract_score=ProductContractScore(
                passed=True,
                category="data_grounding",
            ),
            recovery_events=[
                "missing_tool_call",
                "repair_contradictory_answer",
                "synthesize_answer_from_tool_results",
            ],
        ),
    ]

    summary = build_summary(
        gate="demo",
        run_metadata={
            "run_hash": "hash1",
            "provider": "OllamaProvider",
            "model": "glm-4.7-flash:q4_K_M",
            "fixture": "toy",
            "datasets": ["demo_readiness"],
            "tier": 1,
        },
        results=results,
    )

    assert summary.forbidden_recovery_events == [
        "repair_contradictory_answer",
        "synthesize_answer_from_tool_results",
    ]
    assert summary.forbidden_recovery_total == 2
    assert summary.forbidden_recovered_cases == ["recovered"]
    assert summary.failure_classes == {"eval_or_harness": 1}
    assert summary.failure_class_cases == {"eval_or_harness": ["recovered"]}


def test_build_summary_classifies_answer_grounding_failures() -> None:
    case = EvalCase(
        name="answer_grounding",
        description="Answer omitted returned rows",
        conversation=[{"user": "List projects"}],
        expectations={},
        tier=1,
        tags=["demo"],
    )
    results = [
        EvalResult(
            case=case,
            contract_score=ProductContractScore(
                passed=False,
                category="relation_traversal",
                violations=["Answer omits required result value: Apollo"],
            ),
        ),
    ]

    summary = build_summary(
        gate="demo",
        run_metadata={
            "run_hash": "hash1",
            "provider": "OllamaProvider",
            "model": "glm-4.7-flash:q4_K_M",
            "fixture": "toy",
            "datasets": ["demo_readiness"],
            "tier": 1,
        },
        results=results,
    )

    assert summary.failure_classes == {"answer_grounding": 1}
    assert summary.failure_class_cases == {"answer_grounding": ["answer_grounding"]}


def test_build_summary_promotes_repeated_answer_prompt_attempts_to_model_reliability() -> (
    None
):
    case = EvalCase(
        name="repeat_answer_grounding",
        description="Repeated answer prompt attempts still omit returned rows",
        conversation=[{"user": "List projects"}],
        expectations={},
        tier=1,
        tags=["demo"],
    )
    results = [
        EvalResult(
            case=case,
            contract_score=ProductContractScore(
                passed=False,
                category="relation_traversal",
                violations=["Answer omits required result value: Apollo"],
            ),
            recovery_events=["tool_bridge_answer", "answer_without_query"],
        ),
    ]

    summary = build_summary(
        gate="demo",
        run_metadata={
            "run_hash": "hash1",
            "provider": "OllamaProvider",
            "model": "glm-4.7-flash:q4_K_M",
            "fixture": "toy",
            "datasets": ["demo_readiness"],
            "tier": 1,
        },
        results=results,
    )

    assert summary.failure_classes == {"model_demo_reliability": 1}
    assert summary.failure_class_cases == {
        "model_demo_reliability": ["repeat_answer_grounding"]
    }


def test_build_summary_promotes_repeated_unavailable_manager_prompts_to_model_reliability() -> (
    None
):
    case = EvalCase(
        name="repeat_unavailable_manager",
        description="Repeated unavailable-manager prompts still echo the bad name",
        conversation=[{"user": "Show VehicleManager data"}],
        expectations={},
        tier=1,
        tags=["demo"],
    )
    results = [
        EvalResult(
            case=case,
            contract_score=ProductContractScore(
                passed=False,
                category="manager_discovery",
                violations=["Unexpected answer text: VehicleManager"],
            ),
            recovery_events=[
                "unavailable_manager_echo",
                "unavailable_manager_echo_retry",
                "unavailable_manager_echo_final_retry",
                "unavailable_manager_echo_minimal_retry",
            ],
        ),
    ]

    summary = build_summary(
        gate="demo",
        run_metadata={
            "run_hash": "hash1",
            "provider": "OllamaProvider",
            "model": "glm-4.7-flash:q4_K_M",
            "fixture": "toy",
            "datasets": ["demo_readiness"],
            "tier": 1,
        },
        results=results,
    )

    assert summary.failure_classes == {"model_demo_reliability": 1}
    assert summary.failure_class_cases == {
        "model_demo_reliability": ["repeat_unavailable_manager"]
    }


def test_readiness_exit_code_fails_on_forbidden_recovery() -> None:
    summary = ReadinessSummary(
        run_hash="hash1",
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        total=1,
        passed=1,
        product_contract_total=1,
        product_contract_passed=1,
        native_passed=0,
        recovered_passed=1,
        recovery_total=1,
        recovered_cases=["recovered"],
        forbidden_recovery_events=["repair_contradictory_answer"],
        forbidden_recovery_total=1,
        forbidden_recovered_cases=["recovered"],
        diagnostics={},
    )

    assert (
        readiness_exit_code(
            summary=summary,
            comparison=None,
            fail_on_regression=False,
        )
        == 1
    )


def test_write_readiness_artifacts_writes_summary_and_report(tmp_path: Path) -> None:
    summary = ReadinessSummary(
        run_hash="hash1",
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        total=1,
        passed=1,
        product_contract_total=1,
        product_contract_passed=1,
        native_passed=1,
        recovered_passed=0,
        recovery_total=0,
        recovered_cases=[],
        diagnostics={},
    )
    config = ReadinessConfig(
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        tags=["demo"],
        output_dir=tmp_path,
        baseline_json=None,
        fail_on_regression=False,
        live=False,
    )

    write_readiness_artifacts(
        config=config,
        summary=summary,
        report="Dimension report",
        comparison=None,
    )

    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == (
        "Dimension report\n"
    )


def test_write_readiness_artifacts_includes_recovery_section(
    tmp_path: Path,
) -> None:
    summary = ReadinessSummary(
        run_hash="hash1",
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        total=2,
        passed=2,
        product_contract_total=2,
        product_contract_passed=2,
        native_passed=1,
        recovered_passed=1,
        recovery_total=1,
        recovered_cases=["recovered"],
        diagnostics={},
    )
    config = ReadinessConfig(
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        tags=["demo"],
        output_dir=tmp_path,
        baseline_json=None,
        fail_on_regression=False,
        live=False,
    )

    write_readiness_artifacts(
        config=config,
        summary=summary,
        report="Dimension report",
        comparison=None,
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Harness recovery:" in report
    assert "- Native passes: 1" in report
    assert "- Recovered passes: 1 / 1" in report
    assert "- Recovered cases: recovered" in report


def test_write_readiness_artifacts_includes_forbidden_recovery_section(
    tmp_path: Path,
) -> None:
    summary = ReadinessSummary(
        run_hash="hash1",
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        total=1,
        passed=1,
        product_contract_total=1,
        product_contract_passed=1,
        native_passed=0,
        recovered_passed=1,
        recovery_total=1,
        recovered_cases=["recovered"],
        forbidden_recovery_events=["repair_contradictory_answer"],
        forbidden_recovery_total=1,
        forbidden_recovered_cases=["recovered"],
        diagnostics={},
    )
    config = ReadinessConfig(
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        tags=["demo"],
        output_dir=tmp_path,
        baseline_json=None,
        fail_on_regression=False,
        live=False,
    )

    write_readiness_artifacts(
        config=config,
        summary=summary,
        report="Dimension report",
        comparison=None,
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Forbidden recovery:" in report
    assert "- Forbidden events: repair_contradictory_answer" in report
    assert "- Forbidden cases: recovered" in report


def test_write_readiness_artifacts_includes_failure_class_section(
    tmp_path: Path,
) -> None:
    summary = ReadinessSummary(
        run_hash="hash1",
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        total=2,
        passed=1,
        product_contract_total=2,
        product_contract_passed=1,
        failure_classes={"answer_grounding": 1},
        failure_class_cases={"answer_grounding": ["bad_answer"]},
        diagnostics={},
    )
    config = ReadinessConfig(
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        tags=["demo"],
        output_dir=tmp_path,
        baseline_json=None,
        fail_on_regression=False,
        live=False,
    )

    write_readiness_artifacts(
        config=config,
        summary=summary,
        report="Dimension report",
        comparison=None,
    )

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Failure classes:" in report
    assert "- answer_grounding: 1 (bad_answer)" in report
