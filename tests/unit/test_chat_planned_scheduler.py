"""Focused scheduler contracts for the planned chat execution boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import json
import threading
import time
from dataclasses import replace
from types import MappingProxyType
from typing import Any, ClassVar, cast

import pytest
from django.test.utils import override_settings

from general_manager.chat.planned.config import PlannedChatSettings, ProviderProfile
from general_manager.chat.planned.models import (
    EvidenceRequirement,
    PlannedTask,
    ValidatedPlan,
)
from general_manager.chat.planned.planner import PlanningResult
from general_manager.chat.planned.budget import RoundBudgetExhausted
from general_manager.chat.planned.provider_calls import (
    InvalidProviderRoundError,
    ProviderRoundResult,
)
from general_manager.chat.planned.events import PLANNED_PUBLIC_MESSAGES
from general_manager.chat.planned.scheduler import (
    PreparedPlannedTurn,
    SchedulerCallbacks,
    _Runner,
    iter_planned_read_events,
    _parse_action,
    prepare_planned_turn,
)
from general_manager.chat.planned.synthesis import SynthesisResult
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)


PLANNED_AUDIT_EVENTS: list[dict[str, object]] = []


def _capture_planned_audit_event(event: dict[str, object]) -> None:
    PLANNED_AUDIT_EVENTS.append(event)


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


@override_settings(
    GENERAL_MANAGER={
        "CHAT": {
            "audit": {
                "enabled": True,
                "level": "tool_calls",
                "logger": "tests.unit.test_chat_planned_scheduler._capture_planned_audit_event",
            }
        }
    }
)
def test_scheduler_audit_hashes_planner_task_lineage_for_every_sink() -> None:
    """Raw planner task IDs must never reach generic or legacy audit sinks."""
    import hashlib

    root_id = "HiddenManagerRoot"
    child_id = "HiddenManagerChild"
    root = _task(root_id)
    child = _dynamic_child(child_id, depends_on=[root_id])
    _Executor.responses = [
        {
            "answer": "The requested record is available.",
            "evidence_ids": [f"{root_id}:child:{child_id}:query:1"],
        }
    ]
    _Executor.responses_by_task = {
        root_id: [
            {"action": "spawn_children", "children": [child]},
            {
                "action": "complete",
                "evidence_ids": [f"{root_id}:child:{child_id}:query:1"],
            },
        ],
        child_id: [
            ToolCallEvent(
                "child-query",
                "query",
                {"manager": "PartManager", "fields": ["name"]},
            ),
            {"action": "complete", "evidence_ids": [f"{child_id}:query:1"]},
        ],
    }
    PLANNED_AUDIT_EVENTS.clear()
    legacy_audit_events: list[tuple[str, dict[str, object]]] = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (root,)), _settings(), user_text="show parts"
    )

    events = asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(
                    execute_tool=lambda *_args: {"status": "success", "data": []},
                    emit_audit_event=lambda event_type,
                    payload: legacy_audit_events.append((event_type, payload)),
                ),
            )
        )
    )

    root_hash = hashlib.sha256(root_id.encode()).hexdigest()
    child_hash = hashlib.sha256(child_id.encode()).hexdigest()
    serialized = json.dumps([PLANNED_AUDIT_EVENTS, legacy_audit_events])
    assert events[-1]["type"] == "done"
    assert root_id not in serialized
    assert child_id not in serialized
    assert root_hash in serialized
    assert child_hash in serialized
    assert {
        payload["task_id"]
        for event_type, payload in legacy_audit_events
        if event_type in {"planned_tool_call", "planned_tool_result"}
    } == {child_hash}


@override_settings(
    GENERAL_MANAGER={
        "CHAT": {
            "audit": {
                "enabled": True,
                "level": "tool_calls",
                "logger": "tests.unit.test_chat_planned_scheduler._capture_planned_audit_event",
            }
        }
    }
)
def test_synthesis_round_budget_exhaustion_has_budget_terminal_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hard synthesis admission limit is public/audited as budget exhaustion."""
    _Executor.responses = []
    _Executor.responses_by_task = {
        "task_1": [
            ToolCallEvent(
                "query", "query", {"manager": "PartManager", "fields": ["name"]}
            ),
            {"action": "complete", "evidence_ids": ["task_1:query:1"]},
        ]
    }

    async def exhausted_synthesis(*_args: object, **_kwargs: object) -> SynthesisResult:
        raise RoundBudgetExhausted("")

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.synthesize_answer", exhausted_synthesis
    )
    PLANNED_AUDIT_EVENTS.clear()
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)), _settings(), user_text="show parts"
    )

    events = asyncio.run(
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

    assert events[-1] == {
        "type": "error",
        "code": "budget_exhausted",
        "message": PLANNED_PUBLIC_MESSAGES["budget_exhausted"],
    }
    assert {
        "event_type": "planned_terminal",
        "coverage": {"resolved": 1, "total": 1},
        "terminal_reason": "budget_exhausted",
    } in PLANNED_AUDIT_EVENTS


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


def _dynamic_child(task_id: str, *, depends_on: list[str]) -> dict[str, object]:
    """Return a hand-written dynamic child accepted by the graph validator."""

    requirement_id = f"{task_id}_query"
    return {
        "task_id": task_id,
        "objective": "find dependent records",
        "depends_on": depends_on,
        "requirements": [
            {
                "requirement_id": requirement_id,
                "kind": "query",
                "description": "records",
                "operation": None,
            }
        ],
        "completion_criteria": [requirement_id],
        "routing_features": ["has_dependency"],
    }


def _dynamic_parent() -> PlannedTask:
    return PlannedTask(
        task_id="task_1",
        objective="find records and their dependencies",
        depends_on=(),
        requirements=(
            EvidenceRequirement("parent_first", "query", "first records", None),
            EvidenceRequirement("parent_second", "query", "second records", None),
        ),
        completion_criteria=("parent_first", "parent_second"),
        routing_features=("multiple_queries",),
    )


def test_dynamic_children_handoff_parent_owned_snapshots_only_to_synthesis() -> None:
    """Leaking original child records to synthesis bypasses their parent handoff."""

    parent = _dynamic_parent()
    first_id, second_id = "task_1_child_1", "task_1_child_2"
    _Executor.responses = [
        {
            "answer": "Both dependent record sets are empty.",
            "evidence_ids": [
                f"task_1:child:{first_id}:query:1",
                f"task_1:child:{second_id}:query:1",
            ],
        }
    ]
    _Executor.responses_by_task = {
        "task_1": [
            {
                "action": "spawn_children",
                "children": [
                    _dynamic_child(first_id, depends_on=[parent.task_id]),
                    _dynamic_child(second_id, depends_on=[parent.task_id]),
                ],
            },
            {
                "action": "complete",
                "evidence_ids": [
                    f"task_1:child:{first_id}:query:1",
                    f"task_1:child:{second_id}:query:1",
                ],
            },
        ],
        first_id: [
            ToolCallEvent(
                "first-query",
                "query",
                {"manager": "PartManager", "fields": ["first"]},
            ),
            {"action": "complete", "evidence_ids": [f"{first_id}:query:1"]},
        ],
        second_id: [
            ToolCallEvent(
                "second-query",
                "query",
                {"manager": "PartManager", "fields": ["second"]},
            ),
            {"action": "complete", "evidence_ids": [f"{second_id}:query:1"]},
        ],
    }
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (parent,)), _settings(), user_text="show dependencies"
    )

    events = asyncio.run(
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

    assert events[-1]["type"] == "done"
    assert events[-1]["orchestration"] == {
        "status": "complete",
        "coverage": {"resolved": 1, "total": 1},
        "unresolved": [],
    }
    assert prepared.result is not None
    result = prepared.result
    assert result.statuses == {
        "task_1": "resolved",
        first_id: "resolved",
        second_id: "resolved",
    }
    parent_evidence = result.evidence.for_task("task_1")
    assert len(parent_evidence) == 2
    assert all(record.task_id == "task_1" for record in parent_evidence)
    synthesis_reference = json.loads(
        _Executor.calls[-1][-1].content.removeprefix("RESOLVED_REFERENCE_DATA=")
    )
    assert {item["task_id"] for item in synthesis_reference["resolved_evidence"]} == {
        "task_1"
    }


def test_dynamic_handoff_does_not_satisfy_a_same_named_root_requirement() -> None:
    """Parent handoff remains private when an independent root shares its ID."""
    requirement = EvidenceRequirement("shared", "query", "records", None)
    parent = PlannedTask(
        "parent", "find dependent records", (), (requirement,), ("shared",), ()
    )
    child = _dynamic_child("parent_child", depends_on=["parent"])
    independent = PlannedTask(
        "independent", "find records", (), (requirement,), ("shared",), ()
    )
    _Executor.responses = []
    _Executor.responses_by_task = {
        "parent": [
            {"action": "spawn_children", "children": [child]},
            {
                "action": "complete",
                "evidence_ids": ["parent:child:parent_child:query:1"],
            },
        ],
        "parent_child": [
            ToolCallEvent(
                "child-query", "query", {"manager": "PartManager", "fields": []}
            ),
            {"action": "complete", "evidence_ids": ["parent_child:query:1"]},
        ],
        "independent": [
            ToolCallEvent(
                "root-query", "query", {"manager": "PartManager", "fields": []}
            ),
            {"action": "complete", "evidence_ids": ["independent:query:1"]},
        ],
    }
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (parent, independent)),
        _settings(),
        user_text="show records",
    )
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
        await runner.run_task(runner.runtimes["parent"])
        await runner.run_task(runner.runtimes["independent"])

    asyncio.run(run())

    assert runner.result().statuses == {
        "parent": "resolved",
        "independent": "resolved",
        "parent_child": "resolved",
    }
    assert [
        record.evidence_id
        for record in runner.evidence.for_requirement("parent", requirement)
    ] == ["parent:child:parent_child:query:1"]
    assert [
        record.evidence_id
        for record in runner.evidence.for_requirement("independent", requirement)
    ] == ["independent:query:1"]


