"""Focused scheduler contracts for the planned chat execution boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import json
import threading
import time
from types import MappingProxyType
from typing import Any, ClassVar, cast

import pytest

from general_manager.chat.planned.config import PlannedChatSettings, ProviderProfile
from general_manager.chat.planned.models import (
    EvidenceRequirement,
    PlannedTask,
    ValidatedPlan,
)
from general_manager.chat.planned.planner import PlanningResult
from general_manager.chat.planned.provider_calls import ProviderRoundResult
from general_manager.chat.planned.scheduler import (
    PreparedPlannedTurn,
    SchedulerCallbacks,
    _Runner,
    iter_planned_read_events,
    _parse_action,
    prepare_planned_turn,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)


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


def test_query_timeout_is_capped_when_the_serialized_tool_call_starts() -> None:
    """A queued query must not retain the longer timeout from before the lock."""

    task = _task("task_1")
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="show parts"
    )
    now = [99.979]
    seen_timeouts: list[int] = []

    def execute(
        _name: str, _args: Mapping[str, Any], context: object
    ) -> dict[str, Any]:
        seen_timeouts.append(cast(Any, context).planned_query_timeout_ms)
        return {"status": "success", "data": []}

    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(execute_tool=execute),
        100.0,
        lambda: now[0],
    )
    runner.tool_semaphore = asyncio.Semaphore(0)

    async def run() -> None:
        executing = asyncio.create_task(
            runner.execute_tool(
                runner.runtimes["task_1"],
                ToolCallEvent(
                    "query", "query", {"manager": "PartManager", "fields": []}
                ),
            )
        )
        await asyncio.sleep(0)
        now[0] = 100.0
        runner.tool_semaphore.release()
        await executing

    asyncio.run(run())

    assert seen_timeouts == [1]


class _RoundProbe:
    def __init__(self, *, gate_first_rounds: int = 0) -> None:
        self.gate_first_rounds = gate_first_rounds
        self.in_flight = 0
        self.maximum_in_flight = 0
        self.first_rounds_started = asyncio.Event()
        self.release_first_rounds = asyncio.Event()
        self.rounds_by_task: dict[str, int] = {}
        self.cancelled = False
        self._lock = asyncio.Lock()

    async def enter(self, task_id: str) -> int:
        async with self._lock:
            self.in_flight += 1
            self.maximum_in_flight = max(self.maximum_in_flight, self.in_flight)
            round_number = self.rounds_by_task.get(task_id, 0) + 1
            self.rounds_by_task[task_id] = round_number
            if (
                self.gate_first_rounds
                and round_number == 1
                and self.in_flight >= self.gate_first_rounds
            ):
                self.first_rounds_started.set()
        if self.gate_first_rounds and round_number == 1:
            await self.release_first_rounds.wait()
        return round_number

    async def leave(self) -> None:
        async with self._lock:
            self.in_flight -= 1


class _RoundProbeProvider:
    probe: ClassVar[_RoundProbe | None] = None

    @classmethod
    def from_config(cls, _config: object) -> _RoundProbeProvider:
        return cls()

    async def complete(
        self, messages: list[object], _tools: list[object]
    ) -> AsyncIterator[ToolCallEvent | TextChunkEvent | DoneEvent]:
        content = cast(Any, messages[-1]).content
        task_id = json.loads(content.removeprefix("REFERENCE_DATA="))["task"]["task_id"]
        probe = type(self).probe
        assert probe is not None
        try:
            round_number = await probe.enter(task_id)
            if round_number == 1:
                yield ToolCallEvent(
                    f"{task_id}:query",
                    "query",
                    {"manager": "PartManager", "fields": [task_id]},
                )
            else:
                yield TextChunkEvent(
                    json.dumps(
                        {"action": "complete", "evidence_ids": [f"{task_id}:query:1"]}
                    )
                )
            yield DoneEvent(TokenUsage())
        except asyncio.CancelledError:
            probe.cancelled = True
            raise
        finally:
            await probe.leave()


def _probe_settings(
    *, max_concurrent_tasks: int = 3, evidence_timeout_seconds: float = 90.0
) -> PlannedChatSettings:
    profile = ProviderProfile(
        "probe",
        "tests.unit.test_chat_planned_scheduler._RoundProbeProvider",
        MappingProxyType({"probe": True}),
        "local",
    )
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType({"probe": profile}),
        roles=MappingProxyType(
            {
                "simple_executor": "probe",
                "complex_executor": "probe",
                "fallback_executor": "probe",
                "synthesizer": "probe",
                "planner": "probe",
            }
        ),
        catalog_source=None,
        max_concurrent_tasks=max_concurrent_tasks,
        evidence_timeout_seconds=evidence_timeout_seconds,
    )


def _prepared_probe_roots(
    root_count: int, *, max_concurrent_tasks: int = 3
) -> PreparedPlannedTurn:
    tasks = tuple(
        PlannedTask(
            task_id=f"task_{number}",
            objective="find records",
            depends_on=(),
            requirements=(
                EvidenceRequirement(f"task_{number}_query", "query", "records", None),
            ),
            completion_criteria=(f"task_{number}_query",),
            routing_features=(),
        )
        for number in range(root_count)
    )
    return PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", tasks),
        _probe_settings(max_concurrent_tasks=max_concurrent_tasks),
        user_text="show parts",
    )


def test_scheduler_caps_provider_concurrency_at_three() -> None:
    """Removing the root semaphore would let all six first rounds overlap."""

    probe = _RoundProbe(gate_first_rounds=3)
    _RoundProbeProvider.probe = probe
    prepared = _prepared_probe_roots(6, max_concurrent_tasks=3)
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(
            execute_tool=lambda *_args: {"status": "success", "data": []}
        ),
        100.0,
        lambda: 0.0,
    )

    async def run() -> None:
        execution = asyncio.create_task(runner.run())
        await asyncio.wait_for(probe.first_rounds_started.wait(), timeout=0.2)
        probe.release_first_rounds.set()
        await execution

    asyncio.run(run())

    assert probe.maximum_in_flight == 3
    assert set(runner.result().statuses.values()) == {"resolved"}


class _ToolConcurrencyProbe:
    def __init__(self) -> None:
        self.in_flight = 0
        self.maximum_in_flight = 0
        self.tool_calls = 0
        self.run_sync_calls = 0
        self._lock = threading.Lock()

    def execute(
        self, _name: str, _args: Mapping[str, Any], _context: object
    ) -> dict[str, Any]:
        with self._lock:
            self.in_flight += 1
            self.maximum_in_flight = max(self.maximum_in_flight, self.in_flight)
            self.tool_calls += 1
        try:
            time.sleep(0.01)
            return {"status": "success", "data": []}
        finally:
            with self._lock:
                self.in_flight -= 1

    async def run_sync(
        self, fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        if getattr(fn, "__self__", None) is self:
            self.run_sync_calls += 1
        return await asyncio.to_thread(fn, *args, **kwargs)


def test_scheduler_serializes_application_tools_through_run_sync() -> None:
    """Removing the size-one tool semaphore would overlap the sync executions."""

    probe = _RoundProbe()
    _RoundProbeProvider.probe = probe
    tool_probe = _ToolConcurrencyProbe()
    prepared = _prepared_probe_roots(3)
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(
            execute_tool=tool_probe.execute, run_sync=tool_probe.run_sync
        ),
        100.0,
        lambda: 0.0,
    )

    asyncio.run(runner.run())

    assert tool_probe.maximum_in_flight == 1
    assert tool_probe.run_sync_calls == tool_probe.tool_calls == 3
    assert set(runner.result().statuses.values()) == {"resolved"}


class _BudgetProbeProvider:
    rounds_by_task: ClassVar[dict[str, int]] = {}
    resolved_task_id: ClassVar[str | None] = None

    @classmethod
    def from_config(cls, _config: object) -> _BudgetProbeProvider:
        return cls()

    async def complete(
        self, messages: list[object], _tools: list[object]
    ) -> AsyncIterator[ToolCallEvent | TextChunkEvent | DoneEvent]:
        content = cast(Any, messages[-1]).content
        task_id = json.loads(content.removeprefix("REFERENCE_DATA="))["task"]["task_id"]
        round_number = type(self).rounds_by_task.get(task_id, 0) + 1
        type(self).rounds_by_task[task_id] = round_number
        if task_id == type(self).resolved_task_id and round_number == 2:
            yield TextChunkEvent(
                json.dumps(
                    {"action": "complete", "evidence_ids": [f"{task_id}:query:1"]}
                )
            )
        elif task_id == "task_1" and round_number == 1:
            yield TextChunkEvent(json.dumps({"unexpected": "action"}))
        else:
            fields = [
                task_id,
                str(2 if task_id == "task_1" and round_number == 3 else round_number),
            ]
            yield ToolCallEvent(
                f"{task_id}:{round_number}",
                "query",
                {
                    "manager": "PartManager",
                    "fields": fields,
                },
            )
        yield DoneEvent(TokenUsage())


def _budget_settings() -> PlannedChatSettings:
    profile = ProviderProfile(
        "budget",
        "tests.unit.test_chat_planned_scheduler._BudgetProbeProvider",
        MappingProxyType({"probe": True}),
        "local",
    )
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType({"budget": profile}),
        roles=MappingProxyType(
            {
                "simple_executor": "budget",
                "complex_executor": "budget",
                "fallback_executor": "budget",
                "synthesizer": "budget",
                "planner": "budget",
            }
        ),
        catalog_source=None,
    )


def _budget_task(task_id: str, requirements: int) -> PlannedTask:
    requirement_ids = tuple(
        f"{task_id}_query_{number}" for number in range(requirements)
    )
    return PlannedTask(
        task_id=task_id,
        objective="find records",
        depends_on=(),
        requirements=tuple(
            EvidenceRequirement(requirement_id, "query", "records", None)
            for requirement_id in requirement_ids
        ),
        completion_criteria=requirement_ids,
        routing_features=("multiple_queries",) if requirements > 1 else (),
    )


def test_scheduler_marks_a_subtree_budget_exhausted_after_fifteen_requests() -> None:
    """Changing the subtree admission boundary would permit a sixteenth request."""

    task = _budget_task("task_1", 15)
    _BudgetProbeProvider.rounds_by_task = {}
    _BudgetProbeProvider.resolved_task_id = None
    tool_calls: list[object] = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _budget_settings(), user_text="show parts"
    )

    def execute_tool(*_args: object) -> dict[str, Any]:
        tool_calls.append(object())
        return {"status": "success", "data": []}

    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(execute_tool=execute_tool),
        100.0,
        lambda: 0.0,
    )

    async def run() -> None:
        runtime = runner.runtimes["task_1"]
        runtime.status = "running"
        runtime.role = "complex_executor"
        for _ in range(16):
            await runner._execute_one_pass(runtime, ())

    asyncio.run(run())

    assert _BudgetProbeProvider.rounds_by_task == {"task_1": 15}
    assert len(tool_calls) == 13
    assert prepared.budget.subtree_count("task_1") == 15
    assert runner.result().statuses["task_1"] == "budget_exhausted"


def test_scheduler_global_budget_preserves_resolved_independent_evidence() -> None:
    """Changing the global formula would admit more than 80 total requests."""

    resolved = _budget_task("resolved", 1)
    unfinished = tuple(_budget_task(f"task_{number}", 15) for number in range(5))
    _BudgetProbeProvider.rounds_by_task = {}
    _BudgetProbeProvider.resolved_task_id = "resolved"
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (resolved, *unfinished)),
        _budget_settings(),
        user_text="show parts",
    )
    for _ in range(10):
        prepared.budget.consume_global()
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(
            execute_tool=lambda *_args: {"status": "success", "data": []}
        ),
        100.0,
        lambda: 0.0,
    )

    async def run() -> None:
        resolved_runtime = runner.runtimes["resolved"]
        resolved_runtime.status = "running"
        resolved_runtime.role = "complex_executor"
        await runner._execute_one_pass(resolved_runtime, ())
        await runner._execute_one_pass(resolved_runtime, ())

        unfinished_runtimes = [runner.runtimes[task.task_id] for task in unfinished]
        for runtime in unfinished_runtimes:
            runtime.status = "running"
            runtime.role = "complex_executor"
        while prepared.budget.global_remaining:
            for runtime in unfinished_runtimes:
                if prepared.budget.global_remaining:
                    await runner._execute_one_pass(runtime, ())
        for runtime in unfinished_runtimes:
            await runner._execute_one_pass(runtime, ())

    asyncio.run(run())

    result = runner.result()
    assert prepared.budget.global_limit == 80
    assert prepared.budget.global_count == 80
    assert result.statuses["resolved"] == "resolved"
    assert result.coverage.resolved == 1
    assert result.evidence.for_task("resolved")
    assert {result.statuses[task.task_id] for task in unfinished} == {
        "budget_exhausted"
    }


def test_planning_and_evidence_share_one_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating a fresh evidence deadline would pass a full 90 seconds downstream."""

    task = _task("task_1")
    time_readings = iter((100.0, 189.5))
    timeouts: list[float] = []

    async def planner(*_args: object) -> PlanningResult:
        return PlanningResult(ValidatedPlan("read", (task,)), TokenUsage())

    async def complete(
        _provider: object,
        _messages: list[object],
        _tools: list[object],
        timeout_seconds: float,
    ) -> ProviderRoundResult:
        timeouts.append(timeout_seconds)
        return ProviderRoundResult(
            text=json.dumps({"action": "block", "reason": "manager_unresolved"}),
            tool_call=None,
            usage=TokenUsage(),
        )

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.complete_provider_round", complete
    )
    prepared = asyncio.run(
        prepare_planned_turn(
            "show parts",
            [],
            _probe_settings(evidence_timeout_seconds=90),
            {},
            planner=planner,
            resolver=cast(Any, _StableExactResolver()),
            clock=lambda: next(time_readings),
        )
    )
    asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                clock=lambda: 189.5,
            )
        )
    )

    assert timeouts == [pytest.approx(0.5)]


