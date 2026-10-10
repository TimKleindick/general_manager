"""Distinct schema definitions share keys without changing any logical evidence."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from general_manager.chat.planned import schema_projection as codec
from general_manager.chat.providers.base import Message


def reference(count=200):
    values = [
        {
            "public_contract_field_name": f"f{i}",
            "exact_graphql_type_reference": f"Type{i}!",
            "nullable_default_in_public_contract": None,
            "typed_boolean_default": i % 2 == 0,
            "typed_integer_default": i,
            "field_description_preserved_verbatim": f'Beschreibung {i}: "x" \\ Ω',
        }
        for i in range(count)
    ]
    original = {
        "original_request": '{"$gm_shape":[0,[]]}',
        "task_evidence": [
            {"kind": "schema", "payload": {"definitions": values}},
            {"kind": "query", "payload": {"duplicate_rows": [values[0], values[0]]}},
        ],
        "conversation_context": [{"role": "user", "content": json.dumps(values)}],
    }
    slots = (
        codec.SchemaSlot(
            ("task_evidence", 0, "payload"),
            "task_schema",
            '{"manager":"A","snapshot":"a","owner":"one"}',
        ),
    )
    return original, slots


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def shapes(node):
    if isinstance(node, dict):
        if "$gm_shape" in node:
            yield node
        for child in node.values():
            yield from shapes(child)
    elif isinstance(node, list):
        for child in node:
            yield from shapes(child)


def test_distinct_definitions_share_keys_with_exact_order_values_and_full_gain():
    original, slots = reference()
    packed = codec.project_reference(original, slots)
    assert packed["format"] == "gm.schema-data/3"
    assert list(shapes(packed["reference"]["task_evidence"][0]["payload"]))
    assert dump(codec.expand_reference(packed, original, slots)) == dump(original)
    assert packed == codec.project_reference(original, slots)
    assert packed["reference"]["task_evidence"][1] == original["task_evidence"][1]
    assert (
        packed["reference"]["conversation_context"] == original["conversation_context"]
    )
    assert packed["reference"]["original_request"] == original["original_request"]
    selected_size = len(json.dumps(dump(original["task_evidence"][0]["payload"])))
    wire = {
        **packed["reference"],
        "schema_transport": {k: v for k, v in packed.items() if k != "reference"},
    }
    assert (
        len(json.dumps(dump(original))) - len(json.dumps(dump(wire)))
        > selected_size * 0.45
    )


@pytest.mark.parametrize(
    "literal",
    [
        {"$gm_shape": [0, []]},
        {"$gm_ref": 0},
        {"$gm_literal": [["x", 1]]},
        {"$gm_shape": True, "extra": False},
    ],
)
def test_all_reserved_keys_remain_literal_with_false_zero_and_float(literal):
    original, slots = reference()
    original["task_evidence"][0]["payload"]["literal"] = literal
    original["task_evidence"][0]["payload"]["typed"] = [False, 0, 0.0, True, 1, 1.0]
    assert dump(
        codec.expand_reference(
            codec.project_reference(original, slots), original, slots
        )
    ) == dump(original)


@pytest.mark.parametrize(
    "mutation",
    [
        "bool",
        "negative",
        "forward",
        "extra",
        "short",
        "long",
        "nonlist",
        "keys_bool",
        "keys_duplicate",
        "keys_marker",
        "shape_extra",
    ],
)
def test_corrupted_shape_is_rejected(mutation):
    original, slots = reference()
    packed = codec.project_reference(original, slots)
    shape = next(shapes(packed["reference"]["task_evidence"][0]["payload"]))
    index, values = shape["$gm_shape"]
    if mutation == "bool":
        shape["$gm_shape"][0] = True
    elif mutation == "negative":
        shape["$gm_shape"][0] = -1
    elif mutation == "forward":
        shape["$gm_shape"][0] = len(packed["objects"])
    elif mutation == "extra":
        shape["$gm_shape"].append("foreign")
    elif mutation == "short":
        values.pop()
    elif mutation == "long":
        values.append("foreign")
    elif mutation == "nonlist":
        shape["$gm_shape"][1] = {"foreign": values}
    elif mutation == "keys_bool":
        packed["objects"][index][0] = True
    elif mutation == "keys_duplicate":
        packed["objects"][index][1] = packed["objects"][index][0]
    elif mutation == "keys_marker":
        packed["objects"][index][0] = "$gm_ref"
    else:
        shape["foreign"] = "extra"
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec.expand_reference(packed, original, slots)


def test_equal_key_sets_with_distinct_order_keep_each_order():
    original, slots = reference()
    rows = original["task_evidence"][0]["payload"]["definitions"]
    rows.extend(dict(reversed(list(row.items()))) for row in deepcopy(rows))
    packed = codec.project_reference(original, slots)
    restored = codec.expand_reference(packed, original, slots)
    assert dump(restored) == dump(original)
    assert list(rows[0]) != list(rows[-1])


def test_source_binding_and_snapshot_do_not_merge_distinct_definitions():
    original, slots = reference()
    other = deepcopy(original["task_evidence"][0])
    other["payload"]["definitions"][0]["exact_graphql_type_reference"] = (
        "HiddenRemovedType!"
    )
    original["task_evidence"].append(other)
    slots += (
        codec.SchemaSlot(
            ("task_evidence", 2, "payload"),
            "task_schema",
            '{"manager":"B","snapshot":"b","owner":"two"}',
        ),
    )
    packed = codec.project_reference(original, slots)
    assert dump(codec.expand_reference(packed, original, slots)) == dump(original)
    assert len(packed["occurrences"]) == 2
    assert packed["occurrences"][0]["binding"] != packed["occurrences"][1]["binding"]
    with pytest.raises(ValueError):
        codec.expand_reference(
            packed, original, (replace(slots[0], binding=slots[1].binding), slots[1])
        )


def test_frozen_legacy_codec_keeps_its_original_marker_semantics_and_version():
    fixture = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/schema_projection_v1_legacy_v44.json"
        ).read_text()
    )
    slot = fixture["slot"]
    slots = (codec.SchemaSlot(tuple(slot["path"]), slot["source"], slot["binding"]),)
    assert fixture["projection"]["format"] == "gm.schema-data/1"
    assert dump(
        codec.expand_reference(fixture["projection"], fixture["original"], slots)
    ) == dump(fixture["original"])
    relabelled = deepcopy(fixture["projection"])
    relabelled["format"] = "gm.schema-data/3"
    with pytest.raises(ValueError):
        codec.expand_reference(relabelled, fixture["original"], slots)
    relabelled["instruction"] = codec.INSTRUCTION
    with pytest.raises(ValueError):
        codec.expand_reference(relabelled, fixture["original"], slots)


def test_explicit_evaluation_version_cannot_mislabel_new_transport():
    from experiments.gm_eval.profiles import (
        EvaluationProfile,
        ExperimentProvider,
        make_profile,
    )

    assert make_profile("strong-only").schema_transport_version == "gm.schema-data/3"
    with pytest.raises(ValueError, match="unsupported_schema_transport_version"):
        EvaluationProfile(
            "strong-only", "weak", "strong", schema_transport_version="gm.schema-data/1"
        )
    with pytest.raises(ValueError, match="unsupported_schema_transport_version"):
        ExperimentProvider.from_config(
            {"model": "fake", "schema_transport_version": "gm.schema-data/1"}
        )


def test_real_serialized_guard_accepts_exact_compaction_and_blocks_unselected_text():
    from experiments.siwc_eval.errors import EvalError
    from experiments.siwc_eval.provider import Provider, payload

    original, slots = reference(900)
    logical = codec.reference_message(original, slots)
    # Leave only the permitted schema occurrence large in this guard fixture.
    original["conversation_context"] = []
    logical = codec.reference_message(original, slots)
    packed = codec.compact_messages([logical])
    assert len(json.dumps(payload("fake", [logical], []))) > 200000
    assert len(json.dumps(payload("fake", packed, []))) < 200000
    assert codec.logical_messages(packed) == [logical]
    calls = []

    class Transport:
        async def stream(self, body):
            calls.append(body)
            yield {"type": "response.completed", "response": {"output": []}}

    async def run(messages):
        return [
            event
            async for event in Provider("fake", Transport()).complete(messages, [])
        ]

    asyncio.run(run(packed))
    assert len(calls) == 1
    with pytest.raises(EvalError, match="input_budget_exhausted"):
        asyncio.run(run([Message("user", "x" * 200001)]))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [("format", "gm.schema-data/1"), ("transport_content_sha256", "0" * 64)],
)
def test_transport_receipt_must_match_the_version_and_exact_wire(field, value):
    original, slots = reference()
    message = codec.compact_messages([codec.reference_message(original, slots)])[0]
    receipt = {**message.projection_receipt, field: value}
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec.logical_messages([replace(message, projection_receipt=receipt)])


@pytest.mark.parametrize(
    "field", ["format", "instruction", "original_sha256", "occurrences"]
)
def test_malformed_transport_metadata_keeps_the_stable_rejection(field):
    original, slots = reference()
    message = codec.compact_messages([codec.reference_message(original, slots)])[0]
    wire = json.loads(message.content.split("=", 1)[1])
    wire["schema_transport"].pop(field)
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec.logical_messages(
            [replace(message, content="REFERENCE_DATA=" + dump(wire))]
        )