def test_child_failure_blocks_parent_but_not_an_independent_root() -> None:
    """A failed child must not turn a sibling root's grounded answer into an error."""

    parent = _dynamic_parent()
    independent = _task("task_2")
    child_id = "task_1_child"
    _Executor.responses = [
        {"answer": "No independent records found.", "evidence_ids": ["task_2:query:1"]}
    ]
    _Executor.responses_by_task = {
        "task_1": [
            {
                "action": "spawn_children",
                "children": [_dynamic_child(child_id, depends_on=[parent.task_id])],
            }
        ],
        child_id: [{"action": "block", "reason": "manager_unresolved"}],
        "task_2": [
            ToolCallEvent(
                "independent-query",
                "query",
                {"manager": "PartManager", "fields": ["name"]},
            ),
            {"action": "complete", "evidence_ids": ["task_2:query:1"]},
        ],
    }
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (parent, independent)),
        _settings(max_concurrent_tasks=1),
        user_text="show records",
    )

    events = asyncio.run(
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
    assert prepared.result.reasons["task_1"] == "dependency_blocked"
    assert prepared.result.statuses["task_2"] == "resolved"
    assert [event["type"] for event in events].count("done") == 1
    assert [event["type"] for event in events].count("error") == 0
    assert events[-1]["type"] == "done"
    assert events[-1]["orchestration"] == {
        "status": "partial",
        "coverage": {"resolved": 1, "total": 2},
        "unresolved": [{"task_id": "task_1", "reason": "dependency_blocked"}],
    }


class _ChildDeadlineProvider:
    clock: ClassVar[list[float]] = []
    rounds: ClassVar[dict[str, int]] = {}

    @classmethod
    def from_config(cls, _config: object) -> _ChildDeadlineProvider:
        return cls()

    async def complete(
        self, messages: list[object], _tools: list[object]
    ) -> AsyncIterator[ToolCallEvent | TextChunkEvent | DoneEvent]:
        content = cast(Any, messages[-1]).content
        task_id = json.loads(content.removeprefix("REFERENCE_DATA="))["task"]["task_id"]
        round_number = type(self).rounds.get(task_id, 0) + 1
        type(self).rounds[task_id] = round_number
        if task_id == "task_1":
            yield TextChunkEvent(
                json.dumps(
                    {
                        "action": "spawn_children",
                        "children": [
                            _dynamic_child("task_1_child", depends_on=["task_1"])
                        ],
                    }
                )
            )
            yield DoneEvent(TokenUsage())
            type(self).clock[0] = 10.0
            return
        if round_number == 1:
            yield ToolCallEvent(
                "independent-query",
                "query",
                {"manager": "PartManager", "fields": []},
            )
        else:
            yield TextChunkEvent(
                json.dumps({"action": "complete", "evidence_ids": ["task_2:query:1"]})
            )
        yield DoneEvent(TokenUsage())


def _child_deadline_settings() -> PlannedChatSettings:
    profile = ProviderProfile(
        "child_deadline",
        "tests.unit.test_chat_planned_scheduler._ChildDeadlineProvider",
        MappingProxyType({"probe": True}),
        "local",
    )
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType({"child_deadline": profile}),
        roles=MappingProxyType(
            {
                "simple_executor": "child_deadline",
                "complex_executor": "child_deadline",
                "fallback_executor": "child_deadline",
                "synthesizer": "child_deadline",
                "planner": "child_deadline",
            }
        ),
        catalog_source=None,
    )


def test_child_deadline_marks_parent_and_preserves_independent_root_coverage() -> None:
    """Flattening every child failure loses the parent deadline reason."""

    now = [0.0]
    parent = _dynamic_parent()
    independent = _task("task_2")
    _ChildDeadlineProvider.clock = now
    _ChildDeadlineProvider.rounds = {}
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (parent, independent)),
        _child_deadline_settings(),
        user_text="show records",
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(
            execute_tool=lambda *_args: {"status": "success", "data": []}
        ),
        10.0,
        lambda: now[0],
    )

    async def run() -> None:
        await runner.run_task(runner.runtimes["task_2"])
        await runner.run_task(runner.runtimes["task_1"])

    asyncio.run(run())
    result = runner.result()

    assert result.statuses["task_2"] == "resolved"
    assert result.reasons["task_1"] == "deadline_exceeded"
    assert result.coverage.resolved == 1
    assert result.coverage.total == 2
    assert result.coverage.unresolved == (("task_1", "deadline_exceeded"),)


def test_scheduler_rejects_a_third_dynamic_child_across_repeated_actions() -> None:
    """Removing existing-child validation would schedule the third child."""

    parent = _task("task_1")
    first_id, second_id = "task_1_first", "task_1_second"
    third_child = _dynamic_child("task_1_third", depends_on=[parent.task_id])
    _Executor.responses = []
    _Executor.responses_by_task = {
        "task_1": [
            {
                "action": "spawn_children",
                "children": [
                    _dynamic_child(first_id, depends_on=[parent.task_id]),
                    _dynamic_child(second_id, depends_on=[parent.task_id]),
                ],
            },
            *[{"action": "spawn_children", "children": [third_child]}] * 4,
        ],
        first_id: [
            ToolCallEvent(
                "first-query", "query", {"manager": "PartManager", "fields": []}
            ),
            {"action": "complete", "evidence_ids": [f"{first_id}:query:1"]},
        ],
        second_id: [
            ToolCallEvent(
                "second-query", "query", {"manager": "PartManager", "fields": []}
            ),
            {"action": "complete", "evidence_ids": [f"{second_id}:query:1"]},
        ],
    }
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (parent,)), _settings(), user_text="show records"
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
    assert set(prepared.result.statuses) == {"task_1", first_id, second_id}
    assert prepared.result.reasons["task_1"] == "provider_failed"


