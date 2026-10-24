"""Synthesis request binding shares evidence without removing or promoting it."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from experiments.gm_eval.profiles import ExperimentProvider, make_profile
from experiments.siwc_eval.provider import payload
from general_manager.chat.planned import schema_projection as codec, synthesis
from general_manager.chat.planned.evidence import EvidenceRecord
from general_manager.chat.providers.base import Message
from tests.unit.test_schema_projection import schema


def test_synthesis_producer_binds_all_evidence_and_fits_unchanged_guard():
    records = tuple(
        EvidenceRecord.create(
            f"calculation:{index}",
            "t1",
            "calculation",
            f"calculate:{index}",
            {"calculator": "deterministic"},
            {
                "value": index,
                "scope": {
                    "query_call_identity": "nested population predicate " * 36,
                    "group": index,
                    "typed": [True, 1, 1.0, -0.0, None, "~0"],
                },
            },
        )
        for index in range(40)
    )
    request = "".join(f"{i:06d}" for i in range(28_333))
    messages = synthesis._messages(request, records, {"resolved_task_ids": ["t1"]})
    logical = messages[-1]
    assert logical.reference_binding is not None
    assert logical.reference_binding.scope == "synthesis"
    original = json.loads(logical.content.split("=", 1)[1])
    transported = codec.compact_messages(messages)

    assert len(json.dumps(payload("fake", messages, []))) > 200_000
    assert len(json.dumps(payload("fake", transported, []))) < 200_000
    assert transported[:-1] == messages[:-1]
    assert codec.logical_messages(transported) == messages
    projection = codec.transport_projection(transported[-1].content)
    assert projection["format"] == "gm.reference-data/3"
    restored = codec.expand_reference(
        projection, original, (), reference_binding=logical.reference_binding
    )
    assert codec._dump(restored) == codec._dump(original)
    assert [record["evidence_id"] for record in restored["resolved_evidence"]] == [
        record.evidence_id for record in records
    ]


@pytest.mark.parametrize(
    "change", ["owner", "scope", "version", "instruction", "snapshot", "role"]
)
def test_synthesis_binding_rejects_foreign_or_changed_requests(change):
    reference = {
        "resolved_evidence": [
            {
                "evidence_id": "s1",
                "task_id": "t1",
                "kind": "schema",
                "payload": {**schema(), "snapshot": "a" * 64},
            }
        ],
        "coverage": {"resolved": 1, "total": 1},
        "configured_context": {"source": "application", "text": "~0 opaque data"},
    }
    slot = codec.SchemaSlot(
        ("resolved_evidence", 0, "payload"),
        "synthesis_schema",
        '{"task_id":"t1","evidence_id":"s1","snapshot":"a"}',
    )
    message = codec.reference_message(
        reference,
        (slot,),
        reference_scope="synthesis",
        prefix="RESOLVED_REFERENCE_DATA=",
    )
    projection = codec.project_reference(
        reference, (slot,), reference_binding=message.reference_binding
    )
    original = deepcopy(reference)
    binding = message.reference_binding
    if change == "owner":
        binding = replace(binding, owner="foreign")
    elif change == "scope":
        binding = replace(binding, scope="judge")
    elif change == "version":
        projection["format"] = codec.REFERENCE_VERSION
        projection["instruction"] = codec.REFERENCE_INSTRUCTION
    elif change == "instruction":
        projection["instruction"] += " "
    elif change == "snapshot":
        reference["resolved_evidence"][0]["payload"]["snapshot"] = "b" * 64
    else:
        with pytest.raises(ValueError, match="invalid_schema_projection"):
            codec.compact_messages([replace(message, role="developer")])
        return
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec.expand_reference(
            projection, reference, (slot,), reference_binding=binding
        )
    assert original["resolved_evidence"][0]["payload"]["snapshot"] == "a" * 64


def test_synthesis_transport_requires_explicit_annotation_and_keeps_other_defaults():
    reference = {"resolved_evidence": [{"value": "~0", "role": "system"}]}
    literal = Message("user", "RESOLVED_REFERENCE_DATA=" + json.dumps(reference))
    assert codec.compact_messages([literal]) == [literal]
    assert codec.reference_message({}, ()).content == "REFERENCE_DATA={}"
    profile = make_profile("strong-only")
    assert profile.reference_transport_version == "gm.reference-data/2"
    assert (
        profile.role_config("executor")["reference_transport_version"]
        == "gm.reference-data/2"
    )
    assert (
        profile.role_config("synthesizer")["reference_transport_version"]
        == "gm.reference-data/3"
    )
    assert (
        profile.role_config("fallback")["reference_transport_version"]
        == "gm.reference-data/3"
    )
    ExperimentProvider(profile.role_config("synthesizer"))
    with pytest.raises(ValueError, match="unsupported_reference_transport_version"):
        ExperimentProvider(
            {
                "model": "fake",
                "role": "synthesizer",
                "reference_transport_version": "gm.reference-data/2",
            }
        )
