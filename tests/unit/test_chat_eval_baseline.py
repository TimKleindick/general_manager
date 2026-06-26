from __future__ import annotations

from general_manager.chat.evals.baseline import (
    ReadinessSummary,
    compare_to_baseline,
)


def _summary(
    *,
    passed: int,
    total: int,
    diagnostics: dict[str, dict[str, int]],
) -> ReadinessSummary:
    return ReadinessSummary(
        run_hash="abc123",
        gate="demo",
        provider="OllamaProvider",
        model="glm-4.7-flash:q4_K_M",
        fixture="toy",
        datasets=["demo_readiness"],
        tier=1,
        total=total,
        passed=passed,
        product_contract_total=total,
        product_contract_passed=passed,
        diagnostics=diagnostics,
    )


def test_compare_to_baseline_detects_lower_pass_rate() -> None:
    baseline = _summary(passed=5, total=5, diagnostics={})
    current = _summary(
        passed=4,
        total=5,
        diagnostics={"prompt": {"missing_required_tool": 1}},
    )

    comparison = compare_to_baseline(current, baseline)

    assert comparison.regressed is True
    assert comparison.pass_rate_delta == -0.2
    assert comparison.messages == ["Overall pass rate regressed from 100% to 80%."]


def test_compare_to_baseline_allows_equal_or_better_run() -> None:
    baseline = _summary(
        passed=4,
        total=5,
        diagnostics={"prompt": {"missing_required_tool": 1}},
    )
    current = _summary(passed=5, total=5, diagnostics={})

    comparison = compare_to_baseline(current, baseline)

    assert comparison.regressed is False
    assert comparison.pass_rate_delta == 0.2
    assert comparison.messages == []


def test_compare_to_baseline_detects_new_hard_failure_category() -> None:
    baseline = _summary(passed=5, total=5, diagnostics={})
    current = _summary(
        passed=5,
        total=5,
        diagnostics={"runtime": {"forbidden_tool": 1}},
    )

    comparison = compare_to_baseline(current, baseline)

    assert comparison.regressed is True
    assert comparison.messages == [
        "New diagnostic category runtime/forbidden_tool appeared 1 time."
    ]


def test_compare_to_baseline_allows_new_soft_strategy_deviation() -> None:
    baseline = _summary(passed=5, total=5, diagnostics={})
    current = _summary(
        passed=5,
        total=5,
        diagnostics={"prompt": {"strategy_deviation": 1}},
    )

    comparison = compare_to_baseline(current, baseline)

    assert comparison.regressed is False
    assert comparison.pass_rate_delta == 0.0
    assert comparison.messages == []


def test_compare_to_baseline_detects_new_soft_category_when_not_perfect() -> None:
    baseline = _summary(passed=4, total=5, diagnostics={})
    current = _summary(
        passed=4,
        total=5,
        diagnostics={"prompt": {"strategy_deviation": 1}},
    )

    comparison = compare_to_baseline(current, baseline)

    assert comparison.regressed is True
    assert comparison.pass_rate_delta == 0.0
    assert comparison.messages == [
        "New diagnostic category prompt/strategy_deviation appeared 1 time."
    ]