def test_scheduler_rejects_recursive_dynamic_child_actions() -> None:
    """Allowing a child to spawn would bypass the one-level subtree bound."""

    parent = _task("task_1")
    child_id = "task_1_child"
    grandchild = _dynamic_child("task_1_grandchild", depends_on=[child_id])
    _Executor.responses = []
    _Executor.responses_by_task = {
        "task_1": [
            {
                "action": "spawn_children",
                "children": [_dynamic_child(child_id, depends_on=[parent.task_id])],
            }
        ],
        child_id: [{"action": "spawn_children", "children": [grandchild]}] * 4,
    }
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (parent,)), _settings(), user_text="show records"
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
    assert "task_1_grandchild" not in prepared.result.statuses
    assert prepared.result.reasons["task_1"] == "dependency_blocked"


def test_scheduler_rejects_dynamic_child_dependency_on_another_root() -> None:
    """A cross-subtree dependency must not add the child to either root's graph."""

    parent, other_root = _task("task_1"), _task("task_2")
    invalid_child = _dynamic_child("task_1_child", depends_on=[other_root.task_id])
    _Executor.responses = []
    _Executor.responses_by_task = {
        "task_1": [{"action": "spawn_children", "children": [invalid_child]}] * 4,
        "task_2": [{"action": "block", "reason": "manager_unresolved"}],
    }
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (parent, other_root)),
        _settings(max_concurrent_tasks=1),
        user_text="show records",
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
    assert set(prepared.result.statuses) == {"task_1", "task_2"}
    assert prepared.result.reasons["task_1"] == "provider_failed"


def test_dynamic_child_rounds_exhaust_the_owning_root_budget() -> None:
    """Giving children an independent ledger would leave the root below 15 rounds."""

    parent = _dynamic_parent()
    first_id, second_id = "task_1_first", "task_1_second"

    def child_with_queries(task_id: str, count: int) -> dict[str, object]:
        requirement_ids = [f"{task_id}_query_{number}" for number in range(count)]
        return {
            "task_id": task_id,
            "objective": "find all dependent records",
            "depends_on": [parent.task_id],
            "requirements": [
                {
                    "requirement_id": requirement_id,
                    "kind": "query",
                    "description": "records",
                    "operation": None,
                }
                for requirement_id in requirement_ids
            ],
            "completion_criteria": requirement_ids,
            "routing_features": ["has_dependency", "multiple_queries"],
        }

    _Executor.responses = []
    _Executor.responses_by_task = {
        "task_1": [
            {
                "action": "spawn_children",
                "children": [child_with_queries(first_id, 8)],
            },
            {
                "action": "spawn_children",
                "children": [child_with_queries(second_id, 2)],
            },
            {
                "action": "complete",
                "evidence_ids": [
                    f"task_1:child:{first_id}:query:1",
                    f"task_1:child:{second_id}:query:1",
                ],
            },
        ],
        first_id: [
            ToolCallEvent(
                f"query-{number}",
                "query",
                {"manager": "PartManager", "fields": [str(number)]},
            )
            for number in range(8)
        ]
        + [
            {
                "action": "complete",
                "evidence_ids": [
                    f"{first_id}:query:{number + 1}" for number in range(8)
                ],
            }
        ],
        second_id: [
            ToolCallEvent(
                f"second-query-{number}",
                "query",
                {"manager": "PartManager", "fields": [str(number)]},
            )
            for number in range(2)
        ]
        + [
            {
                "action": "complete",
                "evidence_ids": [
                    f"{second_id}:query:{number + 1}" for number in range(2)
                ],
            }
        ],
    }
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (parent,)), _settings(), user_text="show records"
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
    assert prepared.result.statuses[first_id] == "resolved"
    assert prepared.result.statuses[second_id] == "resolved"
    assert prepared.result.statuses[parent.task_id] == "budget_exhausted"
    assert prepared.budget.subtree_count(parent.task_id) == 15


def _resolved_executor_responses() -> None:
    _Executor.responses = [
        {"answer": "No parts found.", "evidence_ids": ["task_1:query:1"]}
    ]
    _Executor.responses_by_task = {
        "task_1": [
            ToolCallEvent(
                "query", "query", {"manager": "PartManager", "fields": ["name"]}
            ),
            {"action": "complete", "evidence_ids": ["task_1:query:1"]},
        ]
    }


@pytest.mark.parametrize(
    "callback_name",
    ["append_message", "emit_tool_called", "emit_audit_event", "enforce_rate_limit"],
)
def test_callback_failure_does_not_change_a_committed_planned_result(
    callback_name: str,
) -> None:
    """Any optional persistence, signal, audit, or limiter failure stays private."""

    _resolved_executor_responses()
    tool_calls: list[str] = []

    def raise_callback(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError

    callback_values: dict[str, Any] = {
        "execute_tool": lambda name, *_args: tool_calls.append(name)
        or {"status": "success", "data": []},
        "append_message": lambda *_args, **_kwargs: None,
        "emit_tool_called": lambda **_kwargs: None,
        "emit_audit_event": lambda *_args, **_kwargs: None,
        "enforce_rate_limit": lambda *_args, **_kwargs: None,
    }
    callback_values[callback_name] = raise_callback
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)), _settings(), user_text="show parts"
    )

    events = asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=object(),
                messages=[],
                callbacks=SchedulerCallbacks(**callback_values),
            )
        )
    )

    assert [event["type"] for event in events].count("done") == 1
    assert [event["type"] for event in events].count("error") == 0
    assert tool_calls == ["query"]
    assert prepared.result is not None
    assert len(prepared.result.evidence.for_task("task_1")) == 1


def test_uncached_tool_result_and_synthesized_answer_are_persisted_once() -> None:
    """Persisting duplicate tool calls would create misleading conversation history."""

    _Executor.responses = [
        {"answer": "No parts found.", "evidence_ids": ["task_1:query:1"]}
    ]
    _Executor.responses_by_task = {
        "task_1": [
            ToolCallEvent(
                "one", "query", {"manager": "PartManager", "fields": ["name"]}
            ),
            ToolCallEvent(
                "two", "query", {"fields": ["name"], "manager": "PartManager"}
            ),
            {"action": "complete", "evidence_ids": ["task_1:query:1"]},
        ]
    }
    persisted: list[dict[str, object]] = []
    executions: list[str] = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)), _settings(), user_text="show parts"
    )

    def append_message(*_args: object, **kwargs: object) -> None:
        persisted.append(dict(kwargs))

    asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=object(),
                messages=[],
                callbacks=SchedulerCallbacks(
                    execute_tool=lambda name, *_args: executions.append(name)
                    or {"status": "success", "data": []},
                    append_message=append_message,
                    emit_tool_called=lambda **_kwargs: None,
                    enforce_rate_limit=lambda *_args, **_kwargs: None,
                ),
            )
        )
    )

    assert executions == ["query"]
    assert [record["role"] for record in persisted] == ["tool", "assistant"]
    assert persisted[0]["tool_name"] == "query"
    assert "tool_name" not in persisted[1]


