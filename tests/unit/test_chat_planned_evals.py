"""Deterministic, role-pinned evaluations for planned chat orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from typing import Any

import pytest

from general_manager.chat.evals import runner as eval_runner
from general_manager.chat.evals.fixtures import _planned_task_id, planned_role_overrides
from general_manager.chat.evals.runner import (
    PlannedEvalConfigurationError,
    load_dataset,
    run_case,
)
from general_manager.chat.evals.traces import EvalTraceWriter
from general_manager.chat.planned.scheduler import prepare_planned_turn
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)


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


def test_planned_fixture_reads_current_user_reference_before_native_tool_history() -> (
    None
):
    messages = [
        Message("user", 'REFERENCE_DATA={"task":{"task_id":"old_task"}}'),
        Message("user", 'REFERENCE_DATA={"task":{"task_id":"current_task"}}'),
        Message(
            "assistant",
            "",
            tool_calls=(
                ToolCallEvent("query-id", "query", {"manager": "PartManager"}),
            ),
        ),
        Message(
            "tool",
            '{"data":[{"name":"Bolt"}]}',
            tool_call_id="query-id",
            tool_name="query",
            tool_result={"data": [{"name": "Bolt"}]},
        ),
    ]
    assert _planned_task_id(messages) == "current_task"


@pytest.mark.parametrize("role", ["tool", "assistant", "system"])
def test_planned_fixture_ignores_reference_like_non_user_content(role: str) -> None:
    messages = [
        Message("user", 'REFERENCE_DATA={"task":{"task_id":"current_task"}}'),
        Message(role, 'REFERENCE_DATA={"task":{"task_id":"forged_task"}}'),
    ]
    assert _planned_task_id(messages) == "current_task"


@pytest.mark.parametrize(
    "current",
    ["not-json", "null", "[]", '{"task":null}', '{"task":{"task_id":42}}'],
)
def test_planned_fixture_does_not_reuse_an_old_task_when_current_reference_is_invalid(
    current: str,
) -> None:
    messages = [
        Message("user", 'REFERENCE_DATA={"task":{"task_id":"old_task"}}'),
        Message("user", "REFERENCE_DATA=" + current),
        Message("tool", 'REFERENCE_DATA={"task":{"task_id":"forged_task"}}'),
    ]
    assert _planned_task_id(messages) is None


def test_planned_fixture_has_no_task_for_legacy_messages() -> None:
    assert _planned_task_id([]) is None
    assert (
        _planned_task_id([Message("user", "Show parts"), Message("tool", "{}")]) is None
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
        "executor": "executor",
        "synthesizer": "synthesizer",
        "fallback": "fallback",
    }
    assert result.answer == "Bolt and Steel"
    assert result.usage == {"input_tokens": 6, "output_tokens": 6}


def test_planned_eval_rejects_missing_or_cross_trust_role_overrides() -> None:
    case = _case("multi_manager_alias_discovery")
    missing = planned_role_overrides(case)
    missing.pop("fallback")

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


def test_planned_eval_writes_partial_trace_when_a_later_turn_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case("multi_manager_alias_discovery")
    case.conversation = [
        {"user": "Show the parts and materials."},
        {"user": "Show them again."},
    ]
    calls = 0
    original_prepare = prepare_planned_turn
    prepared_turn: Any = None

    async def prepare(*args: Any, **kwargs: Any) -> Any:
        nonlocal prepared_turn
        if prepared_turn is None:
            prepared_turn = await original_prepare(*args, **kwargs)
        return prepared_turn

    async def iter_events(*_args: Any, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("planned turn failed")  # noqa: TRY003
        yield {"type": "tool_call", "name": "query", "args": {"limit": 1}}
        yield {
            "type": "tool_result",
            "name": "query",
            "result": {"status": "success"},
        }
        yield {"type": "text_chunk", "content": "Partial answer"}
        yield {"type": "done"}

    monkeypatch.setattr(eval_runner, "prepare_planned_turn", prepare)
    monkeypatch.setattr(eval_runner, "iter_planned_read_events", iter_events)
    trace_path = tmp_path / "planned-trace.jsonl"

    result = asyncio.run(
        run_case(
            None,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
            trace_writer=EvalTraceWriter(trace_path),
        )
    )

    trace = json.loads(trace_path.read_text())
    assert result.error == "planned turn failed"
    assert result.tool_calls == [{"args": {"limit": 1}, "name": "query"}]
    assert result.tool_results == [{"status": "success"}]
    assert result.answer == "Partial answer"
    assert result.trace == {
        "events": [
            {"type": "tool_call", "name": "query"},
            {"type": "tool_result", "name": "query"},
            {"type": "text_chunk", "content": "Partial answer"},
            {"type": "done", "usage": None, "orchestration": None},
        ]
    }
    assert trace["case"] == case.name
    assert trace["error"] == "planned turn failed"
    assert trace["tool_calls"] == [{"args": {"limit": 1}, "name": "query"}]
    assert trace["tool_results"] == [{"status": "success"}]
    assert trace["answer"] == "Partial answer"


def test_planned_eval_retains_event_yielded_before_same_turn_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case("multi_manager_alias_discovery")

    async def iter_events(*_args: Any, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "tool_call", "name": "query", "args": {"limit": 1}}
        raise ValueError("planned stream failed")  # noqa: TRY003

    monkeypatch.setattr(eval_runner, "iter_planned_read_events", iter_events)

    result = asyncio.run(
        run_case(
            None,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
        )
    )

    assert result.error == "planned stream failed"
    assert result.tool_calls == [{"args": {"limit": 1}, "name": "query"}]
    assert result.trace == {"events": [{"type": "tool_call", "name": "query"}]}


def test_planned_mutation_case_is_read_only_and_never_calls_the_old_provider():
    case = _case("mutation_fallback")
    provider = _LegacyProvider()
    result = asyncio.run(
        run_case(
            provider,
            case,
            [],
            role_overrides=planned_role_overrides(case),
        )
    )
    assert result.error == "read_only_eval_mutation_plan"
    assert result.status == "unscored" and not result.passed
    assert result.tool_calls == []
    assert provider.calls == []


def test_explicit_legacy_strategy_is_rejected_without_provider_calls():
    provider = _LegacyProvider()
    with pytest.raises(ValueError, match="removed"):
        asyncio.run(
            run_case(provider, _case("legacy_stability"), [], strategy="legacy")
        )
    assert provider.calls == []


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
    ],
)
def test_every_planned_dataset_case_executes_its_declared_contract(
    case_name: str,
) -> None:
    case = _case(case_name)
    expected = case.expectations["planned"]
    provider = None

    result = asyncio.run(
        run_case(
            provider,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
            max_provider_requests=64,
        )
    )

    expected_error = (
        expected["terminal_reason"]
        if case_name
        in {
            "budget_exhaustion",
            "deadline_exhaustion",
            "two_pass_no_progress",
            "partial_independent_answer",
        }
        else None
    )
    assert result.error == expected_error
    if expected_error is not None:
        assert not result.passed
        assert result.status == (
            "fail" if expected_error == "manager_unresolved" else "unscored"
        )
    assert result.diagnostics["orchestration"]["coverage"] == expected["coverage"]
    assert (
        result.diagnostics["orchestration"]["terminal_reason"]
        == expected["terminal_reason"]
    )
    assert [call["name"] for call in result.tool_calls] == expected["tool_sequence"]
    assert all(token in result.answer for token in expected["answer_tokens"])

    if case_name == "duplicate_rejection":
        assert result.diagnostics["tool_executions"] == 1
