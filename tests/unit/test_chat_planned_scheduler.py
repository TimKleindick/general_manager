"""Focused scheduler contracts for the planned chat execution boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json
from types import MappingProxyType
from typing import Any, ClassVar

import pytest

from general_manager.chat.planned.config import PlannedChatSettings, ProviderProfile
from general_manager.chat.planned.models import (
    EvidenceRequirement,
    PlannedTask,
    ValidatedPlan,
)
from general_manager.chat.planned.scheduler import (
    PreparedPlannedTurn,
    SchedulerCallbacks,
    _Runner,
    iter_planned_read_events,
    _parse_action,
)
from general_manager.chat.providers.base import DoneEvent, TokenUsage, ToolCallEvent


class _Executor:
    responses: ClassVar[list[object]] = []
    responses_by_task: ClassVar[dict[str, list[object]]] = {}
    calls: ClassVar[list[list[object]]] = []
    roles: ClassVar[list[str]] = []

    @classmethod
    def from_config(cls, config: object) -> _Executor:
        executor = cls()
        executor.role = (
            config.get("role", "unknown") if isinstance(config, Mapping) else "unknown"
        )
        return executor

    async def complete(self, _messages: list[object], _tools: list[object]):
        type(self).calls.append(_messages)
        type(self).roles.append(self.role)
        content = _messages[-1].content
        reference = json.loads(
            content.removeprefix("REFERENCE_DATA=").removeprefix(
                "RESOLVED_REFERENCE_DATA="
            )
        )
        task_id = reference.get("task", {}).get("task_id", "synthesis")
        responses = type(self).responses_by_task.get(task_id, type(self).responses)
        response = responses.pop(0)
        if isinstance(response, _ProviderFailure):
            raise response
        if isinstance(response, ToolCallEvent):
            yield response
        else:
            from general_manager.chat.providers.base import TextChunkEvent

            yield TextChunkEvent(json.dumps(response))
        yield DoneEvent(TokenUsage(input_tokens=1, output_tokens=1))


def _settings(*, max_concurrent_tasks: int = 3) -> PlannedChatSettings:
    profile = ProviderProfile(
        "test",
        "tests.unit.test_chat_planned_scheduler._Executor",
        MappingProxyType({"model": "test"}),
        "local",
    )
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType({"test": profile}),
        roles=MappingProxyType(
            {
                "simple_executor": "test",
                "complex_executor": "test",
                "fallback_executor": "test",
                "synthesizer": "test",
                "planner": "test",
            }
        ),
        catalog_source=None,
        max_concurrent_tasks=max_concurrent_tasks,
    )


def _role_settings() -> PlannedChatSettings:
    roles = (
        "simple_executor",
        "complex_executor",
        "fallback_executor",
        "synthesizer",
        "planner",
    )
    profiles = {
        role: ProviderProfile(
            role,
            "tests.unit.test_chat_planned_scheduler._Executor",
            MappingProxyType({"role": role}),
            "local",
        )
        for role in roles
    }
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType(profiles),
        roles=MappingProxyType({role: role for role in roles}),
        catalog_source=None,
    )


class _ProviderFailure(Exception):
    pass


def _task(task_id: str, *, depends_on: tuple[str, ...] = ()) -> PlannedTask:
    requirement = EvidenceRequirement("query", "query", "records", None)
    return PlannedTask(
        task_id=task_id,
        objective="find records",
        depends_on=depends_on,
        requirements=(requirement,),
        completion_criteria=("query",),
        routing_features=(),
    )


def _tool_runtime(tool_name: str) -> tuple[_Runner, Any]:
    kind = {"get_manager_schema": "schema", "find_path": "path"}[tool_name]
    requirement = EvidenceRequirement(kind, kind, kind, None)
    task = PlannedTask("task_1", "part", (), (requirement,), (kind,), ())
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="part"
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(execute_tool=lambda *_args: None),
        100.0,
        lambda: 0.0,
    )
    return runner, runner.runtimes[task.task_id]


def _run_one_tool_result(tool_name: str, result: object) -> tuple[bool, Any, _Runner]:
    runner, runtime = _tool_runtime(tool_name)
    args = (
        {"manager": "PartManager"}
        if tool_name == "get_manager_schema"
        else {"from_manager": "PartManager", "to_manager": "PartManager"}
    )
    runner.callbacks = SchedulerCallbacks(execute_tool=lambda *_args: result)
    _, progress = asyncio.run(
        runner.execute_tool(runtime, ToolCallEvent("tool", tool_name, args))
    )
    return progress, runtime, runner


@pytest.mark.parametrize(
    ("tool_name", "result", "expected_progress", "expected_path_depth"),
    [
        ("get_manager_schema", None, False, None),
        ("get_manager_schema", {}, False, None),
        ("get_manager_schema", {"status": "error", "code": "tool_failed"}, False, None),
        ("get_manager_schema", {"status": "success", "fields": []}, True, None),
        ("find_path", None, False, None),
        ("find_path", {"status": "error"}, False, None),
        ("find_path", [], True, 0),
        ("find_path", ["PartManager"], True, 1),
        ("find_path", ["PartManager", "ProjectManager"], True, 2),
    ],
)
def test_tool_evidence_gate_rejects_failures_and_accepts_valid_zero_hop_paths(
    tool_name: str,
    result: object,
    expected_progress: bool,
    expected_path_depth: int | None,
) -> None:
    progress, runtime, runner = _run_one_tool_result(tool_name, result)

    assert progress is expected_progress
    assert bool(runner.evidence.for_task(runtime.task.task_id)) is expected_progress
    assert ("PartManager" in runtime.resolved_anchors) is (
        expected_progress and tool_name == "get_manager_schema"
    )
    assert runtime.path_depth == expected_path_depth


def test_duplicate_query_consumes_a_round_but_executes_once() -> None:
    task = _task("task_1")
    _Executor.responses = [
        ToolCallEvent("one", "query", {"manager": "PartManager", "fields": ["name"]}),
        ToolCallEvent("two", "query", {"fields": ["name"], "manager": "PartManager"}),
        {"action": "complete", "evidence_ids": ["task_1:query:1"]},
        {"answer": "No parts found.", "evidence_ids": ["task_1:query:1"]},
    ]
    _Executor.responses_by_task = {}
    calls: list[tuple[str, dict[str, Any]]] = []

    def execute(name: str, args: dict[str, Any], _context: object) -> dict[str, Any]:
        calls.append((name, args))
        return {"status": "success", "data": []}

    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="show parts"
    )
    events = asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(execute_tool=execute),
            )
        )
    )

    assert len(calls) == 1
    assert prepared.budget.subtree_count("task_1") == 3
    assert [event["type"] for event in events] == [
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "text_chunk",
        "done",
    ]
    assert events[1]["result"] == events[3]["result"]


def test_failed_dependency_blocks_only_its_dependent_task() -> None:
    first, second, independent = (
        _task("task_1"),
        _task("task_2", depends_on=("task_1",)),
        _task("task_3"),
    )
    _Executor.responses = [
        {"answer": "No parts found.", "evidence_ids": ["task_3:query:1"]}
    ]
    _Executor.responses_by_task = {
        "task_1": [{"action": "block", "reason": "manager_unresolved"}],
        "task_3": [
            ToolCallEvent(
                "three", "query", {"manager": "PartManager", "fields": ["name"]}
            ),
            {"action": "complete", "evidence_ids": ["task_3:query:1"]},
        ],
    }
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (first, second, independent)),
        _settings(max_concurrent_tasks=1),
        user_text="x",
    )
    asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(
                    execute_tool=lambda *_args: {"status": "success", "data": []}
                ),
            )
        )
    )

    assert prepared.result is not None
    assert prepared.result.statuses == {
        "task_1": "blocked",
        "task_2": "blocked",
        "task_3": "resolved",
    }


async def _collect(iterator: Any) -> list[dict[str, Any]]:
    return [event async for event in iterator]


class _ExactResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def resolve(self, query: str, anchors: tuple[str, ...] = ()) -> tuple[Any, ...]:
        from general_manager.chat.planned.resolver import ManagerCandidate

        self.calls.append((query, anchors))
        return (
            ManagerCandidate(
                f"PartManager{len(self.calls)}", ("exact manager name",), True
            ),
        )


class _StableExactResolver:
    def resolve(self, _query: str, _anchors: tuple[str, ...] = ()) -> tuple[Any, ...]:
        from general_manager.chat.planned.resolver import ManagerCandidate

        return (ManagerCandidate("PartManager", ("exact manager name",), True),)


def _failure_response(failure_mode: str) -> object:
    return {
        "provider_none": _ProviderFailure(),
        "malformed_action": {"unexpected": "action"},
        "failed_tool": ToolCallEvent(
            "query", "query", {"manager": "PartManager", "fields": ["name"]}
        ),
    }[failure_mode]


def _run_failure_modes(failure_modes: tuple[str, ...]) -> tuple[Any, list[str]]:
    _Executor.responses = [_failure_response(mode) for mode in failure_modes]
    _Executor.responses_by_task = {}
    _Executor.roles = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)),
        _role_settings(),
        user_text="show parts",
        resolver=_StableExactResolver(),
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(
            execute_tool=lambda *_args: {
                "status": "error",
                "code": "tool_failed",
            }
        ),
        100.0,
        lambda: 0.0,
    )
    runtime = runner.runtimes["task_1"]
    runtime.candidates = ("PartManager",)
    asyncio.run(runner.run_task(runtime))
    return runner.result(), _Executor.roles


@pytest.mark.parametrize(
    ("failure_mode", "expected_reason"),
    [
        ("provider_none", "provider_failed"),
        ("malformed_action", "provider_failed"),
        ("failed_tool", "manager_unresolved"),
    ],
)
def test_two_normal_failures_then_two_fallback_failures_block(
    failure_mode: str,
    expected_reason: str,
) -> None:
    result, roles = _run_failure_modes((failure_mode,) * 4)

    assert roles == [
        "simple_executor",
        "simple_executor",
        "fallback_executor",
        "fallback_executor",
    ]
    assert result.reasons["task_1"] == expected_reason


@pytest.mark.parametrize(
    ("fallback_pair", "expected_reason"),
    [
        (("provider_none", "malformed_action"), "provider_failed"),
        (("failed_tool", "failed_tool"), "manager_unresolved"),
        (("failed_tool", "provider_none"), "manager_unresolved"),
        (("provider_none", "failed_tool"), "manager_unresolved"),
    ],
)
def test_fallback_terminal_reason_requires_two_provider_failures(
    fallback_pair: tuple[str, str],
    expected_reason: str,
) -> None:
    result, roles = _run_failure_modes(("failed_tool", "failed_tool", *fallback_pair))

    assert roles == [
        "simple_executor",
        "simple_executor",
        "fallback_executor",
        "fallback_executor",
    ]
    assert result.reasons["task_1"] == expected_reason


def test_real_progress_resets_consecutive_failure_count() -> None:
    requirement = EvidenceRequirement("schema", "schema", "schema", None)
    task = PlannedTask("task_1", "part", (), (requirement,), ("schema",), ())
    _Executor.responses = [
        {"unexpected": "action"},
        ToolCallEvent("schema", "get_manager_schema", {"manager": "PartManager"}),
        {"unexpected": "action"},
        {"unexpected": "action"},
        {"action": "complete", "evidence_ids": ["task_1:schema:1"]},
        {"answer": "Part schema found.", "evidence_ids": ["task_1:schema:1"]},
    ]
    _Executor.responses_by_task = {}
    _Executor.roles = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)),
        _role_settings(),
        user_text="show parts",
        resolver=_StableExactResolver(),
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(
            execute_tool=lambda *_args: {"status": "success", "fields": []}
        ),
        100.0,
        lambda: 0.0,
    )
    runtime = runner.runtimes["task_1"]
    runtime.candidates = ("PartManager",)
    asyncio.run(runner.run_task(runtime))

    assert _Executor.roles[:4] == ["simple_executor"] * 4
    assert runner.result().statuses["task_1"] == "resolved"


def test_candidate_churn_cannot_bypass_ten_local_pass_cap() -> None:
    _Executor.responses = [{"unexpected": "action"}] * 12
    _Executor.responses_by_task = {}
    _Executor.roles = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)),
        _role_settings(),
        user_text="show parts",
        resolver=_ExactResolver(),
    )

    asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(execute_tool=lambda *_args: {}),
            )
        )
    )

    assert prepared.result is not None
    assert len(_Executor.roles) <= 9
    assert prepared.result.reasons["task_1"] == "manager_unresolved"


def test_resolver_passes_are_free_and_cap_at_ten_before_provider_budget() -> None:
    task = _task("task_1")
    _Executor.responses = [{"action": "complete", "evidence_ids": ["missing"]}] * 12
    _Executor.responses_by_task = {}
    resolver = _ExactResolver()
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)),
        _settings(),
        user_text="show parts",
        resolver=resolver,
    )

    asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(execute_tool=lambda *_args: {}),
            )
        )
    )

    assert len(resolver.calls) == 10
    assert prepared.budget.subtree_count("task_1") == 9


def test_executor_reference_includes_the_declared_requirement_operation() -> None:
    task = PlannedTask(
        task_id="task_1",
        objective="find ratio",
        depends_on=(),
        requirements=(EvidenceRequirement("ratio", "calculation", "ratio", "ratio"),),
        completion_criteria=("ratio",),
        routing_features=("requires_calculation",),
    )
    _Executor.responses = [{"action": "block", "reason": "manager_unresolved"}]
    _Executor.responses_by_task = {}
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="ratio"
    )
    asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(execute_tool=lambda *_args: {}),
            )
        )
    )

    reference = json.loads(
        _Executor.calls[-1][-1].content.removeprefix("REFERENCE_DATA=")
    )
    assert reference["task"]["requirements"] == [
        {"requirement_id": "ratio", "kind": "calculation", "operation": "ratio"}
    ]


def test_none_schema_result_never_becomes_evidence() -> None:
    requirement = EvidenceRequirement("schema", "schema", "schema", None)
    task = PlannedTask("task_1", "part", (), (requirement,), ("schema",), ())
    _Executor.responses = [
        ToolCallEvent("schema", "get_manager_schema", {"manager": "PartManager"}),
        {"action": "complete", "evidence_ids": ["task_1:schema:1"]},
        {"action": "block", "reason": "manager_unresolved"},
    ]
    _Executor.responses_by_task = {}
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="part"
    )
    events = asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(execute_tool=lambda *_args: None),
            )
        )
    )
    assert [event["type"] for event in events] == ["tool_call", "tool_result", "error"]
    assert prepared.result is not None
    assert prepared.result.evidence.get("task_1:schema:1") is None


def test_empty_path_is_evidence_but_none_path_is_not() -> None:
    requirement = EvidenceRequirement("path", "path", "path", None)
    task = PlannedTask("task_1", "path", (), (requirement,), ("path",), ())
    _Executor.responses = [
        ToolCallEvent(
            "path",
            "find_path",
            {"from_manager": "PartManager", "to_manager": "PartManager"},
        ),
        {"action": "complete", "evidence_ids": ["task_1:path:1"]},
        {"answer": "same manager", "evidence_ids": ["task_1:path:1"]},
    ]
    _Executor.responses_by_task = {}
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="path"
    )
    events = asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(execute_tool=lambda *_args: []),
            )
        )
    )
    assert events[-1]["type"] == "done"


def test_executor_rejects_mutate_before_any_public_or_private_callback() -> None:
    task = _task("task_1")
    _Executor.responses = [
        ToolCallEvent("unsafe", "mutate", {"mutation": "deletePart", "input": {}}),
        {"action": "block", "reason": "manager_unresolved"},
    ]
    _Executor.responses_by_task = {}
    calls: list[str] = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="show parts"
    )

    events = asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(
                    execute_tool=lambda *_args: calls.append("tool"),
                    emit_tool_called=lambda **_kwargs: calls.append("signal"),
                    emit_audit_event=lambda *_args: calls.append("audit"),
                ),
            )
        )
    )

    assert calls == []
    assert [event["type"] for event in events] == ["error"]


def test_executor_action_parser_rejects_duplicate_keys_trailing_data_and_extra_fields() -> (
    None
):
    assert _parse_action('{"action":"complete","evidence_ids":["e"],"x":1}') is None
    assert (
        _parse_action('{"action":"block","reason":"manager_unresolved"} trailing')
        is None
    )
    assert _parse_action('{"action":"block","reason":"x","reason":"y"}') is None


def test_calculate_action_is_schema_bound_to_query_evidence() -> None:
    task = PlannedTask(
        task_id="task_1",
        objective="calculate total",
        depends_on=(),
        requirements=(
            EvidenceRequirement("query", "query", "records", None),
            EvidenceRequirement("total", "calculation", "sum", "sum"),
        ),
        completion_criteria=("query", "total"),
        routing_features=("requires_calculation",),
    )
    _Executor.responses = [
        ToolCallEvent(
            "query", "query", {"manager": "PartManager", "fields": ["value"]}
        ),
        {
            "action": "calculate",
            "requirement_id": "total",
            "operation": "sum",
            "operands": [
                {"evidence_id": "task_1:query:1", "path": ["data", 0, "value"]}
            ],
        },
        {
            "action": "complete",
            "evidence_ids": ["task_1:query:1", "task_1:calculation:2"],
        },
        {"answer": "Total is 3.", "evidence_ids": ["task_1:calculation:2"]},
    ]
    _Executor.responses_by_task = {}
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="total"
    )

    events = asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(
                    execute_tool=lambda *_args: {
                        "status": "success",
                        "data": [{"value": 3}],
                    }
                ),
            )
        )
    )

    assert events[-1]["type"] == "done"
    assert prepared.result is not None
    assert prepared.result.evidence.get("task_1:calculation:2") is not None
