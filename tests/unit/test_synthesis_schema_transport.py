"""Synthesis transport preserves eligible evidence and original history exactly."""

import asyncio
from dataclasses import replace
import json

import pytest

from general_manager.chat.planned import schema_projection as codec, synthesis
from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.evidence import EvidenceRecord
from general_manager.chat.providers.base import Message
from tests.unit.test_chat_planned_scheduler import _task
from tests.unit.test_chat_planned_synthesis import _settings, _SynthesisProvider
from tests.unit.test_schema_projection import schema


def synthesis_fixture():
    payload = schema()
    records = (
        EvidenceRecord.create(
            "schema-a", "t1", "schema", "schema-call-a", {"manager": "A"}, payload
        ),
        EvidenceRecord.create(
            "query-a",
            "t1",
            "query",
            "query-call",
            {"manager": "A"},
            {"data": [payload, payload], "complete": True, "total_count": 2},
        ),
        EvidenceRecord.create(
            "schema-b", "t1", "schema", "schema-call-b", {"manager": "B"}, payload
        ),
    )
    historical = codec.with_historical_schema(
        Message(
            "assistant",
            "Historical tool data (get_manager_schema): " + json.dumps(payload),
        ),
        tool_name="get_manager_schema",
        tool_result=payload,
        binding={"conversation": "c1", "message": 7},
    )
    history = (
        Message("system", "privileged context"),
        Message("tool", "not visible in synthesis"),
        Message("user", json.dumps(payload)),
        historical,
        Message("assistant", historical.content),
    )
    context = synthesis.SynthesisTaskContext(_task("t1"), ())
    messages = synthesis._messages(
        "Auswertung",
        records,
        {"resolved_task_ids": ["t1"]},
        "Configured scope",
        history,
        task_context=(context,),
    )
    return records, history, messages


def test_synthesis_selects_only_trusted_schema_origins_and_roundtrips_every_field():
    records, _, messages = synthesis_fixture()
    logical = messages[-1]
    assert logical.content.startswith("RESOLVED_REFERENCE_DATA=")
    assert [slot.path for slot in logical.schema_slots] == [
        ("resolved_evidence", 0, "payload"),
        ("resolved_evidence", 2, "payload"),
        ("conversation_context", 1, "content"),
    ]
    for slot, record in zip(
        logical.schema_slots[:2], (records[0], records[2]), strict=True
    ):
        assert slot.source == "synthesis_schema"
        assert json.loads(slot.binding) == {
            "task_id": record.task_id,
            "evidence_id": record.evidence_id,
            "call_identity": record.call_identity,
            "provenance": dict(record.provenance),
        }
    transport = codec.compact_messages(messages)
    assert transport[-1].logical_content == logical.content
    assert len(json.dumps(transport[-1].content)) < len(json.dumps(logical.content))
    assert codec.logical_messages(transport) == messages
    original = json.loads(logical.content.split("=", 1)[1])
    projection = codec.transport_projection(transport[-1].content)
    expanded = codec.expand_reference(projection, original, logical.schema_slots)
    assert expanded == original
    assert (
        projection["reference"]["resolved_evidence"][1]
        == original["resolved_evidence"][1]
    )
    assert (
        projection["reference"]["conversation_context"][0]
        == original["conversation_context"][0]
    )
    assert (
        projection["reference"]["conversation_context"][2]
        == original["conversation_context"][2]
    )
    for key in (
        "original_request",
        "coverage",
        "completed_task_context",
        "required_json_schema",
        "configured_context",
    ):
        assert projection["reference"][key] == original[key]
    assert transport[:-1] == messages[:-1]


def test_synthesis_provider_receives_compacted_eligible_records_only():
    records, history, _ = synthesis_fixture()
    foreign = EvidenceRecord.create(
        "foreign", "unresolved", "schema", "other-call", {"manager": "Other"}, schema()
    )
    _SynthesisProvider.responses = ['{"answer":"grounded","evidence_ids":["query-a"]}']
    _SynthesisProvider.calls = []
    result = asyncio.run(
        synthesis.synthesize_answer(
            "Auswertung",
            (*records, foreign),
            {"resolved_task_ids": ["t1"]},
            _settings(),
            RoundBudget(()),
            conversation_context=history,
        )
    )
    assert result.evidence_ids == ("query-a",)
    wire = _SynthesisProvider.calls[0][-1]
    assert wire.logical_content is not None
    restored = codec.logical_messages(_SynthesisProvider.calls[0])[-1]
    reference = json.loads(restored.content.split("=", 1)[1])
    assert [r["evidence_id"] for r in reference["resolved_evidence"]] == [
        r.evidence_id for r in records
    ]
    assert reference["required_json_schema"]["oneOf"][0]["properties"]["evidence_ids"][
        "items"
    ]["enum"] == sorted(r.evidence_id for r in records)
    assert all("foreign" not in slot.binding for slot in wire.schema_slots)


def test_synthesis_rejects_stale_history_origin_before_dispatch():
    records, history, _ = synthesis_fixture()
    tampered = (
        *history[:3],
        replace(history[3], content=history[3].content + " "),
        history[4],
    )
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        synthesis._messages("question", records, {}, conversation_context=tampered)


@pytest.mark.parametrize("prefix", ["REFERENCE_DATA=", "RESOLVED_REFERENCE_DATA="])
def test_known_reference_prefixes_roundtrip_without_changing_the_default(prefix):
    reference = {"resolved_evidence": [{"kind": "schema", "payload": schema()}]}
    slot = codec.SchemaSlot(
        ("resolved_evidence", 0, "payload"), "synthesis_schema", '{"evidence_id":"s1"}'
    )
    message = codec.reference_message(reference, (slot,), prefix=prefix)
    assert message.content.startswith(prefix)
    assert codec.logical_messages(codec.compact_messages([message])) == [message]
    default = codec.reference_message({}, ())
    assert default.content == "REFERENCE_DATA={}"
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec.reference_message(reference, (slot,), prefix="ARBITRARY_DATA=")


@pytest.mark.parametrize("kind", ["query", "calculation"])
def test_synthesis_selector_cannot_compact_non_schema_evidence(kind):
    reference = {"resolved_evidence": [{"kind": kind, "payload": schema()}]}
    slot = codec.SchemaSlot(
        ("resolved_evidence", 0, "payload"), "synthesis_schema", "{}"
    )
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec.project_reference(reference, (slot,))
