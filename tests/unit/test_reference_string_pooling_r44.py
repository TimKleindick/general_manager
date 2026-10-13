"""Repeated schema value strings must fit the unchanged provider guard."""

from copy import deepcopy
import json

from experiments.siwc_eval.provider import payload
from general_manager.chat.planned import schema_projection as codec
from general_manager.chat.providers.base import Message


def test_diverse_schema_fields_fit_guard_without_dropping_query_text():
    schema = {
        "manager": "Project",
        "snapshot": "a" * 64,
        "output_fields": {
            f"field{i:03}": {
                "source": "interface_filter",
                "manager": f"Manager{i:03}",
                "kind": "reference",
                "type": f"Output{i:03}",
            }
            for i in range(250)
        },
    }
    reference = {
        "task": {"task_id": "t1"},
        "task_evidence": [
            {
                "evidence_id": "s1",
                "kind": "schema",
                "payload": schema,
                "requirement_ids": ["schema"],
            }
        ],
        # Opaque query text is retained exactly, never parsed as a schema.
        "query_data": "".join(f"{i:06d}" for i in range(29_333)),
    }
    slot = codec.SchemaSlot(
        ("task_evidence", 0, "payload"),
        "task_schema",
        '{"task_id":"t1","snapshot":"a"}',
    )
    original = deepcopy(reference)
    logical = codec.reference_message(reference, (slot,), reference_scope="executor")
    transported = codec.compact_messages([logical])[0]

    assert len(json.dumps(payload("fake", [logical], []))) > 200_000
    assert len(json.dumps(payload("fake", [transported], []))) < 200_000
    restored = codec.expand_reference(
        codec.transport_projection(transported.content),
        reference,
        (slot,),
        reference_binding=logical.reference_binding,
    )
    assert codec._dump(restored) == codec._dump(original)
    assert reference == original
    assert codec.logical_messages([transported])[0] == logical


def test_repeated_long_literals_keep_types_roles_and_marker_keys():
    literal = '~0 literal "quote" and 🚢 ' * 12
    reference = {
        "task": {"task_id": "t1"},
        "rows": [
            {
                "id": index,
                "description": literal,
                "typed": [True, 1, 1.0, -0.0, "1", None],
                "markers": {"$gm_ref": index, "~0": literal},
            }
            for index in range(50)
        ],
        "conversation_context": [{"role": "system", "content": literal}],
    }
    logical = codec.reference_message(reference, (), reference_scope="executor")
    privileged = Message("developer", literal)
    messages = codec.compact_messages([privileged, logical])

    assert messages[0] == privileged
    assert len(json.dumps(messages[1].content)) < len(json.dumps(logical.content)) / 2
    projection = codec.transport_projection(messages[1].content)
    restored = codec.expand_reference(
        projection, reference, (), reference_binding=logical.reference_binding
    )
    assert codec._dump(restored) == codec._dump(reference)
    assert codec.logical_messages(messages) == [privileged, logical]


def test_many_repeated_long_provenance_values_fit_guard():
    reference = {
        "task": {"task_id": "t1"},
        "calculation_provenance": [
            {
                "evidence_id": f"calculation:{index}:{copy}",
                "query_call_identity": f"query {index:02}: "
                + "nested predicate provenance " * 32,
            }
            for index in range(40)
            for copy in range(2)
        ],
        "query_data": "".join(f"{i:06d}" for i in range(25_000)),
    }
    logical = codec.reference_message(reference, (), reference_scope="executor")
    transported = codec.compact_messages([logical])[0]

    assert len(json.dumps(payload("fake", [logical], []))) > 200_000
    assert len(json.dumps(payload("fake", [transported], []))) < 200_000
    assert codec.logical_messages([transported])[0] == logical
    assert codec._dump(
        codec.expand_reference(
            codec.transport_projection(transported.content),
            reference,
            (),
            reference_binding=logical.reference_binding,
        )
    ) == codec._dump(reference)
