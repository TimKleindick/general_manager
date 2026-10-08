"""A model-selected public code must not impersonate an execution failure."""

from __future__ import annotations

import asyncio
import json

import pytest

from general_manager.chat.audit import _sanitize_planned_audit_payload
from general_manager.chat.planned.scheduler import _parse_action
from tests.unit.test_chat_planned_tool_feedback import _record_rounds, _runner


@pytest.mark.parametrize(
    "reason", ["provider_failed", "synthesis_failed", "deadline_exceeded"]
)
def test_explicit_block_origin_is_owned_by_scheduler(monkeypatch, reason):
    _record_rounds(monkeypatch, [{"action": "block", "reason": reason}])
    audit = []
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.emit_planned_audit_event",
        lambda kind, payload: audit.append(
            (kind, _sanitize_planned_audit_payload(kind, payload))
        ),
    )
    runner = _runner(lambda *_: pytest.fail("block must not execute a tool"))
    runtime = runner.runtimes["task_1"]
    asyncio.run(runner.run_task(runtime))
    assert runtime.reason == reason
    assert runtime.reason_origin == "model_declared_block"
    assert runner.result().reason_origins["task_1"] == "model_declared_block"
    progress = [payload for kind, payload in audit if kind == "task_progress"]
    assert progress[-1]["terminal_reason"] == reason
    assert progress[-1]["reason_origin"] == "model_declared_block"


def test_provider_exception_has_distinct_framework_origin(monkeypatch):
    async def failed(*_args):
        detail = "private transport detail"
        raise RuntimeError(detail)

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.complete_provider_round", failed
    )
    runner = _runner(lambda *_: pytest.fail("provider exception precedes tools"))
    runtime = runner.runtimes["task_1"]
    asyncio.run(runner.run_task(runtime))
    assert runtime.reason == "provider_failed"
    assert runtime.reason_origin == "provider_exception"
    assert runner.result().reason_origins["task_1"] == "provider_exception"


def test_model_cannot_supply_its_own_reason_origin():
    assert (
        _parse_action(
            json.dumps(
                {
                    "action": "block",
                    "reason": "provider_failed",
                    "reason_origin": "provider_exception",
                }
            )
        )
        is None
    )


@pytest.mark.parametrize("event_type", ["task_progress", "terminal"])
def test_audit_accepts_only_fixed_reason_origins(event_type):
    assert _sanitize_planned_audit_payload(
        event_type, {"reason_origin": "model_declared_block"}
    ) == {"reason_origin": "model_declared_block"}
    assert (
        _sanitize_planned_audit_payload(
            event_type, {"reason_origin": "private arbitrary text"}
        )
        == {}
    )


@pytest.mark.parametrize("failure", ["model", "provider", "synthesizer"])
def test_terminal_origin_matches_actual_execution_stage(monkeypatch, failure):
    from tests.unit.test_chat_planned_scheduler import (
        _Executor,
        _ProviderFailure,
        _collect,
        _settings,
        _task,
    )
    from general_manager.chat.planned.scheduler import (
        PreparedPlannedTurn,
        SchedulerCallbacks,
        iter_planned_read_events,
    )
    from general_manager.chat.planned.models import ValidatedPlan
    from general_manager.chat.providers.base import ToolCallEvent

    if failure == "model":
        responses = [{"action": "block", "reason": "synthesis_failed"}]
    elif failure == "provider":
        responses = [_ProviderFailure("private detail")]
    else:
        responses = [
            ToolCallEvent(
                "query", "query", {"manager": "PartManager", "fields": ["name"]}
            ),
            {"action": "complete", "evidence_ids": ["task_1:query:1"]},
        ]
    _Executor.responses_by_task = {"task_1": responses}
    _Executor.responses = []
    audit = []
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.emit_planned_audit_event",
        lambda kind, payload: audit.append(
            (kind, _sanitize_planned_audit_payload(kind, payload))
        ),
    )

    async def fail_synthesis(*args, **kwargs):
        if failure != "synthesizer":
            pytest.fail("no resolved task should reach synthesis")
        detail = "private synthesis detail"
        raise RuntimeError(detail)

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.synthesize_answer", fail_synthesis
    )
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
                    execute_tool=lambda *_: {"status": "success", "data": []}
                ),
            )
        )
    )
    expected_code = "provider_failed" if failure == "provider" else "synthesis_failed"
    expected_origin = {
        "model": "model_declared_block",
        "provider": "provider_exception",
        "synthesizer": "synthesizer",
    }[failure]
    assert events[-1]["code"] == expected_code
    terminal = [payload for kind, payload in audit if kind == "terminal"][-1]
    assert terminal["terminal_reason"] == expected_code
    assert terminal["reason_origin"] == expected_origin
    assert "reason_origin" not in events[-1]
