"""Diagnosed executor errors stop without a generic no-progress round cap."""

from __future__ import annotations

import asyncio
import json

import pytest

from general_manager.chat.providers.base import (
    ChatEvent,
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from tests.unit.test_chat_planned_multi_tool import (
    _BatchProvider,
    _configured_runner,
    _query,
)


def _text(payload: object) -> list[ChatEvent]:
    return [TextChunkEvent(json.dumps(payload)), DoneEvent(TokenUsage(2, 1))]


def _complete() -> list[ChatEvent]:
    return _text({"action": "complete", "evidence_ids": ["root:query:1"]})


def _invalid_actions() -> list[object]:
    return [
        {"action": "invented"},
        {"action": "complete", "evidence_ids": "wrong-type"},
        {"action": "calculate", "operands": []},
        {"action": "spawn_children", "children": "wrong-type"},
    ]


@pytest.mark.parametrize("empty", [False, True])
def test_repeated_invalid_output_stops_after_the_same_feedback_is_ignored(
    monkeypatch: pytest.MonkeyPatch, empty: bool
) -> None:
    rejected = (
        [DoneEvent(TokenUsage(2, 1))]
        if empty
        else _text({"action": "complete", "evidence_ids": ["invented"]})
    )
    provider = _BatchProvider([rejected, rejected])
    runner = _configured_runner(monkeypatch, provider, lambda *_: pytest.fail("tool"))
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))

    assert runtime.status == "blocked" and runtime.reason == "provider_failed"
    assert len(provider.seen) == runner.prepared.budget.global_used == 2
    assert runner.usage == TokenUsage(4, 2)
    assert runtime.action_validation_error is not None
    assert runner.evidence.records == ()


def test_distinct_correctable_errors_have_no_fixed_attempt_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _BatchProvider(
        [
            *(_text(action) for action in _invalid_actions()),
            [_query("query"), DoneEvent(TokenUsage(2, 1))],
            _complete(),
        ]
    )
    runner = _configured_runner(monkeypatch, provider, lambda *_: {"data": []})
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))

    assert runtime.status == "resolved"
    assert len(provider.seen) == 6
    assert runner.prepared.budget.global_used == 6
    assert runner.usage == TokenUsage(12, 6)


def test_successful_operational_schema_allows_repair_to_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _text({"action": "invented"})
    provider = _BatchProvider(
        [
            bad,
            [
                ToolCallEvent(
                    "schema", "get_manager_schema", {"manager": "PartManager"}
                ),
                DoneEvent(TokenUsage(2, 1)),
            ],
            bad,
            [_query("query"), DoneEvent(TokenUsage(2, 1))],
            _complete(),
        ]
    )
    runner = _configured_runner(monkeypatch, provider, lambda *_: {"data": []})
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))

    assert runtime.status == "resolved"
    assert len(provider.seen) == 5
    assert [record.kind for record in runner.evidence.records] == ["query"]


def test_repeated_failed_tool_batch_stops_without_replaying_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _BatchProvider(
        [[_query(f"query-{index}"), DoneEvent(TokenUsage(2, 1))] for index in range(2)]
    )
    executed: list[str] = []

    def fail(name: str, *_: object) -> object:
        executed.append(name)
        return {"status": "error", "code": "invalid_field"}

    runner = _configured_runner(monkeypatch, provider, fail)
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))

    assert runtime.status == "blocked" and runtime.reason == "manager_unresolved"
    assert executed == ["query"]
    assert len(provider.seen) == 2
    assert (
        len([message for message in runtime.tool_history if message.role == "tool"])
        == 2
    )
    assert runner.evidence.records == ()


def test_error_cycle_is_detected_across_different_intervening_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors = _invalid_actions()
    provider = _BatchProvider([_text(errors[0]), _text(errors[1]), _text(errors[0])])
    runner = _configured_runner(monkeypatch, provider, lambda *_: pytest.fail("tool"))
    runtime = runner.runtimes["root"]
    asyncio.run(runner.run_task(runtime))

    assert runtime.status == "blocked" and runtime.reason == "provider_failed"
    assert len(provider.seen) == runner.prepared.budget.global_used == 3


