"""Grounded questions pause business work without inventing task completion."""

import asyncio
import json

import pytest

from general_manager.chat.planned.choice_context import ChoiceContext
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    canonical_call_identity,
)
from general_manager.chat.planned.events import planned_done_event
from general_manager.chat.planned.models import EvidenceRequirement, PlannedTask
from general_manager.chat.planned.scheduler import _parse_action_with_feedback
from general_manager.chat.planned.scheduler import (
    SchedulerCallbacks,
    iter_planned_read_events,
)
from general_manager.chat.planned.selector_clarification import render_selector
from general_manager.chat.providers.base import TokenUsage
from tests.unit.test_chat_planned_tool_feedback import _runner, _record_rounds


def setup(
    *,
    linked=True,
    owner="identity-work",
    complete=True,
    has_more=False,
    duplicate=False,
):
    task = PlannedTask(
        "identity-work",
        "Resolve the user-selected identity before related reads",
        (),
        (
            EvidenceRequirement(
                "identity", "query", "Read the matching identities", None
            ),
            EvidenceRequirement(
                "business",
                "query",
                "Read related business rows after choosing identity",
                None,
            ),
        ),
        ("identity", "business"),
        ("multiple_queries",),
    )
    runner = _runner(
        lambda *_: pytest.fail(
            "No dependent business read while awaiting a user choice"
        ),
        tasks=[task],
    )
    call = {
        "manager": "Organization",
        "fields": ["id", "code", "name"],
        "filters": {"name": "Same public name"},
        "limit": 100,
    }
    record = EvidenceRecord.create(
        "identity-query",
        owner,
        "query",
        canonical_call_identity("query", call),
        {"tool": "query", "manager": "Organization"},
        {
            "data": [
                {"id": 31, "code": "O31", "name": "Same public name"},
                {
                    "id": 32,
                    "code": "O31" if duplicate else "O32",
                    "name": "Same public name",
                },
            ],
            "complete": complete,
            "has_more": has_more,
            "total_count": 2,
        },
    )
    runner.evidence.add(record, requirement=task.requirements[0] if linked else None)
    return runner, runner.runtimes[task.task_id], record


def action(evidence_id="identity-query", field="code"):
    return {
        "action": "clarify_selector",
        "requirement_id": "identity",
        "language": "de",
        "selector": {"evidence_id": evidence_id, "field": field},
    }


def test_explicit_selector_action_is_admitted_with_a_closed_source_witness():
    value = action()
    parsed, feedback = _parse_action_with_feedback(json.dumps(value))
    assert parsed == value and feedback is None


@pytest.mark.parametrize(
    "extra",
    [
        {"options": ["invented"]},
        {"answer": "business claim"},
        {"evidence_ids": ["identity-query"]},
    ],
)
def test_selector_action_cannot_add_claims_or_model_written_options(extra):
    parsed, feedback = _parse_action_with_feedback(json.dumps({**action(), **extra}))
    assert parsed is None and feedback is not None


def test_grounded_selector_pauses_without_completing_business_requirements(monkeypatch):
    runner, runtime, record = setup()
    before = record.payload_json
    _record_rounds(monkeypatch, [action()])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.status == "awaiting_clarification"
    result = runner.result()
    assert result.coverage.resolved == 0 and result.coverage.total == 1
    assert result.coverage.unresolved == (("identity-work", "clarification_required"),)
    assert not runner.synthesis_evidence()
    assert runtime.selected_evidence_ids == ()
    assert record.payload_json == before
    assert (
        runner.evidence.for_requirement(
            runtime.task.task_id, runtime.task.requirements[1]
        )
        == ()
    )


def test_question_and_labels_are_derived_only_from_the_complete_query(monkeypatch):
    runner, runtime, record = setup()
    _record_rounds(monkeypatch, [action()])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    question = runner.result().clarification
    expected, _ = render_selector(
        {
            "language": "de",
            "requirements": ["record_selector"],
            "selector": action()["selector"],
        },
        [record],
    )
    assert question.answer == expected
    assert "O31" in question.answer and "O32" in question.answer
    assert question.evidence_ids == ("identity-query",)


def test_waiting_coverage_can_finish_with_a_question_and_no_false_completion():
    event = planned_done_event(
        TokenUsage(),
        resolved=0,
        total=1,
        unresolved=[("identity-work", "clarification_required")],
    )
    assert event["type"] == "done"
    assert event["orchestration"] == {
        "status": "partial",
        "coverage": {"resolved": 0, "total": 1},
        "unresolved": [
            {"task_id": "identity-work", "reason": "clarification_required"}
        ],
    }


@pytest.mark.parametrize(
    "change",
    [
        {"linked": False},
        {"linked": False, "owner": "foreign"},
        {"complete": False},
        {"has_more": True},
        {"duplicate": True},
    ],
)
def test_unlinked_foreign_partial_and_duplicate_sources_cannot_pause(
    monkeypatch, change
):
    runner, runtime, _ = setup(**change)
    _record_rounds(monkeypatch, [action()])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runtime.status == "running"
    assert runtime.action_validation_error["code"] == "invalid_selector_evidence"
    assert runner.result().clarification is None


def test_answered_same_scope_selector_cannot_be_asked_again(monkeypatch):
    runner, runtime, _ = setup()
    runner.prepared.choice_context = ChoiceContext(
        json.dumps({"clarification_requests": []})
    )
    _record_rounds(monkeypatch, [action()])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runtime.status == "running"
    assert (
        runtime.action_validation_error["code"]
        == "unrequested_or_answered_clarification"
    )


class _PersistenceFailure(Exception):
    pass


@pytest.mark.parametrize("failure", [_PersistenceFailure, asyncio.CancelledError])
def test_question_persistence_failure_never_emits_question_or_false_done(
    monkeypatch, failure
):
    runner, _, _ = setup()
    audits = []
    saved = []

    def append_message(*args, **kwargs):
        saved.append(kwargs)
        message = "PRIVATE PERSISTENCE DETAIL"
        raise failure(message)

    async def run_sync(fn, args, kwargs):
        return fn(*args, **kwargs)

    callbacks = SchedulerCallbacks(
        append_message=append_message,
        execute_tool=lambda *_: pytest.fail("No business read before the choice"),
        emit_tool_called=lambda **_: None,
        enforce_rate_limit=None,
        run_sync=run_sync,
    )
    runner.callbacks = callbacks
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler._Runner", lambda *_: runner
    )
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.emit_planned_audit_event",
        lambda kind, payload: audits.append((kind, payload)),
    )
    _record_rounds(monkeypatch, [action()])

    async def run():
        return [
            event
            async for event in iter_planned_read_events(
                runner.prepared,
                scope={},
                conversation=object(),
                messages=[],
                callbacks=callbacks,
                clock=lambda: 0.0,
            )
        ]

    if failure is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    else:
        events = asyncio.run(run())
        assert events[-1]["type"] == "error"
        assert events[-1]["code"] == "provider_failed"
        assert all(event["type"] not in ("text_chunk", "done") for event in events)
        terminal = [payload for kind, payload in audits if kind == "terminal"]
        assert terminal == [
            {
                "coverage": {"resolved": 0, "total": 1},
                "terminal_reason": "provider_failed",
                "reason_origin": "scheduler",
            }
        ]
        assert "PRIVATE PERSISTENCE DETAIL" not in json.dumps(events)
    assert len(saved) == 1
    assert saved[0]["tool_result"]["gm_clarification"]["version"] == 2
    assert runner.result().coverage.resolved == 0
