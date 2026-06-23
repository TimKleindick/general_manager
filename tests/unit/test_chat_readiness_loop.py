from __future__ import annotations

from pathlib import Path

from general_manager.chat.evals.baseline import ReadinessSummary
from general_manager.chat.evals.judges.contract import ProductContractScore
from general_manager.chat.evals.readiness import (
    ReadinessConfig,
    build_summary,
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