class _DeadlineProbeProvider:
    entered: ClassVar[asyncio.Event | None] = None
    release: ClassVar[asyncio.Event | None] = None
    rounds_by_task: ClassVar[dict[str, int]] = {}
    cancelled: ClassVar[bool] = False

    @classmethod
    def from_config(cls, _config: object) -> _DeadlineProbeProvider:
        return cls()

    async def complete(
        self, messages: list[object], _tools: list[object]
    ) -> AsyncIterator[ToolCallEvent | TextChunkEvent | DoneEvent]:
        content = cast(Any, messages[-1]).content
        task_id = json.loads(content.removeprefix("REFERENCE_DATA="))["task"]["task_id"]
        if task_id == "task_2":
            entered = type(self).entered
            release = type(self).release
            assert entered is not None and release is not None
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                type(self).cancelled = True
                raise
        else:
            round_number = type(self).rounds_by_task.get(task_id, 0) + 1
            type(self).rounds_by_task[task_id] = round_number
            if round_number == 1:
                yield ToolCallEvent(
                    "query", "query", {"manager": "PartManager", "fields": []}
                )
            else:
                yield TextChunkEvent(
                    json.dumps(
                        {"action": "complete", "evidence_ids": [f"{task_id}:query:1"]}
                    )
                )
            yield DoneEvent(TokenUsage())


