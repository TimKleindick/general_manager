"""Completed task intent is internal context, never user authority or an answer."""

import asyncio
from dataclasses import replace
import json

from general_manager.chat.planned import synthesis
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from general_manager.chat.planned.models import EvidenceRequirement
from general_manager.chat.providers.base import Message
from tests.unit.test_chat_planned_scheduler import _task


def context_fixture():
    task = replace(
        _task("t1"),
        objective="Planner assumption: rank all projects",
        requirements=(
            EvidenceRequirement(
                "named-like-other", "query", "Read full chosen population", None
            ),
        ),
        completion_criteria=("named-like-other",),
    )
    store = EvidenceStore()
    selected = store.add(
        EvidenceRecord.create(
            "arbitrary-id", "t1", "query", "query", {}, {"data": [1]}
        ),
        requirement=task.requirements[0],
    )
    store.add(
        EvidenceRecord.create(
            "t1:named-like-other:looks-linked",
            "t1",
            "query",
            "query",
            {},
            {"data": [2]},
        )
    )
    store.add(
        EvidenceRecord.create(
            "linked-but-unselected", "t1", "query", "query", {}, {"data": [3]}
        ),
        requirement=task.requirements[0],
    )
    return task, store, selected


def test_task_context_uses_real_requirement_links_intersected_with_selection():
    task, store, selected = context_fixture()
    assert hasattr(synthesis, "build_task_context"), (
        "runtime synthesis task context is absent"
    )
    context = synthesis.build_task_context(task, store, (selected,))
    messages = synthesis._messages("rank", (selected,), {}, task_context=(context,))
    ref = json.loads(messages[-1].content.split("=", 1)[1])
    data = ref["completed_task_context"]["tasks"][0]
    assert data["task_id"] == "t1"
    assert data["objective"] == task.objective
    assert data["completion_criteria"] == ["named-like-other"]
    assert data["requirements"][0]["description"] == "Read full chosen population"
    assert data["requirements"][0]["selected_evidence_ids"] == ["arbitrary-id"]
    assert "looks-linked" not in json.dumps(data)
    assert "linked-but-unselected" not in json.dumps(data)


def test_multiple_tasks_and_synthesis_eligibility_keep_bindings_separate():
    task, store, selected = context_fixture()
    assert hasattr(synthesis, "build_task_context")
    second = replace(task, task_id="t2", objective="Different assumption")
    other = store.add(
        EvidenceRecord.create("second-id", "t2", "query", "query", {}, {"data": [4]}),
        requirement=second.requirements[0],
    )
    contexts = (
        synthesis.build_task_context(task, store, (selected, other)),
        synthesis.build_task_context(second, store, (selected, other)),
    )
    full = json.loads(
        synthesis._messages("rank", (selected, other), {}, task_context=contexts)[
            -1
        ].content.split("=", 1)[1]
    )
    assert [
        r["requirements"][0]["selected_evidence_ids"]
        for r in full["completed_task_context"]["tasks"]
    ] == [["arbitrary-id"], ["second-id"]]
    partial = json.loads(
        synthesis._messages("rank", (selected,), {}, task_context=contexts)[
            -1
        ].content.split("=", 1)[1]
    )
    assert [r["task_id"] for r in partial["completed_task_context"]["tasks"]] == ["t1"]


def test_missing_and_conflicting_history_do_not_create_user_decision_state():
    task, store, selected = context_fixture()
    assert hasattr(synthesis, "build_task_context")
    context = synthesis.build_task_context(task, store, (selected,))
    for history in [
        [],
        [
            Message("user", "All projects"),
            Message("assistant", "Only approved"),
            Message("user", "I may mean active ones"),
        ],
    ]:
        messages = synthesis._messages(
            "rank",
            (selected,),
            {"resolved": 1, "total": 1},
            "App definitions",
            history,
            task_context=(context,),
        )
        ref = json.loads(messages[-1].content.split("=", 1)[1])
        assert ref["conversation_context"] == [
            {"role": m.role, "content": m.content} for m in history
        ]
        assert ref["configured_context"]["text"] == "App definitions"
        assert "decision_context" not in ref
        assert "unresolved_required" not in json.dumps(ref)
        assert task.objective not in messages[-2].content
        assert (
            ref["completed_task_context"]["authority"]
            == "planner_intent_not_user_consent"
        )


