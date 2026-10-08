"""Offline transport-watchdog and verbatim recording regressions."""

import asyncio
from types import SimpleNamespace
import json
import pytest
from experiments.gm_eval import harness
from experiments.gm_eval.trace import TraceRecorder, sanitize


def test_recording_preserves_ordinary_json_text_across_chunk_boundaries():
    chunks = ['{"quantity": 12}', " pieces; this is a quoted example."]
    answer = "".join(chunks)
    events = [{"type": "text_chunk", "content": chunk} for chunk in chunks] + [
        {"type": "done"}
    ]
    rows = [
        {"role": "user", "content": "read"},
        {"role": "assistant", "content": answer},
    ]
    result = harness._turn_record(1, "read", events, TraceRecorder(), rows, 0, 1)
    assert result["answer"] == answer
    assert (
        "".join(e["content"] for e in result["events"] if e["type"] == "text_chunk")
        == answer
    )
    assert result["persistence_verified"] is True
    assert sanitize('{ "quantity" : 12 }') == '{ "quantity" : 12 }'


def test_watchdog_covers_the_configured_evidence_and_synthesis_stages():
    settings = SimpleNamespace(
        evidence_timeout_seconds=90, synthesis_timeout_seconds=30
    )
    assert harness.consumer_watchdog_seconds(settings) >= 120


@pytest.mark.parametrize(
    "failure", [TimeoutError(), RuntimeError("closed"), asyncio.CancelledError()]
)
def test_consumer_failure_retains_partial_events_and_stable_origin(failure):
    class Communicator:
        def __init__(self):
            self.calls = 0
            self.timeouts = []

        async def receive_output(self, timeout):
            self.timeouts.append(timeout)
            self.calls += 1
            if self.calls == 1:
                return {
                    "type": "websocket.send",
                    "text": json.dumps({"type": "tool_call", "id": "q"}),
                }
            raise failure

    communicator = Communicator()
    events = []
    result = asyncio.run(harness._consume_events(communicator, events, 125))
    assert events[0] == {"type": "tool_call", "id": "q"}
    assert events[-1]["type"] == "error"
    assert result["origin"] == "harness"
    assert result["error_type"] == type(failure).__name__
    assert all(120 < value <= 125 for value in communicator.timeouts)
    turn = {
        "persistence_verified": True,
        "terminal": events[-1],
        "harness_failure": result,
    }
    assert "harness_failure" in harness._failure_flags([turn], TraceRecorder(), [])


def test_real_asgi_watchdog_keeps_partial_event_after_application_cancellation():
    from asgiref.testing import ApplicationCommunicator

    async def run():
        async def application(scope, receive, send):
            await send(
                {
                    "type": "websocket.send",
                    "text": json.dumps({"type": "tool_call", "id": "partial"}),
                }
            )
            await receive()

        communicator = ApplicationCommunicator(application, {"type": "websocket"})
        events = []
        result = await harness._consume_events(communicator, events, 0.02)
        return result, events

    result, events = asyncio.run(run())
    assert result["error_type"] == "TimeoutError"
    assert events == [
        {"type": "tool_call", "id": "partial"},
        {"type": "error", "code": "harness_failure"},
    ]