def _deadline_settings() -> PlannedChatSettings:
    profile = ProviderProfile(
        "deadline",
        "tests.unit.test_chat_planned_scheduler._DeadlineProbeProvider",
        MappingProxyType({"probe": True}),
        "local",
    )
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType({"deadline": profile}),
        roles=MappingProxyType(
            {
                "simple_executor": "deadline",
                "complex_executor": "deadline",
                "fallback_executor": "deadline",
                "synthesizer": "deadline",
                "planner": "deadline",
            }
        ),
        catalog_source=None,
    )


def _deadline_tasks() -> tuple[PlannedTask, PlannedTask]:
    return (
        PlannedTask(
            "task_1",
            "find records",
            (),
            (EvidenceRequirement("task_1_query", "query", "records", None),),
            ("task_1_query",),
            (),
        ),
        PlannedTask(
            "task_2",
            "find more records",
            (),
            (EvidenceRequirement("task_2_query", "query", "records", None),),
            ("task_2_query",),
            (),
        ),
    )


def test_evidence_deadline_cancels_async_provider_and_keeps_resolved_evidence() -> None:
    """Removing deadline cleanup would leave task_2 running and discard no result."""

    _DeadlineProbeProvider.entered = asyncio.Event()
    _DeadlineProbeProvider.release = asyncio.Event()
    _DeadlineProbeProvider.rounds_by_task = {}
    _DeadlineProbeProvider.cancelled = False
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", _deadline_tasks()),
        _deadline_settings(),
        user_text="parts",
    )

    async def run() -> _Runner:
        loop = asyncio.get_running_loop()
        runner = _Runner(
            prepared,
            {},
            None,
            [],
            SchedulerCallbacks(
                execute_tool=lambda *_args: {"status": "success", "data": []}
            ),
            loop.time() + 0.03,
            loop.time,
        )
        await runner.run()
        return runner

    runner = asyncio.run(run())
    result = runner.result()

    assert _DeadlineProbeProvider.cancelled is True
    assert result.coverage.resolved == 1
    assert result.reasons["task_2"] == "deadline_exceeded"


