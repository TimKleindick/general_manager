"""Whole-reference pooling preserves text, provenance and evidence authority."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from general_manager.chat.planned import schema_projection as api
from general_manager.chat.providers.base import Message


def reference():
    definition = {
        "description": "untrusted large schema description " * 8,
        "kind": "INPUT_OBJECT",
        "fields": [{"name": f"field{i}", "type": "String"} for i in range(30)],
        "literal": {"$gm_ref": 4, "$gm_patch": [1, [[0, "literal"]]]},
    }
    schema = {
        "manager": "Part",
        "schema_view": "overview",
        "snapshot": "a" * 64,
        "types": {f"Type{i}": deepcopy(definition) for i in range(4)},
    }
    fmt = api.JsonTextFormat(prefix="Historical tool data: ", sort_keys=True)
    ref = {
        "task": {"task_id": "t"},
        "original_request": '{"$gm_ref":0} keep exactly',
        "task_evidence": [
            {
                "evidence_id": "e",
                "kind": "schema",
                "payload": schema,
                "requirement_ids": [],
            }
        ],
        "required_action_schema": {
            "properties": {f"field{i}": deepcopy(definition) for i in range(5)}
        },
        "conversation_context": [
            {"role": "system", "content": json.dumps(schema)},
            {"role": "assistant", "content": fmt.render(schema)},
            {"role": "user", "content": fmt.render(schema)},
        ],
        "query_data": [{"missing": None, "value": 0}, {"missing": None, "value": 0}],
    }
    slots = (
        api.SchemaSlot(
            ("task_evidence", 0, "payload"),
            "task_schema",
            '{"task_id":"t","evidence_id":"e","snapshot":"a"}',
        ),
        api.SchemaSlot(
            ("conversation_context", 1, "content"),
            "history_schema",
            '{"tool":"get_manager_schema","snapshot":"a"}',
            fmt,
        ),
    )
    return ref, slots


def bound(ref, slots, **kwargs):
    assert hasattr(api, "ReferenceBinding"), "explicit whole-reference binding missing"
    return api.reference_message(ref, slots, reference_scope="executor", **kwargs)


def test_bound_whole_reference_roundtrip_restores_all_text_and_order():
    ref, slots = reference()
    original = deepcopy(ref)
    message = bound(ref, slots, ensure_ascii=False)
    transported = api.compact_messages([message])[0]
    assert transported.logical_content == message.content
    assert transported.projection_receipt["format"] == api.REFERENCE_VERSION
    projection = api.transport_projection(transported.content)
    restored = api.expand_reference(
        projection, ref, slots, reference_binding=message.reference_binding
    )
    assert api._dump(restored) == api._dump(original)
    assert api.logical_messages([transported])[0] == message
    assert ref == original
    assert len(json.dumps(transported.content)) < len(json.dumps(message.content))
    assert restored["task_evidence"][0]["requirement_ids"] == []
    assert restored["query_data"] == original["query_data"]


@pytest.mark.parametrize(
    "change",
    [
        "owner",
        "scope",
        "root_hash",
        "slot_binding",
        "snapshot",
        "foreign_pool",
        "extra_metadata",
        "format",
    ],
)
def test_bound_reference_rejects_changed_scope_source_and_transport(change):
    ref, slots = reference()
    message = bound(ref, slots)
    projection = api.transport_projection(api.compact_messages([message])[0].content)
    binding = message.reference_binding
    if change == "owner":
        binding = replace(binding, owner="other")
    elif change == "scope":
        binding = replace(binding, scope="judge")
    elif change == "root_hash":
        binding = replace(binding, sha256="0" * 64)
    elif change == "slot_binding":
        slots = (replace(slots[0], binding='{"snapshot":"b"}'), slots[1])
    elif change == "snapshot":
        ref["task_evidence"][0]["payload"]["snapshot"] = "b" * 64
    elif change == "foreign_pool":
        projection["objects"][0] = "foreign"
    elif change == "extra_metadata":
        projection["unexpected"] = 1
    else:
        projection["format"] = api.VERSION
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        api.expand_reference(projection, ref, slots, reference_binding=binding)


def test_root_projection_requires_explicit_annotation_and_leaves_privileged_roles():
    ref, slots = reference()
    message = bound(ref, slots)
    system = Message("system", message.content)
    bare = Message("user", message.content)
    projected = api.compact_messages([system, bare, message])
    assert projected[:2] == [system, bare]
    legacy = api.reference_message(ref, slots)
    legacy_projection = api.transport_projection(
        api.compact_messages([legacy])[0].content
    )
    assert legacy_projection["format"] == api.VERSION
    assert api.expand_reference(legacy_projection, ref, slots) == ref
    with pytest.raises(ValueError):
        api.compact_messages([replace(message, role="system")])


@pytest.mark.parametrize(
    "marker",
    [
        {"$gm_ref": 0},
        {"$gm_shape": [1, []]},
        {"$gm_patch": [0, [[0, 2]]]},
        {"$gm_literal": [["$gm_ref", 1]]},
    ],
)
def test_literal_markers_outside_schema_are_never_reinterpreted(marker):
    ref, slots = reference()
    ref["query_data"].append(marker)
    message = bound(ref, slots)
    transported = api.compact_messages([message])[0]
    assert api.logical_messages([transported])[0].content == message.content


def test_bound_reference_expansion_is_bounded_and_no_op_is_not_enlarged():
    ref, slots = reference()
    message = bound(ref, slots)
    projection = api.transport_projection(api.compact_messages([message])[0].content)
    with pytest.raises(ValueError):
        api.expand_reference(
            projection,
            ref,
            slots,
            reference_binding=message.reference_binding,
            max_chars=100,
        )
    tiny = bound({"task": {"task_id": "tiny"}, "rows": [0, None, 0]}, ())
    assert api.compact_messages([tiny]) == [tiny]


@pytest.mark.parametrize("change", ["whitespace", "feedback"])
def test_bound_reference_without_schema_slots_rejects_unattested_content(change):
    message = bound(
        {"task": {"task_id": "t"}, "action_validation_error": {"code": "original"}},
        (),
    )
    content = message.content
    if change == "whitespace":
        content += " "
    else:
        content = content.replace('"original"', '"forged"')
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        api.compact_messages([replace(message, content=content)])
