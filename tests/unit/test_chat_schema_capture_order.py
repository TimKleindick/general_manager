"""Independent v27 concurrency repro; shared frozen sources remain unchanged."""

import asyncio
from dataclasses import replace
import json

import pytest

from general_manager.chat.planned.models import PlannedTask
from general_manager.chat.providers.base import ToolCallEvent
from tests.unit.test_chat_planned_tool_feedback import _runner
from tests.unit.test_chat_schema_requirements import A, B, record, requirement


@pytest.mark.parametrize("later_result", ["new_snapshot", "failed_capture"])
def test_delayed_old_capture_must_not_revoke_later_observation(later_result):
    req = requirement("overview", [])
    tasks = [
        PlannedTask(name, "Inspect Part", (), (req,), ("s",), ())
        for name in ("first", "second")
    ]
    captures = []

    def tool(*_):
        captures.append(len(captures) + 1)
        if len(captures) == 1:
            return record("initial", "overview", snapshot=A).payload()
        if later_result == "failed_capture":
            message = "capture failed after a changed runtime"
            raise RuntimeError(message)
        return record("changed", "overview", snapshot=B).payload()

    runner = _runner(tool, tasks=tasks)

    async def scenario():
        first_captured = asyncio.Event()
        release_first = asyncio.Event()

        async def run_sync(fn, args, kwargs):
            # Existing integration seam; allow a slow post-tool signal/persistence
            # to yield while the next task uses the released tool semaphore.
            if (
                fn is runner.callbacks.emit_tool_called
                and kwargs["result"].get("snapshot") == A
            ):
                first_captured.set()
                await release_first.wait()
            return fn(*args, **kwargs)

        runner.callbacks = replace(runner.callbacks, run_sync=run_sync)
        old = asyncio.create_task(
            runner.execute_tool(
                runner.runtimes["first"],
                ToolCallEvent("old", "get_manager_schema", {"manager": "Part"}),
            )
        )
        await first_captured.wait()
        await runner.execute_tool(
            runner.runtimes["second"],
            ToolCallEvent("new", "get_manager_schema", {"manager": "Part"}),
        )
        state_after_later = dict(runner.evidence._schema_snapshots)
        release_first.set()
        await old
        print(
            json.dumps(
                {
                    "later_result": later_result,
                    "captures": captures,
                    "state_after_later": state_after_later,
                    "final_snapshots": runner.evidence._schema_snapshots,
                    "old_requirement_satisfied": runner.requirements_satisfied(
                        runner.runtimes["first"]
                    ),
                    "new_requirement_satisfied": runner.requirements_satisfied(
                        runner.runtimes["second"]
                    ),
                }
            )
        )
        assert not runner.requirements_satisfied(runner.runtimes["first"]), (
            "a delayed earlier capture was revived as current"
        )
        assert runner.evidence._schema_snapshots["Part"] == (
            B if later_result == "new_snapshot" else None
        )

    asyncio.run(scenario())


def test_standard_sync_to_async_serialization_order_for_200_concurrent_pairs():
    """Document default-seam scope separately from the deterministic race."""

    async def scenario():
        for index in range(200):
            req = requirement("overview", [])
            tasks = [
                PlannedTask(name, "Inspect Part", (), (req,), ("s",), ())
                for name in ("first", "second")
            ]
            counter = []

            def tool(*_, counter=counter, index=index):
                counter.append(None)
                return record(
                    str(index), "overview", snapshot=A if len(counter) == 1 else B
                ).payload()

            runner = _runner(tool, tasks=tasks)
            await asyncio.gather(
                *[
                    runner.execute_tool(
                        runner.runtimes[name],
                        ToolCallEvent(name, "get_manager_schema", {"manager": "Part"}),
                    )
                    for name in ("first", "second")
                ]
            )
            assert runner.evidence._schema_snapshots["Part"] == B
            assert not runner.requirements_satisfied(runner.runtimes["first"])
            assert runner.requirements_satisfied(runner.runtimes["second"])
        print("STANDARD_SEAM: 200 concurrent pairs retain capture order")

    asyncio.run(scenario())
