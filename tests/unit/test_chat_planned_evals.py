"""Deterministic, role-pinned evaluations for planned chat orchestration."""

from __future__ import annotations

import asyncio

import pytest

from general_manager.chat.evals.fixtures import planned_role_overrides
from general_manager.chat.evals.runner import (
    PlannedEvalConfigurationError,
    load_dataset,
    run_case,
)
from general_manager.chat.providers.base import DoneEvent, TextChunkEvent, TokenUsage


class _LegacyProvider:
    def __init__(self) -> None:
        self.calls = []

    async def complete(self, _messages, _tools):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": _messages, "tools": _tools})
        yield TextChunkEvent("Apollo")
        yield DoneEvent(TokenUsage(input_tokens=2, output_tokens=3))


def _case(name: str):
    return next(
        case for case in load_dataset("planned_orchestration") if case.name == name
    )


def test_planned_eval_pins_roles_and_reports_complete_coverage() -> None:
    case = _case("multi_manager_alias_discovery")

    result = asyncio.run(
        run_case(
            None,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
        )
    )

    assert result.diagnostics["orchestration"]["coverage"] == {
        "resolved": 2,
        "total": 2,
    }
    assert result.diagnostics["roles"] == {
        "planner": "planner",
        "simple_executor": "simple_executor",
        "complex_executor": "complex_executor",
        "synthesizer": "synthesizer",
        "fallback_executor": "fallback_executor",
    }
    assert result.answer == "Bolt and Steel"
    assert result.usage == {"input_tokens": 6, "output_tokens": 6}


def test_planned_eval_rejects_missing_or_cross_trust_role_overrides() -> None:
    case = _case("multi_manager_alias_discovery")
    missing = planned_role_overrides(case)
    missing.pop("fallback_executor")

    with pytest.raises(PlannedEvalConfigurationError, match="missing role overrides"):
        asyncio.run(
            run_case(None, case, [], strategy="planned", role_overrides=missing)
        )

    mixed = planned_role_overrides(case)
    mixed["synthesizer"] = mixed["synthesizer"].with_trust_group("outside")
    with pytest.raises(PlannedEvalConfigurationError, match="one trust_group"):
        asyncio.run(run_case(None, case, [], strategy="planned", role_overrides=mixed))


def test_planned_eval_fingerprint_trace_and_usage_are_reproducible_and_sanitized() -> (
    None
):
    case = _case("partial_independent_answer")
    first = asyncio.run(
        run_case(
            None,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
        )
    )
    second = asyncio.run(
        run_case(
            None,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
        )
    )

    assert first.fingerprint == second.fingerprint
    assert first.diagnostics["orchestration"] == {
        "coverage": {"resolved": 1, "total": 2},
        "unresolved": [{"task_id": "task_2", "reason": "manager_unresolved"}],
        "terminal_reason": "manager_unresolved",
    }
    assert first.usage == {"input_tokens": 5, "output_tokens": 5}
    serialized_trace = str(first.trace)
    assert "local" not in serialized_trace
    assert "profile" not in serialized_trace.lower()
    assert "PartManager" not in serialized_trace
    assert "task_1" not in serialized_trace
    assert "task_2" not in serialized_trace
    assert "fields" not in serialized_trace


def test_planned_mutation_case_falls_back_to_unchanged_legacy_turn() -> None:
    case = _case("mutation_fallback")
    provider = _LegacyProvider()
    tool_defs = [{"name": "query", "description": "Query records"}]

    result = asyncio.run(
        run_case(
            provider,
            case,
            tool_defs,
            strategy="planned",
            role_overrides=planned_role_overrides(case),
        )
    )

    assert result.answer == "Apollo"
    assert result.diagnostics["orchestration"] == {
        "strategy": "legacy",
        "reason": "mutation",
    }
    assert provider.calls[0]["messages"][-1].content == "Update the Apollo record."
    assert provider.calls[0]["tools"][0].name == "query"


def test_legacy_strategy_retains_existing_result_shape() -> None:
    case = _case("legacy_stability")

    result = asyncio.run(run_case(_LegacyProvider(), case, [], strategy="legacy"))

    assert result.answer == "Apollo"
    assert result.tool_calls == []
    assert result.diagnostics == {}


def test_dataset_declares_every_required_deterministic_orchestration_case() -> None:
    cases = load_dataset("planned_orchestration")
    names = {case.name for case in cases}

    assert names == {
        "multi_manager_alias_discovery",
        "one_edge_dependency",
        "dynamic_child_creation",
        "deterministic_calculation",
        "partial_independent_answer",
        "budget_exhaustion",
        "deadline_exhaustion",
        "duplicate_rejection",
        "two_pass_no_progress",
        "mutation_fallback",
        "legacy_stability",
    }
    for case in cases:
        planned = case.expectations["planned"]
        assert "coverage" in planned
        assert "terminal_reason" in planned
        assert "tool_sequence" in planned
        assert "answer_tokens" in planned


@pytest.mark.parametrize(
    "case_name",
    [
        "multi_manager_alias_discovery",
        "one_edge_dependency",
        "dynamic_child_creation",
        "deterministic_calculation",
        "partial_independent_answer",
        "budget_exhaustion",
        "deadline_exhaustion",
        "duplicate_rejection",
        "two_pass_no_progress",
        "mutation_fallback",
    ],
)
def test_every_planned_dataset_case_executes_its_declared_contract(
    case_name: str,
) -> None:
    case = _case(case_name)
    expected = case.expectations["planned"]
    provider = _LegacyProvider() if case_name == "mutation_fallback" else None

    result = asyncio.run(
        run_case(
            provider,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
        )
    )

    assert result.error is None
    if case_name == "mutation_fallback":
        assert result.diagnostics["orchestration"] == {
            "strategy": "legacy",
            "reason": "mutation",
        }
        assert result.answer == "Apollo"
        return
    assert result.diagnostics["orchestration"]["coverage"] == expected["coverage"]
    assert (
        result.diagnostics["orchestration"]["terminal_reason"]
        == expected["terminal_reason"]
    )
    assert [call["name"] for call in result.tool_calls] == expected["tool_sequence"]
    assert all(token in result.answer for token in expected["answer_tokens"])

    if case_name == "duplicate_rejection":
        assert result.diagnostics["tool_executions"] == 1
