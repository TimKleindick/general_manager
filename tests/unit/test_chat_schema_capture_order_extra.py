"""Capture order survives delayed registration, failure and persistence callbacks."""

import asyncio
from dataclasses import replace
import pytest
from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import PlannedTask
from general_manager.chat.providers.base import ToolCallEvent
from tests.unit.test_chat_planned_tool_feedback import _runner
from tests.unit.test_chat_schema_requirements import A, B, record, requirement


@pytest.mark.parametrize("latest", [B, None])
def test_adding_a_historical_schema_record_never_changes_observation(latest):
    store = EvidenceStore()
    req = requirement("overview", [])
    if latest is None:
        store.invalidate_schema("new", "Part")
    else:
        store.observe_schema(
            "new", record("new", "overview", snapshot=latest).payload()
        )
    old = record("old", "overview", snapshot=A)
    store.add(old, requirement=req)
    assert store._schema_snapshots["Part"] == latest
    assert not store.schema_current(old)
    assert store.for_requirement("t", req) == ()


def test_adding_unobserved_schema_record_does_not_certify_current_snapshot():
    store = EvidenceStore()
    item = record("unobserved", "overview")
    store.add(item)
    assert not store.schema_current(item)
    store.observe_schema("t", item.payload())
    assert store.schema_current(item)


@pytest.mark.parametrize("first_result", ["failure", "hidden", "success"])
def test_three_tasks_keep_latest_capture_after_slow_post_tool_persistence(first_result):
    req = requirement("overview", [])
    tasks = [
        PlannedTask(name, "Inspect Part", (), (req,), ("s",), ())
        for name in ("first", "second", "third")
    ]
    counter = []
    final = "c" * 64

    def tool(*_):
        counter.append(None)
        if len(counter) == 1:
            if first_result == "failure":
                message = "invalid capture"
                raise RuntimeError(message)
            if first_result == "hidden":
                return None
            return record("old", "overview", snapshot=A).payload()
        return record(
            "later", "overview", snapshot=B if len(counter) == 2 else final
        ).payload()

    runner = _runner(tool, tasks=tasks)
    runner.conversation = object()

    async def scenario():
        first_at_persistence = asyncio.Event()
        release_first = asyncio.Event()
        paused = False

        def append(*_, **kwargs):
            return None

        async def run_sync(fn, args, kwargs):
            nonlocal paused
            if fn is append and not paused:
                paused = True
                first_at_persistence.set()
                await release_first.wait()
            return fn(*args, **kwargs)

        runner.callbacks = replace(
            runner.callbacks, run_sync=run_sync, append_message=append
        )
        old = asyncio.create_task(
            runner.execute_tool(
                runner.runtimes["first"],
                ToolCallEvent("old", "get_manager_schema", {"manager": "Part"}),
            )
        )
        await first_at_persistence.wait()
        for name in ("second", "third"):
            await runner.execute_tool(
                runner.runtimes[name],
                ToolCallEvent(name, "get_manager_schema", {"manager": "Part"}),
            )
        assert runner.evidence._schema_snapshots["Part"] == final
        release_first.set()
        await old
        assert runner.evidence._schema_snapshots["Part"] == final
        assert not runner.requirements_satisfied(runner.runtimes["first"])
        assert not runner.requirements_satisfied(runner.runtimes["second"])
        assert runner.requirements_satisfied(runner.runtimes["third"])

    asyncio.run(scenario())


def test_cancelled_capture_revokes_prior_proof_before_releasing_serialization():
    req = requirement("overview", [])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    pending = [record("ok", "overview").payload(), asyncio.CancelledError()]

    def tool(*_):
        value = pending.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    runner = _runner(tool, tasks=[task])
    runtime = runner.runtimes["t"]

    async def scenario():
        await runner.execute_tool(
            runtime, ToolCallEvent("ok", "get_manager_schema", {"manager": "Part"})
        )
        assert runner.requirements_satisfied(runtime)
        with pytest.raises(asyncio.CancelledError):
            await runner.execute_tool(
                runtime,
                ToolCallEvent("cancel", "get_manager_schema", {"manager": "Part"}),
            )
        assert not runner.requirements_satisfied(runtime)
        assert runner.evidence._schema_snapshots["Part"] is None

    asyncio.run(scenario())
