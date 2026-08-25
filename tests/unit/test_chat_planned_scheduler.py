"""Focused scheduler contracts for the planned chat execution boundary."""

from __future__ import annotations

import asyncio
import json
from types import MappingProxyType
from typing import Any, ClassVar

from general_manager.chat.planned.config import PlannedChatSettings, ProviderProfile
from general_manager.chat.planned.models import (
    EvidenceRequirement,
    PlannedTask,
    ValidatedPlan,
)
from general_manager.chat.planned.scheduler import (
    PreparedPlannedTurn,
    SchedulerCallbacks,
    iter_planned_read_events,
)
from general_manager.chat.providers.base import DoneEvent, TokenUsage, ToolCallEvent


class _Executor:
    responses: ClassVar[list[object]] = []
    responses_by_task: ClassVar[dict[str, list[object]]] = {}

    @classmethod
    def from_config(cls, _config: object) -> _Executor:
        return cls()

    async def complete(self, _messages: list[object], _tools: list[object]):
        content = _messages[-1].content
        reference = json.loads(
            content.removeprefix("REFERENCE_DATA=").removeprefix(
                "RESOLVED_REFERENCE_DATA="
            )
        )
        task_id = reference.get("task", {}).get("task_id", "synthesis")
        responses = type(self).responses_by_task.get(task_id, type(self).responses)
        response = responses.pop(0)
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