@pytest.mark.parametrize(
    ("synthesis_responses", "terminal_type", "attempt_count"),
    [
        (
            [{"answer": "No parts found.", "evidence_ids": ["task_1:query:1"]}],
            "done",
            3,
        ),
        (
            [
                {"answer": "Ungrounded.", "evidence_ids": ["missing"]},
                {"answer": "No parts found.", "evidence_ids": ["task_1:query:1"]},
            ],
            "done",
            4,
        ),
        (
            [
                {"answer": "Ungrounded.", "evidence_ids": ["missing"]},
                {"answer": "Still ungrounded.", "evidence_ids": ["missing"]},
            ],
            "error",
            4,
        ),
    ],
)
def test_reported_usage_is_limited_once_for_executor_and_synthesis_attempts(
    synthesis_responses: list[dict[str, object]],
    terminal_type: str,
    attempt_count: int,
) -> None:
    """Dropping a failed/fallback usage report makes public and limiter totals diverge."""

    _Executor.responses = synthesis_responses
    _Executor.responses_by_task = {
        "task_1": [
            ToolCallEvent(
                "query", "query", {"manager": "PartManager", "fields": ["name"]}
            ),
            {"action": "complete", "evidence_ids": ["task_1:query:1"]},
        ]
    }
    limiter_calls: list[dict[str, object]] = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)), _settings(), user_text="show parts"
    )

    events = asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(
                    execute_tool=lambda *_args: {"status": "success", "data": []},
                    enforce_rate_limit=lambda _scope, **kwargs: limiter_calls.append(
                        dict(kwargs)
                    ),
                ),
            )
        )
    )

    assert [event["type"] for event in events][-1] == terminal_type
    assert (
        limiter_calls
        == [{"input_tokens": 1, "output_tokens": 1, "count_request": False}]
        * attempt_count
    )
    if terminal_type == "done":
        assert events[-1]["usage"] == {
            "input_tokens": attempt_count,
            "output_tokens": attempt_count,
        }


@pytest.mark.parametrize(
    "reported_usage",
    [TokenUsage(), TokenUsage(input_tokens=3, output_tokens=5)],
)
def test_malformed_executor_attempt_usage_is_recorded_before_private_limiter_failure(
    monkeypatch: pytest.MonkeyPatch,
    reported_usage: TokenUsage,
) -> None:
    """A missing or malformed provider result still has one known usage boundary."""

    async def malformed_round(*_args: object, **_kwargs: object) -> ProviderRoundResult:
        raise InvalidProviderRoundError("malformed", usage=reported_usage)

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.complete_provider_round",
        malformed_round,
    )
    limiter_calls: list[dict[str, object]] = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)), _settings(), user_text="show parts"
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(
            execute_tool=lambda *_args: {},
            enforce_rate_limit=lambda _scope, **kwargs: limiter_calls.append(
                dict(kwargs)
            ),
        ),
        100.0,
        lambda: 0.0,
    )
    runtime = runner.runtimes["task_1"]
    runtime.status = "running"
    runtime.role = "complex_executor"

    failure_reason = asyncio.run(runner._execute_one_pass(runtime, ()))

    assert failure_reason == "provider_failed"
    assert runner.usage == reported_usage
    assert limiter_calls == [
        {
            "input_tokens": reported_usage.input_tokens,
            "output_tokens": reported_usage.output_tokens,
            "count_request": False,
        }
    ]


def test_expired_executor_admission_does_not_consume_budget_or_call_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Charging before deadline admission leaks a round when no provider can start."""
    provider_calls: list[object] = []

    async def complete(*_args: object, **_kwargs: object) -> ProviderRoundResult:
        provider_calls.append(object())
        raise AssertionError

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.complete_provider_round", complete
    )
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)), _settings(), user_text="show parts"
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(execute_tool=lambda *_args: {}),
        10.0,
        lambda: 10.0,
    )
    runtime = runner.runtimes["task_1"]
    runtime.status = "running"
    runtime.role = "complex_executor"

    failure_reason = asyncio.run(runner._execute_one_pass(runtime, ()))

    assert failure_reason is None
    assert provider_calls == []
    assert prepared.budget.subtree_count("task_1") == 0
    assert (runtime.status, runtime.reason) == ("blocked", "deadline_exceeded")


def test_executor_passes_its_admission_timeout_without_recomputing_the_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later clock read must not shorten the timeout admitted before charging."""
    received_timeouts: list[float] = []
    clock_readings = iter((9.5, 9.6, 9.9))

    async def complete(
        _provider: object,
        _messages: list[object],
        _tools: list[object],
        timeout_seconds: float,
    ) -> ProviderRoundResult:
        received_timeouts.append(timeout_seconds)
        return ProviderRoundResult(
            text=json.dumps({"action": "block", "reason": "manager_unresolved"}),
            tool_call=None,
            usage=TokenUsage(),
        )

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.complete_provider_round", complete
    )
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)), _settings(), user_text="show parts"
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(execute_tool=lambda *_args: {}),
        10.0,
        lambda: next(clock_readings),
    )
    runtime = runner.runtimes["task_1"]
    runtime.status = "running"
    runtime.role = "complex_executor"

    asyncio.run(runner._execute_one_pass(runtime, ()))

    assert received_timeouts == [pytest.approx(0.5)]