@pytest.mark.parametrize(
    "mode", ["watchdog", "external_cancel", "cleanup_external_cancel", "finished"]
)
def test_run_case_real_asgi_watchdog_and_external_cancellation(mode):
    from tests.experiments.test_gm_eval_harness import _child

    result = _child(
        "mode = "
        + repr(mode)
        + "\n"
        + r"""
import asyncio, json
from unittest.mock import patch
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval import harness
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import ScriptedFactory
runtime = bootstrap(manager_count=5)
from general_manager.chat.consumer import ChatConsumer
case = next(item for item in load_catalog() if item["id"] == "E001")
async def run():
    ready = asyncio.Event()
    stopped = asyncio.Event()
    async def application(scope, receive, send):
        try:
            assert (await receive())["type"] == "websocket.connect"
            await send({"type": "websocket.accept"})
            assert (await receive())["type"] == "websocket.receive"
            await send({"type": "websocket.send", "text": json.dumps({
                "type": "tool_call", "id": "partial", "task_id": "task_1",
                "name": "get_manager_schema", "args": {"manager": "Customer"}
            })})
            if mode != "cleanup_external_cancel":
                ready.set()
            if mode in {"finished", "cleanup_external_cancel"}:
                await send({"type": "websocket.send", "text": json.dumps({"type": "done"})})
            else:
                assert (await receive())["type"] == "websocket.disconnect"
            if mode == "cleanup_external_cancel":
                assert (await receive())["type"] == "websocket.disconnect"
                ready.set()
                await asyncio.Event().wait()
        finally:
            stopped.set()
    with patch.object(ChatConsumer, "as_asgi", return_value=application), patch.object(
        harness, "consumer_watchdog_seconds", return_value=0.01 if mode == "watchdog" else 125
    ):
        async def execute():
            try:
                report = await harness.run_case(runtime, case, make_profile("weak-only"), ScriptedFactory({}), route="consumer")
                return {"report": report, "escaped": False, "cancelling": asyncio.current_task().cancelling()}
            except asyncio.CancelledError:
                return {"escaped": True, "cancelling": asyncio.current_task().cancelling()}
        task = asyncio.create_task(execute())
        if mode in {"external_cancel", "cleanup_external_cancel"}:
            await ready.wait()
            task.cancel()
        result = await task
        result["app_stopped"] = stopped.is_set()
        return result
try:
    print(json.dumps(asyncio.run(run())))
finally:
    runtime.close()
"""
    )
    assert result["app_stopped"] is True
    if mode in {"external_cancel", "cleanup_external_cancel"}:
        assert result["escaped"] is True
        assert result["cancelling"] == 1
        return
    assert result["escaped"] is False
    assert result["cancelling"] == 0
    report = result["report"]
    assert report["trace"]["provider_calls"] == []
    turn = report["turns"][0]
    assert turn["events"][0]["id"] == "partial"
    if mode == "watchdog":
        assert report["status"] == "harness_failure"
        assert turn["harness_failure"] == {
            "origin": "harness",
            "code": "harness_failure",
            "error_type": "TimeoutError",
        }
        assert turn["terminal"]["code"] == "harness_failure"
    else:
        assert "harness_failure" not in turn
        assert turn["terminal"]["type"] == "done"


@pytest.mark.parametrize(
    ("error_type", "status", "code"),
    [
        ("RuntimeError", "harness_failure", "chat_error"),
        ("InvalidPlanError", "model_task_failure", "invalid_plan"),
        ("TimeoutError", "budget_exhausted", "deadline_exceeded"),
    ],
)
def test_run_case_scheduler_preprovider_failure_keeps_origin(error_type, status, code):
    from tests.experiments.test_gm_eval_harness import _child

    result = _child(
        "error_type = "
        + repr(error_type)
        + "\n"
        + r"""
import asyncio, json
from unittest.mock import patch
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import ScriptedFactory
runtime = bootstrap(manager_count=5)
from general_manager.chat.planned.planner import InvalidPlanError
errors = {"RuntimeError": RuntimeError("private detail"), "InvalidPlanError": InvalidPlanError(), "TimeoutError": TimeoutError()}
case = next(item for item in load_catalog() if item["id"] == "E001")
try:
    with patch("general_manager.chat.planned.scheduler.prepare_planned_turn", side_effect=errors[error_type]):
        result = asyncio.run(run_case(runtime, case, make_profile("weak-only"), ScriptedFactory({})))
    print(json.dumps(result))
finally:
    runtime.close()
"""
    )
    assert result["trace"]["provider_calls"] == []
    assert result["status"] == status
    turn = result["turns"][0]
    assert turn["persistence_verified"] is True
    assert turn["durable_messages"][0]["role"] == "user"
    assert turn["terminal"]["code"] == code
    assert turn["failure_flags"] == [status]
    assert "private detail" not in json.dumps(result)
    if error_type == "RuntimeError":
        assert turn["harness_failure"] == {
            "origin": "harness",
            "code": "harness_failure",
            "error_type": "RuntimeError",
        }
    else:
        assert "harness_failure" not in turn
