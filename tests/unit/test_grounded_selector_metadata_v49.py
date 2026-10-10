"""Source-bound questions survive history without becoming record authority."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from general_manager.chat.planned import choice_context as choices
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from general_manager.chat.planned.models import EvidenceRequirement, PlannedTask
from general_manager.chat.planned.selector_question import (
    combine_questions,
    selector_question,
    verify_selector_metadata,
)
from general_manager.chat.providers.base import Message
from tests.unit.test_grounded_selector_workflow_v49 import action, setup
from tests.unit.test_chat_planned_tool_feedback import _record_rounds
from tests.unit.test_chat_planned_tool_feedback import _runner
from tests.unit.test_chat_planned_scheduler import _dynamic_child


def question():
    runner, _, record = setup()
    value = {
        "language": "de",
        "requirements": ["record_selector"],
        "selector": action()["selector"],
    }
    return selector_question(runner.prepared.user_text, value, record)


def history():
    q = question()
    return [
        Message("user", "show parts"),
        Message("assistant", q.answer, clarification_metadata=q.as_metadata()),
        Message("user", "Use O31."),
    ]


def test_only_annotated_assistant_questions_receive_conversational_choice_binding():
    messages = history()
    qs = choices.choice_questions(messages)
    assert len(qs) == 1 and qs[0]["topic"] == "record_selector"
    assert qs[0]["question_index"] == 1 and qs[0]["scope_index"] == 0
    assert qs[0]["selector_source_sha256"] == verify_selector_metadata(
        messages[1].clarification_metadata, messages[1].content, messages[0].content
    )
    for role in ("user", "tool"):
        assert not choices.choice_questions(
            [messages[0], replace(messages[1], role=role), messages[2]]
        )
    assert not choices.choice_questions(
        [messages[0], replace(messages[1], clarification_metadata=None), messages[2]]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "version_float",
        "version_bool",
        "kind",
        "scope",
        "question_hash",
        "source_hash",
        "source_rows",
        "source_call",
        "source_manager",
        "source_kind",
        "source_extra",
        "selector_options",
        "entry_extra",
        "root_extra",
        "empty",
        "too_many",
        "text",
    ],
)
def test_altered_or_unsupported_persisted_question_metadata_fails_closed(mutation):
    messages = history()
    metadata = deepcopy(messages[1].clarification_metadata)
    value = metadata["gm_clarification"]
    entry = value["questions"][0]
    if mutation == "version_float":
        value["version"] = 2.0
    elif mutation == "version_bool":
        value["version"] = True
    elif mutation in ("kind", "scope", "question_hash"):
        value[
            {
                "kind": "kind",
                "scope": "scope_user_text",
                "question_hash": "question_sha256",
            }[mutation]
        ] = "foreign"
    elif mutation == "source_hash":
        entry["source_sha256"] = "foreign"
    elif mutation == "source_rows":
        entry["source"]["payload"]["data"].pop()
    elif mutation == "source_call":
        entry["source"]["call_identity"] = "query"
    elif mutation == "source_manager":
        entry["source"]["provenance"]["manager"] = "Other"
    elif mutation == "source_kind":
        entry["source"]["kind"] = "calculation"
    elif mutation == "source_extra":
        entry["source"]["extra"] = True
    elif mutation == "selector_options":
        entry["selector"]["options"] = ["Invented"]
    elif mutation == "entry_extra":
        entry["extra"] = True
    elif mutation == "root_extra":
        metadata["extra"] = True
    elif mutation == "empty":
        value["questions"] = []
    elif mutation == "too_many":
        value["questions"] *= 7
    else:
        messages[1] = replace(messages[1], content="Choose Invented.")
    messages[1] = replace(messages[1], clarification_metadata=metadata)
    with pytest.raises(choices.ChoiceValidationError):
        choices.choice_questions(messages)


def test_answered_choice_binds_user_quote_and_rejects_metadata_change_and_reask():
    messages = history()
    qs = choices.choice_questions(messages)
    envelope = {
        "plan": {},
        "choices": [
            {
                "question_id": qs[0]["question_id"],
                "state": "answered",
                "applicability": "same_scope",
                "user_quotes": [{"index": 2, "quote": "O31"}],
            }
        ],
        "clarification_requests": [],
    }
    context = choices.validate_choices(envelope, messages, user_text="Use O31.")
    context.verify_messages(messages)
    assert (
        context.as_mapping()["authority"] == "user_quote_bindings_not_record_evidence"
    )
    with pytest.raises(choices.ChoiceValidationError):
        context.check_clarification(("record_selector",))
    altered = deepcopy(messages[1].clarification_metadata)
    altered["gm_clarification"]["questions"][0]["source_sha256"] = "foreign"
    with pytest.raises(choices.ChoiceValidationError):
        context.verify_messages(
            [
                messages[0],
                replace(messages[1], clarification_metadata=altered),
                messages[2],
            ]
        )
    envelope["choices"][0]["user_quotes"] = [{"index": 1, "quote": "O31"}]
    with pytest.raises(choices.ChoiceValidationError):
        choices.validate_choices(envelope, messages)


@pytest.mark.parametrize(
    "change",
    [
        "count",
        "count_float",
        "rows",
        "field",
        "identity",
        "manager",
        "label_bool",
        "too_many",
    ],
)
def test_runtime_rejects_inconsistent_population_or_unobserved_identity(
    monkeypatch, change
):
    runner, runtime, record = setup()
    payload = record.payload()
    call = json.loads(record.call_identity)
    provenance = dict(record.provenance)
    if change == "count":
        payload["total_count"] = 3
    elif change == "count_float":
        payload["total_count"] = 2.0
    elif change == "rows":
        payload["data"].pop()
    elif change == "field":
        call["args"]["fields"] = ["id", "name"]
    elif change == "manager":
        provenance["manager"] = "Foreign"
    elif change == "label_bool":
        payload["data"][0]["code"] = True
    elif change == "too_many":
        payload["data"] = [{"id": i, "code": str(i)} for i in range(21)]
        payload["total_count"] = 21
    replacement = EvidenceRecord.create(
        record.evidence_id,
        record.task_id,
        record.kind,
        "query"
        if change == "identity"
        else json.dumps(call, sort_keys=True, separators=(",", ":")),
        provenance,
        payload,
    )
    runner.evidence = EvidenceStore()
    runner.evidence.add(replacement, requirement=runtime.task.requirements[0])
    _record_rounds(monkeypatch, [action()])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runner.result().clarification is None
    assert runtime.action_validation_error["code"] == "invalid_selector_evidence"


def test_accepted_question_cannot_overwrite_expired_evidence_deadline(monkeypatch):
    runner, runtime, _ = setup()
    _record_rounds(monkeypatch, [action()])
    runner.deadline = 0.0
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.status == "blocked" and runtime.reason == "deadline_exceeded"
    assert runner.result().clarification is None


def test_combined_questions_preserve_every_source_and_are_immutable_to_callers():
    first = question()
    combined = combine_questions((first, first), "show parts")
    before = combined.metadata_json
    metadata = combined.as_metadata()
    assert len(metadata["gm_clarification"]["questions"]) == 2
    assert verify_selector_metadata(metadata, combined.answer, "show parts")
    metadata["gm_clarification"]["questions"].pop()
    assert combined.metadata_json == before
    assert len(combined.as_metadata()["gm_clarification"]["questions"]) == 2


def test_dependent_root_does_not_read_or_complete_after_identity_question(monkeypatch):
    seeded, _, record = setup()
    root = seeded.prepared.plan.tasks[0]
    dependent = PlannedTask(
        "dependent-work",
        "Read business data after the identity choice",
        (root.task_id,),
        (EvidenceRequirement("business", "query", "Business rows", None),),
        ("business",),
        ("has_dependency",),
    )
    runner = _runner(
        lambda *_: pytest.fail("No dependent read"), tasks=[root, dependent]
    )
    runner.evidence.add(record, requirement=root.requirements[0])
    _record_rounds(monkeypatch, [action()])
    asyncio.run(runner.run())
    result = runner.result()
    assert result.statuses == {
        root.task_id: "awaiting_clarification",
        dependent.task_id: "awaiting_clarification",
    }
    assert result.coverage.resolved == 0 and result.coverage.total == 2
    assert result.clarification is not None
    assert result.clarification.evidence_ids == (record.evidence_id,)
    assert not runner.evidence.for_task(dependent.task_id)
    assert not runner.synthesis_evidence()


def test_child_question_pauses_owner_without_adopting_business_evidence(monkeypatch):
    seeded, _, record = setup()
    root = seeded.prepared.plan.tasks[0]
    child = _dynamic_child(
        "identity-child", depends_on=[root.task_id], target_requirement_id="identity"
    )
    runner = _runner(
        lambda *_: pytest.fail("No business read after the child question"),
        tasks=[root],
    )
    # The child query is a separate task-local source; parent adoption never
    # happens because the child's requirements are still awaiting a choice.
    child_record = EvidenceRecord.create(
        record.evidence_id,
        "identity-child",
        record.kind,
        record.call_identity,
        record.provenance,
        record.payload(),
    )
    runner.evidence.add(
        child_record,
        requirement=EvidenceRequirement("identity", "query", "records", None),
    )
    _record_rounds(
        monkeypatch, [{"action": "spawn_children", "children": [child]}, action()]
    )
    asyncio.run(runner.run())
    result = runner.result()
    assert (
        result.statuses[root.task_id]
        == result.statuses["identity-child"]
        == "awaiting_clarification"
    )
    assert result.coverage.resolved == 0 and result.coverage.total == 1
    assert result.clarification.evidence_ids == (child_record.evidence_id,)
    assert not runner.evidence.for_task(root.task_id)
    assert not runner.synthesis_evidence()