def test_synthesis_usage_is_accounted_from_its_immutable_attempt_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Using only aggregate synthesis usage would double-charge fallback attempts."""

    _resolved_executor_responses()

    async def synthesis_with_two_attempts(
        *_args: object, **_kwargs: object
    ) -> SynthesisResult:
        return SynthesisResult(
            "No parts found.",
            ("task_1:query:1",),
            TokenUsage(input_tokens=12, output_tokens=16),
            (
                TokenUsage(input_tokens=5, output_tokens=7),
                TokenUsage(input_tokens=7, output_tokens=9),
            ),
        )

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.synthesize_answer",
        synthesis_with_two_attempts,
    )
    limiter_calls: list[dict[str, object]] = []
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (_task("task_1"),)), _settings(), user_text="show parts"
    )

    events = asyncio.run(
        _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(
                    execute_tool=lambda *_args: {"status": "success", "data": []},
                    enforce_rate_limit=lambda _scope, **kwargs: limiter_calls.append(
                        dict(kwargs)
                    ),
                ),
            )
        )
    )

    assert limiter_calls == [
        {"input_tokens": 1, "output_tokens": 1, "count_request": False},
        {"input_tokens": 1, "output_tokens": 1, "count_request": False},
        {"input_tokens": 5, "output_tokens": 7, "count_request": False},
        {"input_tokens": 7, "output_tokens": 9, "count_request": False},
    ]
    assert events[-1]["usage"] == {"input_tokens": 14, "output_tokens": 18}


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
        "complex_executor",
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
        "complex_executor",
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

    assert _Executor.roles[:5] == [
        "simple_executor",
        "complex_executor",
        "complex_executor",
        "complex_executor",
        "fallback_executor",
    ]
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
    now = [99.97]
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
        now[0] = 99.98
        runner.tool_semaphore.release()
        await executing

    asyncio.run(run())

    assert seen_timeouts == [20]


class _RoundProbe:
    def __init__(self, *, gate_first_rounds: int = 0) -> None:
        self.gate_first_rounds = gate_first_rounds
        self.in_flight = 0
        self.maximum_in_flight = 0
        self.first_rounds_started = asyncio.Event()
        self.release_first_rounds = asyncio.Event()
        self.rounds_by_task: dict[str, int] = {}
        self.started_tasks: list[str] = []
        self.cancelled = False
        self._lock = asyncio.Lock()

    async def enter(self, task_id: str) -> int:
        async with self._lock:
            self.in_flight += 1
            self.maximum_in_flight = max(self.maximum_in_flight, self.in_flight)
            round_number = self.rounds_by_task.get(task_id, 0) + 1
            self.rounds_by_task[task_id] = round_number
            if round_number == 1:
                self.started_tasks.append(task_id)
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


def test_scheduler_admits_ready_roots_in_plan_order() -> None:
    set_order = tuple(set(f"root_{number}" for number in range(6)))
    plan_order = tuple(reversed(set_order))
    assert tuple(set(plan_order)) != plan_order
    tasks = tuple(_task(task_id) for task_id in plan_order)
    probe = _RoundProbe(gate_first_rounds=2)
    _RoundProbeProvider.probe = probe
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", tasks),
        _probe_settings(max_concurrent_tasks=2),
        user_text="show parts",
    )
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
        assert probe.started_tasks == list(plan_order[:2])
        probe.release_first_rounds.set()
        await execution

    asyncio.run(run())


def test_scheduler_runs_ready_children_in_creation_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_order = tuple(set(f"child_{number}" for number in range(4)))
    child_order = tuple(reversed(set_order))
    assert tuple(set(child_order)) != child_order
    children = tuple(_task(task_id) for task_id in child_order)
    parent = _task("parent")
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (parent,)), _settings(), user_text="show parts"
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(execute_tool=lambda *_args: {}),
        100.0,
        lambda: 0.0,
    )
    runtime = runner.runtimes[parent.task_id]
    runtime.status = "running"
    runtime.role = "simple_executor"
    started: list[str] = []

    async def complete(*_args: object, **_kwargs: object) -> ProviderRoundResult:
        return ProviderRoundResult(
            text=json.dumps({"action": "spawn_children", "children": []}),
            tool_call=None,
            usage=TokenUsage(),
        )

    async def run_child(child_runtime: Any) -> None:
        started.append(child_runtime.task.task_id)
        child_runtime.status = "resolved"

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.complete_provider_round", complete
    )
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.validate_dynamic_children",
        lambda *_args: children,
    )
    monkeypatch.setattr(runner, "run_task", run_child)

    failure_reason = asyncio.run(runner._execute_one_pass(runtime, ()))

    assert failure_reason is None
    assert started == list(child_order)


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
        elif task_id.startswith("root_") and task_id.count("_") == 1:
            yield TextChunkEvent(
                json.dumps(
                    {
                        "action": "spawn_children",
                        "children": [
                            _budget_child(task_id, "first", 8),
                            _budget_child(task_id, "second", 8),
                        ],
                    }
                )
            )
        elif (
            task_id.startswith("root_")
            and task_id.count("_") == 2
            and round_number == 9
        ):
            yield TextChunkEvent(
                json.dumps(
                    {
                        "action": "complete",
                        "evidence_ids": [f"{task_id}:query:1"],
                    }
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


def _budget_child(root_id: str, suffix: str, requirements: int) -> dict[str, object]:
    task_id = f"{root_id}_{suffix}"
    requirement_ids = [f"{task_id}_query_{number}" for number in range(requirements)]
    return {
        "task_id": task_id,
        "objective": "find dependent records",
        "depends_on": [root_id],
        "requirements": [
            {
                "requirement_id": requirement_id,
                "kind": "query",
                "description": "records",
                "operation": None,
            }
            for requirement_id in requirement_ids
        ],
        "completion_criteria": requirement_ids,
        "routing_features": ["has_dependency", "multiple_queries"],
    }


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
    """Three planner and 77 executor requests fill the six-root ledger."""

    resolved = _budget_task("resolved", 1)
    unfinished = tuple(_budget_task(f"root_{number}", 1) for number in range(5))
    _BudgetProbeProvider.rounds_by_task = {}
    _BudgetProbeProvider.resolved_task_id = "resolved"
    planner_calls = 0

    async def planner(*args: object) -> PlanningResult:
        nonlocal planner_calls
        budget = cast(Any, args[3])
        for _ in range(3):
            budget.consume_global()
            planner_calls += 1
        return PlanningResult(
            ValidatedPlan("read", (resolved, *unfinished)), TokenUsage()
        )

    prepared = asyncio.run(
        prepare_planned_turn(
            "show parts",
            [],
            _budget_settings(),
            {},
            planner=planner,
            resolver=cast(Any, _StableExactResolver()),
        )
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
    result = prepared.result
    assert prepared.budget.global_limit == 80
    assert prepared.budget.global_count == 80, (
        result.statuses,
        result.reasons,
        _BudgetProbeProvider.rounds_by_task,
    )
    assert planner_calls == 3
    assert sum(_BudgetProbeProvider.rounds_by_task.values()) == 77
    assert result.statuses["resolved"] == "resolved"
    assert result.evidence.for_task("resolved")
    assert {result.statuses[task.task_id] for task in unfinished} == {
        "budget_exhausted"
    }


@pytest.mark.parametrize(
    ("plan", "expected_limit"),
    [
        (ValidatedPlan("read", (_task("task_1"),)), 18),
        (ValidatedPlan("mutation", ()), 5),
    ],
)
def test_three_planner_rounds_transfer_to_read_and_zero_root_mutation_ledgers(
    plan: ValidatedPlan, expected_limit: int
) -> None:
    """Changing the transfer ledger would reject a valid three-attempt plan."""

    provisional_limits: list[int] = []

    async def planner(*args: object) -> PlanningResult:
        budget = cast(Any, args[3])
        provisional_limits.append(budget.global_limit)
        for _ in range(3):
            budget.consume_global()
        return PlanningResult(plan, TokenUsage())

    prepared = asyncio.run(
        prepare_planned_turn(
            "show parts",
            [],
            _budget_settings(),
            {},
            planner=planner,
            resolver=cast(Any, _StableExactResolver()),
        )
    )

    assert provisional_limits == [5]
    assert prepared.budget.global_limit == expected_limit
    assert prepared.budget.global_count == 3


def test_planner_fourth_round_is_rejected_by_provisional_admission() -> None:
    """Removing the planner-attempt guard lets untransferable usage escape admission."""

    async def planner(*args: object) -> PlanningResult:
        budget = cast(Any, args[3])
        for _ in range(4):
            budget.consume_global()
        return PlanningResult(ValidatedPlan("mutation", ()), TokenUsage())

    with pytest.raises(RoundBudgetExhausted):
        asyncio.run(
            prepare_planned_turn(
                "show parts",
                [],
                _budget_settings(),
                {},
                planner=planner,
                resolver=cast(Any, _StableExactResolver()),
            )
        )


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
    allow_resolve: ClassVar[asyncio.Event | None] = None
    wake_scheduler: ClassVar[asyncio.Event | None] = None
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
        elif task_id == "task_3":
            wake_scheduler = type(self).wake_scheduler
            assert wake_scheduler is not None
            await wake_scheduler.wait()
            yield TextChunkEvent(
                json.dumps({"action": "block", "reason": "manager_unresolved"})
            )
            yield DoneEvent(TokenUsage())
        else:
            round_number = type(self).rounds_by_task.get(task_id, 0) + 1
            type(self).rounds_by_task[task_id] = round_number
            if round_number == 1:
                yield ToolCallEvent(
                    "query", "query", {"manager": "PartManager", "fields": []}
                )
            else:
                allow_resolve = type(self).allow_resolve
                assert allow_resolve is not None
                await allow_resolve.wait()
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


def _deadline_tasks() -> tuple[PlannedTask, PlannedTask, PlannedTask]:
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
            "task_3",
            "wake deadline check",
            (),
            (EvidenceRequirement("task_3_query", "query", "records", None),),
            ("task_3_query",),
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
    """The deadline expires only after the hanging provider has entered."""

    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", _deadline_tasks()),
        _deadline_settings(),
        user_text="parts",
    )

    async def run() -> _Runner:
        now = [0.0]
        _DeadlineProbeProvider.entered = asyncio.Event()
        _DeadlineProbeProvider.release = asyncio.Event()
        _DeadlineProbeProvider.allow_resolve = asyncio.Event()
        _DeadlineProbeProvider.wake_scheduler = asyncio.Event()
        _DeadlineProbeProvider.rounds_by_task = {}
        _DeadlineProbeProvider.cancelled = False
        runner = _Runner(
            prepared,
            {},
            None,
            [],
            SchedulerCallbacks(
                execute_tool=lambda *_args: {"status": "success", "data": []}
            ),
            10.0,
            lambda: now[0],
        )
        execution = asyncio.create_task(runner.run())
        entered = _DeadlineProbeProvider.entered
        allow_resolve = _DeadlineProbeProvider.allow_resolve
        wake_scheduler = _DeadlineProbeProvider.wake_scheduler
        assert (
            entered is not None
            and allow_resolve is not None
            and wake_scheduler is not None
        )
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        allow_resolve.set()

        async def wait_for_resolution() -> None:
            while runner.runtimes["task_1"].status != "resolved":
                await asyncio.sleep(0)

        try:
            await asyncio.wait_for(wait_for_resolution(), timeout=1.0)
        except TimeoutError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            pytest.fail("task_1 did not resolve after its provider was released")
        now[0] = 10.0
        wake_scheduler.set()
        try:
            await asyncio.wait_for(execution, timeout=1.0)
        except TimeoutError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            pytest.fail("scheduler did not finish after the evidence deadline")
        return runner

    runner = asyncio.run(run())
    result = runner.result()

    assert _DeadlineProbeProvider.cancelled is True
    assert result.coverage.resolved == 1
    assert result.reasons["task_2"] == "deadline_exceeded"


def test_closing_public_iterator_after_tool_call_cancels_in_flight_work() -> None:
    """Removing generator-close cleanup leaks provider and tool work after disconnect."""

    _DeadlineProbeProvider.entered = asyncio.Event()
    _DeadlineProbeProvider.release = asyncio.Event()
    _DeadlineProbeProvider.allow_resolve = asyncio.Event()
    _DeadlineProbeProvider.wake_scheduler = asyncio.Event()
    _DeadlineProbeProvider.rounds_by_task = {}
    _DeadlineProbeProvider.cancelled = False
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", _deadline_tasks()),
        _deadline_settings(),
        user_text="parts",
    )

    async def run() -> None:
        tool_cancelled = asyncio.Event()

        async def run_sync(
            _fn: Any, _args: tuple[Any, ...], _kwargs: dict[str, Any]
        ) -> Any:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                tool_cancelled.set()
                raise

        iterator = cast(
            Any,
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(
                    run_sync=run_sync, enforce_rate_limit=None
                ),
            ),
        )
        event = await asyncio.wait_for(iterator.__anext__(), timeout=0.2)
        assert event["type"] == "tool_call"
        entered = _DeadlineProbeProvider.entered
        assert entered is not None
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        await iterator.aclose()
        await iterator.aclose()

        assert tool_cancelled.is_set()
        assert _DeadlineProbeProvider.cancelled is True
        assert prepared.result is None

    asyncio.run(run())


def test_serialized_tool_at_deadline_skips_callback_and_commits_paired_failure() -> (
    None
):
    """Replacing the post-semaphore deadline guard would execute an expired tool."""

    now = [0.0]
    tool_calls: list[object] = []
    persisted: list[object] = []
    task = _task("task_1")
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", (task,)), _settings(), user_text="show parts"
    )
    runner = _Runner(
        prepared,
        {},
        object(),
        [],
        SchedulerCallbacks(
            execute_tool=lambda *_args: tool_calls.append(object()),
            emit_tool_called=lambda *_args, **_kwargs: tool_calls.append(object()),
            append_message=lambda *_args, **_kwargs: persisted.append(object()),
        ),
        10.0,
        lambda: now[0],
    )

    async def run() -> tuple[tuple[Any, bool], list[dict[str, Any]]]:
        await runner.tool_semaphore.acquire()
        execution = asyncio.create_task(
            runner.execute_tool(
                runner.runtimes[task.task_id],
                ToolCallEvent(
                    "late-query", "query", {"manager": "PartManager", "fields": []}
                ),
            )
        )
        first = await asyncio.wait_for(runner.events.get(), timeout=0.2)
        now[0] = 10.0
        runner.tool_semaphore.release()
        result = await asyncio.wait_for(execution, timeout=0.2)
        second = await asyncio.wait_for(runner.events.get(), timeout=0.2)
        return result, [first, second]

    result, events = asyncio.run(run())

    assert result == ({"status": "error", "code": "deadline_exceeded"}, False)
    assert [event["type"] for event in events] == ["tool_call", "tool_result"]
    assert events[1]["result"] == {"status": "error", "code": "deadline_exceeded"}
    assert tool_calls == []
    assert persisted == []
    assert not runner.evidence.for_task(task.task_id)
    runtime = runner.runtimes[task.task_id]
    assert (runtime.status, runtime.reason) == ("blocked", "deadline_exceeded")


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


PRIVATE_MARKERS = ("profile", "trust_group", "raw_plan", "Traceback", "catalog")
PRIVATE_SENTINELS = (
    "private-profile-sentinel-71e5",
    "private-trust-sentinel-82f6",
    "private-raw-plan-sentinel-93a7",
    "private-exception-sentinel-a4b8",
    "private-catalog-sentinel-b5c9",
    "private-routing-sentinel-c6da",
)


def _assert_public_event_schema(event: dict[str, Any]) -> None:
    event_type = event["type"]
    if event_type == "tool_call":
        assert set(event) == {"type", "task_id", "id", "name", "args"}
    elif event_type == "tool_result":
        assert set(event) == {"type", "task_id", "id", "name", "result"}
    elif event_type == "text_chunk":
        assert set(event) == {"type", "content"}
    elif event_type == "done":
        assert set(event) == {"type", "usage", "orchestration"}
        assert set(event["usage"]) == {"input_tokens", "output_tokens"}
        assert set(event["orchestration"]) == {
            "status",
            "coverage",
            "unresolved",
        }
        assert set(event["orchestration"]["coverage"]) == {"resolved", "total"}
        assert all(
            set(item) == {"task_id", "reason"}
            for item in event["orchestration"]["unresolved"]
        )
    elif event_type == "error":
        assert set(event) == {"type", "code", "message"}
    else:
        pytest.fail("unexpected planned public event type")


def _assert_public_terminal_matrix(events: list[dict[str, Any]]) -> None:
    payload = json.dumps(events)
    assert all(marker not in payload for marker in PRIVATE_MARKERS)
    assert all(sentinel not in payload for sentinel in PRIVATE_SENTINELS)
    assert len([event for event in events if event["type"] in {"done", "error"}]) == 1
    for event in events:
        _assert_public_event_schema(event)
        if event["type"] == "error":
            assert event["code"] in PLANNED_PUBLIC_MESSAGES
        if event["type"] == "done":
            assert event["orchestration"]["status"] in {"complete", "partial"}
            assert all(
                item["reason"] in PLANNED_PUBLIC_MESSAGES
                for item in event["orchestration"]["unresolved"]
            )


def _assert_tool_pairs_are_ordered(events: list[dict[str, Any]]) -> None:
    pending: dict[tuple[str, str], list[dict[str, Any]]] = {}
    call_count = 0
    result_count = 0
    for event in events:
        key = (event.get("task_id", ""), event.get("id", ""))
        if event["type"] == "tool_call":
            pending.setdefault(key, []).append(event)
            call_count += 1
        elif event["type"] == "tool_result":
            assert pending.get(key)
            call = pending[key].pop(0)
            assert event["task_id"] == call["task_id"]
            assert event["name"] == call["name"]
            result_count += 1
    assert call_count == result_count
    assert not any(pending.values())


class _MatrixDeadlineProvider:
    entered: ClassVar[asyncio.Event | None] = None
    first_ready: ClassVar[asyncio.Event | None] = None
    late_waiting: ClassVar[asyncio.Event | None] = None
    release: ClassVar[asyncio.Event | None] = None
    wake: ClassVar[asyncio.Event | None] = None
    rounds: ClassVar[dict[str, int]] = {}

    @classmethod
    def from_config(cls, _config: object) -> _MatrixDeadlineProvider:
        return cls()

    async def complete(
        self, messages: list[object], _tools: list[object]
    ) -> AsyncIterator[ToolCallEvent | TextChunkEvent | DoneEvent]:
        content = cast(Any, messages[-1]).content
        task_id = json.loads(content.removeprefix("REFERENCE_DATA="))["task"]["task_id"]
        entered = type(self).entered
        first_ready = type(self).first_ready
        late_waiting = type(self).late_waiting
        release = type(self).release
        wake = type(self).wake
        assert (
            entered is not None
            and first_ready is not None
            and late_waiting is not None
            and release is not None
            and wake is not None
        )
        round_number = type(self).rounds.get(task_id, 0) + 1
        type(self).rounds[task_id] = round_number
        if task_id == "deadline_first":
            if round_number == 1:
                yield ToolCallEvent(
                    "deadline-first-query",
                    "query",
                    {"manager": "PartManager", "fields": ["first"]},
                )
            else:
                await release.wait()
                first_ready.set()
                yield TextChunkEvent(
                    json.dumps(
                        {
                            "action": "complete",
                            "evidence_ids": ["deadline_first:query:1"],
                        }
                    )
                )
        elif task_id == "deadline_wait":
            entered.set()
            await release.wait()
            await asyncio.Future()
        else:
            late_waiting.set()
            await wake.wait()
            yield ToolCallEvent(
                "deadline-late-query",
                "query",
                {"manager": "PartManager", "fields": ["late"]},
            )
        yield DoneEvent(TokenUsage(input_tokens=1, output_tokens=1))


def _matrix_deadline_settings() -> PlannedChatSettings:
    executor = ProviderProfile(
        PRIVATE_SENTINELS[0],
        "tests.unit.test_chat_planned_scheduler._MatrixDeadlineProvider",
        MappingProxyType(
            {
                "role": "executor",
                "profile_diagnostic": PRIVATE_SENTINELS[0],
                "routing_diagnostic": PRIVATE_SENTINELS[5],
            }
        ),
        PRIVATE_SENTINELS[1],
    )
    synthesizer = ProviderProfile(
        f"{PRIVATE_SENTINELS[0]}-synth",
        "tests.unit.test_chat_planned_scheduler._Executor",
        MappingProxyType(
            {
                "role": "synthesizer",
                "profile_diagnostic": PRIVATE_SENTINELS[0],
                "routing_diagnostic": PRIVATE_SENTINELS[5],
            }
        ),
        PRIVATE_SENTINELS[1],
    )
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType({"executor": executor, "synthesizer": synthesizer}),
        roles=MappingProxyType(
            {
                "simple_executor": "executor",
                "complex_executor": "executor",
                "fallback_executor": "executor",
                "synthesizer": "synthesizer",
                "planner": "synthesizer",
            }
        ),
        catalog_source={"manager": PRIVATE_SENTINELS[4]},
        evidence_timeout_seconds=10.0,
    )


def _matrix_settings() -> PlannedChatSettings:
    profile = ProviderProfile(
        PRIVATE_SENTINELS[0],
        "tests.unit.test_chat_planned_scheduler._Executor",
        MappingProxyType(
            {
                "model": "test",
                "profile_diagnostic": PRIVATE_SENTINELS[0],
                "routing_diagnostic": PRIVATE_SENTINELS[5],
            }
        ),
        PRIVATE_SENTINELS[1],
    )
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType({PRIVATE_SENTINELS[0]: profile}),
        roles=MappingProxyType(
            {
                "simple_executor": PRIVATE_SENTINELS[0],
                "complex_executor": PRIVATE_SENTINELS[0],
                "fallback_executor": PRIVATE_SENTINELS[0],
                "synthesizer": PRIVATE_SENTINELS[0],
                "planner": PRIVATE_SENTINELS[0],
            }
        ),
        catalog_source={"manager": PRIVATE_SENTINELS[4]},
    )


def _matrix_tasks() -> tuple[PlannedTask, PlannedTask, PlannedTask]:
    return tuple(
        PlannedTask(
            task_id,
            f"find records {PRIVATE_SENTINELS[2]}",
            (),
            (EvidenceRequirement(f"{task_id}_query", "query", "records", None),),
            (f"{task_id}_query",),
            (),
        )
        for task_id in ("deadline_first", "deadline_late", "deadline_wait")
    )


async def _run_matrix_scenario(scenario: str) -> list[dict[str, Any]]:
    _Executor.responses = []
    _Executor.responses_by_task = {}
    _Executor.calls = []
    _Executor.roles = []
    if scenario == "complete":
        parent = replace(
            _dynamic_parent(),
            objective=f"find dependent records {PRIVATE_SENTINELS[2]}",
        )
        first_id, second_id = "task_1_child_1", "task_1_child_2"
        _Executor.responses = [
            {
                "answer": "Grounded dynamic result.",
                "evidence_ids": [
                    f"task_1:child:{first_id}:query:1",
                    f"task_1:child:{second_id}:query:1",
                ],
            }
        ]
        _Executor.responses_by_task = {
            "task_1": [
                {
                    "action": "spawn_children",
                    "children": [
                        _dynamic_child(first_id, depends_on=[parent.task_id]),
                        _dynamic_child(second_id, depends_on=[parent.task_id]),
                    ],
                },
                {
                    "action": "complete",
                    "evidence_ids": [
                        f"task_1:child:{first_id}:query:1",
                        f"task_1:child:{second_id}:query:1",
                    ],
                },
            ],
            first_id: [
                ToolCallEvent(
                    "first-query",
                    "query",
                    {"manager": "PartManager", "fields": ["first"]},
                ),
                {"action": "complete", "evidence_ids": [f"{first_id}:query:1"]},
            ],
            second_id: [
                ToolCallEvent(
                    "second-query",
                    "query",
                    {"manager": "PartManager", "fields": ["second"]},
                ),
                {"action": "complete", "evidence_ids": [f"{second_id}:query:1"]},
            ],
        }
        prepared = PreparedPlannedTurn.for_plan(
            ValidatedPlan("read", (parent,)),
            _matrix_settings(),
            user_text="show parts",
        )
        return await _collect(
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
    if scenario == "partial_budget":
        resolved = _task("resolved")
        budget = PlannedTask(
            "budget",
            f"find records and dependent records {PRIVATE_SENTINELS[2]}",
            (),
            (
                EvidenceRequirement("budget_first", "query", "records", None),
                EvidenceRequirement("budget_second", "query", "records", None),
            ),
            ("budget_first", "budget_second"),
            ("multiple_queries",),
        )
        first_id, second_id = "budget_first_child", "budget_second_child"

        def child_with_queries(task_id: str, count: int) -> dict[str, object]:
            requirement_ids = [f"{task_id}_query_{number}" for number in range(count)]
            return {
                "task_id": task_id,
                "objective": "find dependent records",
                "depends_on": [budget.task_id],
                "requirements": [
                    {
                        "requirement_id": requirement_id,
                        "kind": "query",
                        "description": "records",
                        "operation": None,
                    }
                    for requirement_id in requirement_ids
                ],
                "completion_criteria": requirement_ids,
                "routing_features": ["has_dependency", "multiple_queries"],
            }

        _Executor.responses = [
            {
                "answer": "One grounded root resolved.",
                "evidence_ids": ["resolved:query:1"],
            }
        ]
        _Executor.responses_by_task = {
            "resolved": [
                ToolCallEvent(
                    "resolved-query",
                    "query",
                    {"manager": "PartManager", "fields": ["resolved"]},
                ),
                {"action": "complete", "evidence_ids": ["resolved:query:1"]},
            ],
            "budget": [
                {
                    "action": "spawn_children",
                    "children": [child_with_queries(first_id, 8)],
                },
                {
                    "action": "spawn_children",
                    "children": [child_with_queries(second_id, 2)],
                },
            ],
            first_id: [
                *(
                    ToolCallEvent(
                        f"budget-first-query-{number}",
                        "query",
                        {"manager": "PartManager", "fields": [str(number)]},
                    )
                    for number in range(8)
                ),
                {
                    "action": "complete",
                    "evidence_ids": [
                        f"{first_id}:query:{number + 1}" for number in range(8)
                    ],
                },
            ],
            second_id: [
                *(
                    ToolCallEvent(
                        f"budget-second-query-{number}",
                        "query",
                        {"manager": "PartManager", "fields": [str(number)]},
                    )
                    for number in range(2)
                ),
                {
                    "action": "complete",
                    "evidence_ids": [
                        f"{second_id}:query:{number + 1}" for number in range(2)
                    ],
                },
            ],
        }
        prepared = PreparedPlannedTurn.for_plan(
            ValidatedPlan("read", (resolved, budget)),
            _matrix_settings(),
            user_text="show parts",
        )
        return await _collect(
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
    if scenario == "partial_deadline":
        _Executor.responses = [
            {
                "answer": "One grounded deadline result.",
                "evidence_ids": ["deadline_first:query:1"],
            }
        ]
        _MatrixDeadlineProvider.entered = asyncio.Event()
        _MatrixDeadlineProvider.first_ready = asyncio.Event()
        _MatrixDeadlineProvider.late_waiting = asyncio.Event()
        _MatrixDeadlineProvider.release = asyncio.Event()
        _MatrixDeadlineProvider.wake = asyncio.Event()
        _MatrixDeadlineProvider.rounds = {}
        now = [0.0]
        prepared = PreparedPlannedTurn.for_plan(
            ValidatedPlan("read", _matrix_tasks()),
            _matrix_deadline_settings(),
            user_text="show parts",
        )
        prepared.evidence_deadline = 10.0

        async def collect() -> list[dict[str, Any]]:
            late_tool_done = asyncio.Event()

            def execute_tool(
                _name: str, args: Mapping[str, Any], _context: object
            ) -> dict[str, Any]:
                if args.get("fields") == ["late"]:
                    now[0] = 10.0
                    late_tool_done.set()
                return {"status": "success", "data": []}

            async def run_sync(
                fn: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
            ) -> Any:
                return fn(*args, **kwargs)

            iterator = iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(
                    execute_tool=execute_tool,
                    run_sync=run_sync,
                ),
                clock=lambda: now[0],
            )
            result = asyncio.create_task(_collect(iterator))
            entered = _MatrixDeadlineProvider.entered
            first_ready = _MatrixDeadlineProvider.first_ready
            late_waiting = _MatrixDeadlineProvider.late_waiting
            release = _MatrixDeadlineProvider.release
            wake = _MatrixDeadlineProvider.wake
            assert (
                entered is not None
                and first_ready is not None
                and late_waiting is not None
                and release is not None
                and wake is not None
            )
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            await asyncio.wait_for(late_waiting.wait(), timeout=1.0)
            release.set()
            await asyncio.wait_for(first_ready.wait(), timeout=1.0)
            wake.set()
            await asyncio.wait_for(late_tool_done.wait(), timeout=1.0)
            return await result

        return await collect()
    if scenario == "no_evidence_provider":
        _Executor.responses = [_ProviderFailure(PRIVATE_SENTINELS[3])] * 4
        task = replace(_task("task_1"), objective=PRIVATE_SENTINELS[2])
        prepared = PreparedPlannedTurn.for_plan(
            ValidatedPlan("read", (task,)),
            _matrix_settings(),
            user_text="show parts",
        )
        return await _collect(
            iter_planned_read_events(
                prepared,
                scope={},
                conversation=None,
                messages=[],
                callbacks=SchedulerCallbacks(execute_tool=lambda *_args: {}),
            )
        )
    if scenario == "synthesis_failure":
        _Executor.responses = [
            {"answer": "Ungrounded executor prose.", "evidence_ids": ["missing"]},
            {"answer": "Still ungrounded.", "evidence_ids": ["missing"]},
        ]
        _Executor.responses_by_task = {
            "task_1": [
                ToolCallEvent(
                    "query",
                    "query",
                    {"manager": "PartManager", "fields": ["name"]},
                ),
                {"action": "complete", "evidence_ids": ["task_1:query:1"]},
            ]
        }
        task = replace(_task("task_1"), objective=PRIVATE_SENTINELS[2])
        prepared = PreparedPlannedTurn.for_plan(
            ValidatedPlan("read", (task,)),
            _matrix_settings(),
            user_text="show parts",
        )
        return await _collect(
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
    pytest.fail("unknown matrix scenario")


@pytest.mark.parametrize(
    ("scenario", "terminal", "status", "reason"),
    [
        ("complete", "done", "complete", None),
        ("partial_budget", "done", "partial", "budget_exhausted"),
        ("partial_deadline", "done", "partial", "deadline_exceeded"),
        ("no_evidence_provider", "error", None, "provider_failed"),
        ("synthesis_failure", "error", None, "synthesis_failed"),
    ],
)
def test_planned_terminal_event_matrix(
    scenario: str, terminal: str, status: str | None, reason: str | None
) -> None:
    events = asyncio.run(_run_matrix_scenario(scenario))
    _assert_public_terminal_matrix(events)
    terminals = [event for event in events if event["type"] in {"done", "error"}]
    assert len(terminals) == 1
    assert terminals[0]["type"] == terminal
    assert events[-1] is terminals[0]
    if status is not None:
        assert terminals[0]["orchestration"]["status"] == status
    if reason is not None:
        assert reason in json.dumps(terminals[0])
    if terminal == "done":
        _assert_tool_pairs_are_ordered(events)
        text_chunks = [event for event in events if event["type"] == "text_chunk"]
        assert len(text_chunks) == 1
        assert events[-2] is text_chunks[0]
        assert events[-2]["type"] == "text_chunk"
        assert "executor prose" not in text_chunks[0]["content"]
        if scenario == "complete":
            assert terminals[0]["orchestration"]["coverage"] == {
                "resolved": 1,
                "total": 1,
            }
            assert terminals[0]["usage"] == {
                "input_tokens": 7,
                "output_tokens": 7,
            }
        elif scenario == "partial_budget":
            assert terminals[0]["orchestration"]["coverage"] == {
                "resolved": 1,
                "total": 2,
            }
            assert terminals[0]["orchestration"]["unresolved"] == [
                {"task_id": "budget", "reason": "budget_exhausted"}
            ]
            assert terminals[0]["usage"] == {
                "input_tokens": 17,
                "output_tokens": 17,
            }
        else:
            assert terminals[0]["orchestration"]["coverage"] == {
                "resolved": 1,
                "total": 3,
            }
            assert all(
                item["reason"] == "deadline_exceeded"
                for item in terminals[0]["orchestration"]["unresolved"]
            )
            assert {
                item["task_id"] for item in terminals[0]["orchestration"]["unresolved"]
            } == {"deadline_late", "deadline_wait"}
            assert terminals[0]["usage"] == {
                "input_tokens": 4,
                "output_tokens": 4,
            }
    else:
        assert not any(event["type"] == "done" for event in events)
        assert not any(event["type"] == "text_chunk" for event in events)
        assert "Ungrounded" not in json.dumps(events)
