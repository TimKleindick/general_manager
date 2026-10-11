"""Versioned whole-reference tokens retain exact literals and source bindings."""

from copy import deepcopy
import json

import pytest

from general_manager.chat.planned import schema_projection as api
from tests.unit.test_reference_capacity_v51 import reference


def encoded():
    ref, slots = reference()
    ref["query_data"].extend(
        [
            "~",
            "~0",
            "~~0",
            "~01",
            "~true",
            "~🚢",
            {"~0": "~1"},
            {"$gm_ref": "~0", "$gm_literal": [["~0", "~1"]]},
        ]
    )
    message = api.reference_message(ref, slots, reference_scope="executor")
    packed = api.project_reference(
        ref, slots, reference_binding=message.reference_binding
    )
    return ref, slots, message, packed


def test_reference_two_uses_short_tokens_and_preserves_literal_collisions():
    ref, slots, message, packed = encoded()
    assert packed["format"] == "gm.reference-data/2"
    assert '"$gm_ref":' not in json.dumps(packed["objects"])
    assert api._dump(
        api.expand_reference(
            packed, ref, slots, reference_binding=message.reference_binding
        )
    ) == api._dump(ref)
    transported = api.compact_messages([message])[0]
    assert transported.projection_receipt["format"] == "gm.reference-data/2"
    assert api.logical_messages([transported])[0].content == message.content
    assert len(json.dumps(transported.content)) < len(json.dumps(message.content))


@pytest.mark.parametrize(
    "token", ["~01", "~-1", "~+1", "~true", "~1.0", "~", "~\u0661", "~99999"]
)
def test_noncanonical_reference_tokens_fail_closed(token):
    ref, slots, message, packed = encoded()
    packed["objects"][0] = token
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        api.expand_reference(
            packed, ref, slots, reference_binding=message.reference_binding
        )


@pytest.mark.parametrize(
    "marker", [{"$gm_ref": False}, {"$gm_ref": 0}, {"$gm_ref": "0"}, {"$gm_ref": -1}]
)
def test_version_two_rejects_unescaped_old_reference_objects(marker):
    ref, slots, message, packed = encoded()
    packed["objects"][0] = marker
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        api.expand_reference(
            packed, ref, slots, reference_binding=message.reference_binding
        )


def test_schema_only_grammar_stays_version_three():
    ref, slots, _, _ = encoded()
    packed = api.project_reference(ref, slots)
    assert packed["format"] == "gm.schema-data/3"
    assert api._dump(api.expand_reference(packed, ref, slots)) == api._dump(ref)


def test_old_reference_one_retains_tilde_literals_and_backward_object_references():
    ref = {"task": {"task_id": "old"}, "copies": [{"text": "~0"}] * 2}
    message = api.reference_message(ref, (), reference_scope="executor")
    root = {
        "path": [],
        "source": "executor_reference",
        "binding": '{"scope":"executor","owner":"old"}',
        "sha256": api._hash(ref),
        "text_format": None,
    }
    packed = {
        "format": "gm.reference-data/1",
        "instruction": api.LEGACY_REFERENCE_INSTRUCTION,
        "original_sha256": api._hash(ref),
        "occurrences": [root],
        "objects": [{"text": "~0"}],
        "reference": {"task": {"task_id": "old"}, "copies": [{"$gm_ref": 0}] * 2},
    }
    assert api._dump(
        api.expand_reference(
            packed, ref, (), reference_binding=message.reference_binding
        )
    ) == api._dump(ref)


def test_version_two_keeps_exact_instruction_metadata_and_existing_bounds():
    ref, slots, message, packed = encoded()
    assert packed["format"] == "gm.reference-data/2"
    changed = deepcopy(packed)
    changed["instruction"] += " "
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        api.expand_reference(
            changed, ref, slots, reference_binding=message.reference_binding
        )
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        api.expand_reference(
            packed,
            ref,
            slots,
            reference_binding=message.reference_binding,
            max_chars=100,
        )


@pytest.mark.parametrize("version", [{}, [], False, 2])
def test_malformed_version_fails_closed_as_value_error(version):
    ref, slots, message, packed = encoded()
    packed["format"] = version
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        api.expand_reference(
            packed, ref, slots, reference_binding=message.reference_binding
        )


def test_generated_nested_literals_and_shape_keys_roundtrip_with_exact_types():
    scalars = [None, False, True, 0, 1, 1.0, "0", "~0", "~~0", "~", "🚢"]
    ref, slots = reference()
    rows = []
    for index in range(80):
        row = {
            "~" + str(key): scalars[(index * 15 + key * 7) % len(scalars)]
            for key in range(15)
        }
        row["markers"] = {"$gm_ref": index, "$gm_patch": [index, [[0, "~0"]]]}
        rows.append(row)
    ref["generated"] = rows + deepcopy(rows)
    message = api.reference_message(ref, slots, reference_scope="executor")
    transported = api.compact_messages([message])[0]
    assert transported.projection_receipt["format"] == "gm.reference-data/2"
    assert api.logical_messages([transported])[0].content == message.content