def test_cancelling_the_public_iterator_cleans_up_the_in_flight_provider() -> None:
    """Removing iterator cleanup would leak the provider task after disconnect."""

    _DeadlineProbeProvider.entered = asyncio.Event()
    _DeadlineProbeProvider.release = asyncio.Event()
    _DeadlineProbeProvider.rounds_by_task = {}
    _DeadlineProbeProvider.cancelled = False
    task = _deadline_tasks()[1]
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _deadline_settings(), user_text="parts"
    )

    async def run() -> None:
        iterator = cast(
            Any,
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
            ),
        )
        next_event: asyncio.Task[dict[str, Any]] = asyncio.create_task(
            iterator.__anext__()
        )
        entered = _DeadlineProbeProvider.entered
        assert entered is not None
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        next_event.cancel()
        with pytest.raises(asyncio.CancelledError):
            await next_event
        await iterator.aclose()

    asyncio.run(run())

    assert _DeadlineProbeProvider.cancelled is True
    assert prepared.result is None


def test_sync_tool_cancellation_is_best_effort_for_the_worker_thread() -> None:
    """Cancellation must stop awaiting the sync seam without claiming it killed work."""

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    task = _task("task_1")
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="show parts"
    )

    def execute(
        _name: str, _args: Mapping[str, Any], _context: object
    ) -> dict[str, Any]:
        started.set()
        release.wait()
        finished.set()
        return {"status": "success", "data": []}

    async def run_sync(fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)

    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(execute_tool=execute, run_sync=run_sync),
        100.0,
        lambda: 0.0,
    )

    async def run() -> None:
        executing = asyncio.create_task(
            runner.execute_tool(
                runner.runtimes["task_1"],
                ToolCallEvent(
                    "query", "query", {"manager": "PartManager", "fields": []}
                ),
            )
        )
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=0.2)
        executing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await executing
        assert finished.is_set() is False
        release.set()
        await asyncio.wait_for(asyncio.to_thread(finished.wait), timeout=0.2)

    asyncio.run(run())
