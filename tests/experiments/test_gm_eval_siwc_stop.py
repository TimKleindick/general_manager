"""The real GM/SIWC factory shares terminal failure across fresh role providers."""

from __future__ import annotations

import asyncio
import json

import pytest

from experiments.gm_eval.profiles import (
    ExperimentProvider,
    bind_provider_factory,
    make_profile,
    siwc_factory,
)
from experiments.gm_eval.trace import TraceRecorder
from experiments.siwc_eval.errors import EvalError
from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.config import get_planned_chat_settings
from general_manager.chat.planned.planner import InvalidPlanError, plan_request
from general_manager.chat.providers.base import Message


def _completed(text="read"):
    return {
        "type": "response.completed",
        "response": {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        },
    }


class CountingTransport:
    def __init__(self, failure=None, texts=None):
        self.failure = failure
        self.texts = iter(texts or [])
        self.starts = 0
        self.closed = 0
        self.bodies = []

    async def stream(self, body):
        self.starts += 1
        self.bodies.append(body)
        try:
            if self.starts == 1 and self.failure:
                if self.failure in {"http_401", "http_429", "network_error"}:
                    raise EvalError(self.failure)
                if self.failure == "missing_completion":
                    return
                yield {"type": self.failure, "response": {"status": "failed"}}
                return
            yield _completed(next(self.texts, "read"))
        finally:
            self.closed += 1


async def _collect(provider, text="show parts"):
    return [event async for event in provider.complete([Message("user", text)], [])]


@pytest.mark.parametrize(
    "failure",
    [
        "http_401",
        "http_429",
        "network_error",
        "response.failed",
        "response.incomplete",
        "error",
        "missing_completion",
    ],
)
def test_terminal_failure_survives_roles_turns_and_following_case_bindings(failure):
    transport = CountingTransport(failure)
    factory = siwc_factory(transport)
    failures = []
    traces = []

    async def run():
        for _case in range(2):
            trace = TraceRecorder()
            traces.append(trace)
            with bind_provider_factory(factory, trace):
                for turn in range(1, 3):
                    trace.turn = turn
                    for role in ("planner", "executor", "synthesizer", "fallback"):
                        provider = ExperimentProvider.from_config(
                            {"model": "offline", "role": role}
                        )
                        try:
                            await _collect(provider)
                        except EvalError as error:
                            failures.append(error)

    asyncio.run(run())
    assert transport.starts == transport.closed == 1

    assert len(failures) == 16
    assert all(error is failures[0] for error in failures)
    calls = [call for trace in traces for call in trace.calls]
    assert [call["request_count"] for call in calls] == [1] + [0] * 15
    assert all(call["error_code"] == str(failures[0]) for call in calls)


def test_reparable_model_output_can_use_native_planner_correction(settings):
    from tests.unit.test_chat_planned_planner import _plan

    settings.GENERAL_MANAGER = {
        "CHAT": make_profile("strong-only", strong_model="offline").chat_settings()
    }
    transport = CountingTransport(texts=["not valid JSON", json.dumps(_plan())])
    trace = TraceRecorder()

    async def run():
        with bind_provider_factory(siwc_factory(transport), trace):
            return await plan_request(
                "show parts",
                [],
                get_planned_chat_settings(),
                RoundBudget(()),
                {},
                configured_context="",
            )

    result = asyncio.run(run())
    assert result.plan.tasks[0].task_id == "task_1"
    assert transport.starts == transport.closed == 2
    assert [call["request_count"] for call in trace.calls] == [1, 1]


def test_terminal_stop_survives_actual_harness_cases_and_turns():
    from tests.experiments.test_gm_eval_harness import _child

    result = _child(r"""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=5)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile,siwc_factory
from experiments.siwc_eval.errors import EvalError
class OfflineTransport:
 def __init__(self): self.starts=0; self.closed=0
 async def stream(self,body):
  self.starts+=1
  try:
   raise EvalError("http_429")
   yield {}
  finally: self.closed+=1
async def run():
 transport=OfflineTransport()
 factory=siwc_factory(transport)
 factory.offline_only=True
 reports=[]
 for case_id in ("terminal-first", "terminal-following"):
  reports.append(await run_case(runtime,{"id":case_id,"turns":["List active customers","List them again"]},make_profile("strong-only"),factory))
 return {"starts":transport.starts,"closed":transport.closed,"turns":[[turn["terminal"]["type"] for turn in report["turns"]] for report in reports],"measured":[report["model_performance_measured"] for report in reports]}
try: print(json.dumps(asyncio.run(run())))
finally: runtime.close()
""")
    assert result["starts"] == result["closed"] == 1
    assert result["measured"] == [False, False]
    assert all(
        terminals and set(terminals) == {"error"} for terminals in result["turns"]
    )


def test_provider_created_before_terminal_failure_cannot_start_later():
    transport = CountingTransport("http_429")
    factory = siwc_factory(transport)
    first = factory({"model": "offline", "role": "planner"})
    waiting = factory({"model": "offline", "role": "executor"})

    async def run():
        with pytest.raises(EvalError) as failed:
            await _collect(first)
        with pytest.raises(EvalError) as blocked:
            await _collect(waiting)
        assert failed.value is blocked.value

    asyncio.run(run())
    assert transport.starts == transport.closed == 1
    assert waiting.requests == 0


def test_input_guard_stops_factory_before_any_transport_start():
    transport = CountingTransport()
    factory = siwc_factory(transport)

    async def run():
        with pytest.raises(EvalError, match="input_budget_exhausted") as first:
            await _collect(factory({"model": "offline"}), "x" * 200_001)
        with pytest.raises(EvalError) as later:
            await _collect(factory({"model": "offline"}))
        assert later.value is first.value

    asyncio.run(run())
    assert transport.starts == 0


def test_native_planner_retries_do_not_restart_terminal_transport(settings):
    settings.GENERAL_MANAGER = {
        "CHAT": make_profile("strong-only", strong_model="offline").chat_settings()
    }
    transport = CountingTransport("http_401")
    trace = TraceRecorder()

    async def run():
        with bind_provider_factory(siwc_factory(transport), trace):
            for _turn in range(2):
                with pytest.raises(InvalidPlanError):
                    await plan_request(
                        "show parts",
                        [],
                        get_planned_chat_settings(),
                        RoundBudget(()),
                        {},
                        configured_context="",
                    )

    asyncio.run(run())
    assert transport.starts == transport.closed == 1
    assert [call["request_count"] for call in trace.calls] == [1, 0, 0, 0, 0, 0]
    assert all(call["error_code"] == "http_401" for call in trace.calls)


def test_successful_provider_close_does_not_stop_other_roles():
    transport = CountingTransport()
    factory = siwc_factory(transport)

    async def run():
        for role in ("planner", "executor", "synthesizer", "fallback"):
            await _collect(factory({"model": "offline", "role": role}))

    asyncio.run(run())
    assert transport.starts == transport.closed == 4
    assert all(len(body["input"]) == 1 for body in transport.bodies)


def test_cancellation_latches_factory_and_closes_transport():
    started = asyncio.Event()

    class PendingTransport:
        starts = 0
        closed = 0

        async def stream(self, body):
            self.starts += 1
            try:
                started.set()
                await asyncio.Future()
                yield _completed()  # pragma: no cover
            finally:
                self.closed += 1

    transport = PendingTransport()
    factory = siwc_factory(transport)

    async def run():
        waiting = factory({"model": "offline", "role": "executor"})
        task = asyncio.create_task(_collect(factory({"model": "offline"})))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(_collect(waiting), timeout=0.2)

    asyncio.run(run())
    assert transport.starts == transport.closed == 1