def test_same_feedback_for_different_actions_is_not_a_cycle(monkeypatch):
    provider = _BatchProvider(
        [
            _text({"action": "complete", "evidence_ids": ["missing-one"]}),
            _text({"action": "complete", "evidence_ids": ["missing-two"]}),
            [_query("query"), DoneEvent(TokenUsage(2, 1))],
            _complete(),
        ]
    )
    runner = _configured_runner(monkeypatch, provider, lambda *_: {"data": []})
    asyncio.run(runner.run_task(runner.runtimes["root"]))
    assert runner.runtimes["root"].status == "resolved"


def test_repeated_identical_action_has_validation_cycle_origin(monkeypatch):
    bad = _text({"action": "complete", "evidence_ids": ["missing"]})
    runner = _configured_runner(
        monkeypatch, _BatchProvider([bad, bad]), lambda *_: None
    )
    asyncio.run(runner.run_task(runner.runtimes["root"]))
    assert runner.runtimes["root"].reason_origin == "scheduler_validation_cycle"
    proof = runner.runtimes["root"].validation_cycle
    assert proof["first_pass"] == 1 and proof["repeated_pass"] == 2
    assert all(
        len(proof[key]) == 64
        for key in ("action_sha256", "feedback_sha256", "evidence_sha256")
    )


@pytest.mark.parametrize(
    "change",
    ["missing_feedback", "forged_feedback", "task", "dependency", "evidence_payload"],
)
def test_repeated_action_requires_delivered_feedback_and_unchanged_input(
    monkeypatch, change
):
    from general_manager.chat.planned import scheduler
    from general_manager.chat.planned.schema_projection import reference_message

    original = scheduler._executor_messages
    calls = 0

    def messages(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        for index, message in enumerate(result):
            if message.content.startswith("REFERENCE_DATA="):
                reference = json.loads(message.content.removeprefix("REFERENCE_DATA="))
                if calls == 2:
                    if change == "missing_feedback":
                        reference.pop("action_validation_error", None)
                    elif change == "forged_feedback":
                        reference["action_validation_error"] = {"code": "unrelated"}
                    elif change == "task":
                        reference["task"]["objective"] += " clarified scope"
                    elif change == "dependency":
                        reference["dependency_evidence"] = [
                            {"evidence_id": "new", "payload": {"data": []}}
                        ]
                    else:
                        reference["task_evidence"] = [
                            {"evidence_id": "same-id", "payload": {"data": [1]}}
                        ]
                # Model a trusted producer changing context before attestation.
                result[index] = reference_message(
                    reference, message.schema_slots, reference_scope="executor"
                )
        return result

    monkeypatch.setattr(scheduler, "_executor_messages", messages)
    bad = _text({"action": "complete", "evidence_ids": ["missing"]})
    provider = _BatchProvider(
        [bad, bad, [_query("query"), DoneEvent(TokenUsage(2, 1))], _complete()]
    )
    runner = _configured_runner(monkeypatch, provider, lambda *_: {"data": []})
    asyncio.run(runner.run_task(runner.runtimes["root"]))
    assert runner.runtimes["root"].status == "resolved"
    assert len(provider.seen) == 4


def test_feedback_delivered_before_first_matching_action_is_not_cycle_proof(
    monkeypatch,
):
    from general_manager.chat.planned import scheduler
    from general_manager.chat.planned.schema_projection import reference_message

    original = scheduler._executor_messages
    calls = 0

    def messages(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 3:
            for index, message in enumerate(result):
                if message.content.startswith("REFERENCE_DATA="):
                    reference = json.loads(
                        message.content.removeprefix("REFERENCE_DATA=")
                    )
                    reference.pop("action_validation_error", None)
                    result[index] = reference_message(
                        reference, message.schema_slots, reference_scope="executor"
                    )
        return result

    monkeypatch.setattr(scheduler, "_executor_messages", messages)
    first = _text({"action": "complete", "evidence_ids": ["first-missing"]})
    second = _text({"action": "complete", "evidence_ids": ["second-missing"]})
    provider = _BatchProvider(
        [
            first,
            second,
            second,
            [_query("query"), DoneEvent(TokenUsage(2, 1))],
            _complete(),
        ]
    )
    runner = _configured_runner(monkeypatch, provider, lambda *_: {"data": []})
    asyncio.run(runner.run_task(runner.runtimes["root"]))
    assert runner.runtimes["root"].status == "resolved"
    assert len(provider.seen) == 5