def test_task_context_does_not_add_a_clarification_gate_or_rewrite_answer():
    task, store, selected = context_fixture()
    assert hasattr(synthesis, "build_task_context")
    context = synthesis.build_task_context(task, store, (selected,))
    from tests.unit.test_chat_planned_synthesis import _SynthesisProvider, _settings
    from general_manager.chat.planned.budget import RoundBudget

    outputs = [
        (
            {"clarification": {"language": "en", "requirements": ["population"]}},
            "Which records should I include?",
        ),
        (
            {
                "answer": "Which population? The observed count is one.",
                "evidence_ids": ["arbitrary-id"],
            },
            "Which population? The observed count is one.",
        ),
    ]
    for output, expected in outputs:
        _SynthesisProvider.responses = [json.dumps(output)]
        result = asyncio.run(
            synthesis.synthesize_answer(
                "rank",
                (selected,),
                {},
                _settings(),
                RoundBudget(()),
                task_context=(context,),
            )
        )
        assert result.answer == expected
        assert "Planner assumption" not in result.answer
        assert "completion_criteria" not in result.answer


def test_scheduler_context_includes_only_resolved_roots_and_selected_runtime_links():
    from tests.unit.test_chat_planned_tool_feedback import _runner

    task, store, selected = context_fixture()
    blocked = replace(task, task_id="blocked")
    child = replace(task, task_id="child", parent_id="t1")
    runner = _runner(lambda *_: {}, tasks=[task, blocked, child])
    runner.evidence = store
    runner.runtimes["t1"].status = "resolved"
    runner.runtimes["t1"].selected_evidence_ids = (selected.evidence_id,)
    runner.runtimes["blocked"].status = "blocked"
    runner.runtimes["child"].status = "resolved"
    assert hasattr(runner, "synthesis_task_context"), (
        "scheduler does not pass runtime task context"
    )
    contexts = runner.synthesis_task_context()
    assert [context.task.task_id for context in contexts] == ["t1"]
    assert contexts[0].requirement_evidence == (
        ("named-like-other", ("arbitrary-id",)),
    )


def test_scheduler_passes_context_internally_without_appending_it_to_public_answer(
    monkeypatch,
):
    from general_manager.chat.planned.scheduler import (
        PreparedPlannedTurn,
        SchedulerCallbacks,
        iter_planned_read_events,
    )
    from general_manager.chat.planned.models import ValidatedPlan
    from general_manager.chat.providers.base import TokenUsage
    from general_manager.chat.views import _answer_from_events, _encode_sse_event
    from tests.unit.test_chat_planned_scheduler import (
        _resolved_executor_responses,
        _settings,
        _collect,
    )

    _resolved_executor_responses()
    marker = "private-planner-context-DO-NOT-APPEND"
    task = replace(_task("task_1"), objective=marker)
    captured = []

    async def fake_synthesis(*args, **kwargs):
        captured.extend(kwargs.get("task_context", ()))
        return synthesis.SynthesisResult(
            "Only the model answer.", ("task_1:query:1",), TokenUsage()
        )

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.synthesize_answer", fake_synthesis
    )
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
                    execute_tool=lambda *_: {"status": "success", "data": []}
                ),
            )
        )
    )
    assert captured and captured[0].task.objective == marker
    assert [event for event in events if event["type"] == "text_chunk"] == [
        {"type": "text_chunk", "content": "Only the model answer."}
    ]
    assert _answer_from_events(events) == "Only the model answer."
    public_text = "".join(_encode_sse_event(event).decode() for event in events)
    assert marker not in public_text
    assert "completed_task_context" not in public_text
    assert "selected_evidence_ids" not in public_text


def test_context_snapshot_does_not_mutate_links_or_reinterpret_query_scope():
    task, store, selected = context_fixture()
    context = synthesis.build_task_context(task, store, (selected,))
    before = store.for_requirement(task.task_id, task.requirements[0])
    forged = replace(selected, payload_json=json.dumps({"data": ["foreign"]}))
    forged_context = synthesis.build_task_context(task, store, (forged,))
    assert forged_context.requirement_evidence == (("named-like-other", ()),)
    ref = json.loads(
        synthesis._messages("rank", (selected,), {}, task_context=(context,))[
            -1
        ].content.split("=", 1)[1]
    )
    data = ref["resolved_evidence"][0]
    assert data["call_identity"] == selected.call_identity
    assert data["payload"] == selected.payload()
    assert store.for_requirement(task.task_id, task.requirements[0]) == before
    assert context.task is task
