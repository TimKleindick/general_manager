"""Overlapping maps preserve names, definitions, key order and source authority."""

from copy import deepcopy
import json

import pytest

from general_manager.chat.planned import schema_projection as codec


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def data(count=320):
    names = [
        f"VisibleRelationCategory{i:03}NestedConnectionFilterInputObjectDepthOne"
        for i in range(count)
    ]
    first = {name: {"kind": "input"} for name in names}
    first["OwnerType"] = {
        "kind": "object",
        "fields": {"value": {"type": "Int", "default": False}},
    }
    second = {name: {"kind": "input"} for name in reversed(names)}
    second["OwnerType"] = {"kind": "reference", "manager": "Owner"}
    second["SecondOnlyType"] = {"kind": "enum", "values": ["ON", "OFF"]}
    original = {
        "task_evidence": [
            {"kind": "schema", "payload": {"manager": "Owner", "type_manifest": first}},
            {
                "kind": "schema",
                "payload": {"manager": "Related", "type_manifest": second},
            },
            {"kind": "query", "payload": {"rows": [first, second]}},
        ],
        "original_request": dump(first),
    }
    slots = tuple(
        codec.SchemaSlot(
            ("task_evidence", i, "payload"),
            "task_schema",
            dump({"manager": manager, "snapshot": str(i), "owner": "user"}),
        )
        for i, manager in enumerate(["Owner", "Related"])
    )
    return original, slots


@pytest.mark.parametrize("count,minimum_saved", [(320, 0), (360, 10000)])
def test_different_maps_share_their_long_names_with_a_full_transport_gain(
    count, minimum_saved
):
    original, slots = data(count)
    packed = codec.project_reference(original, slots)
    restored = codec.expand_reference(packed, original, slots)
    assert dump(restored) == dump(original)
    assert packed["format"] == "gm.schema-data/3"
    shared_strings = [node for node in packed["objects"] if isinstance(node, str)]
    assert len(shared_strings) >= 300
    wire = {
        **packed["reference"],
        "schema_transport": {k: v for k, v in packed.items() if k != "reference"},
    }
    assert len(json.dumps(dump(original))) - len(json.dumps(dump(wire))) > minimum_saved
    assert packed["reference"]["task_evidence"][2] == original["task_evidence"][2]
    assert packed["reference"]["original_request"] == original["original_request"]
    assert packed == codec.project_reference(original, slots)
    assert len(packed["occurrences"]) == 2
    assert packed["occurrences"][0]["binding"] != packed["occurrences"][1]["binding"]
    assert (
        restored["task_evidence"][0]["payload"]["type_manifest"]["OwnerType"]
        != restored["task_evidence"][1]["payload"]["type_manifest"]["OwnerType"]
    )


@pytest.mark.parametrize(
    "literal", [{"$gm_shape": [0, []]}, {"$gm_ref": False}, {"$gm_literal": [["x", 0]]}]
)
def test_literal_keys_and_different_same_name_values_remain_exact(literal):
    original, slots = data()
    for index in [0, 1]:
        manifest = original["task_evidence"][index]["payload"]["type_manifest"]
        manifest["LiteralMarkerType"] = literal
        manifest["ScalarDefaultType"] = {
            "kind": "input",
            "values": [False, 0, 0.0, None, True, 1, 1.0][index : index + 5],
        }
    packed = codec.project_reference(original, slots)
    assert dump(codec.expand_reference(packed, original, slots)) == dump(original)


@pytest.mark.parametrize(
    "mutation", ["bool_key", "duplicate_key", "forward", "cycle", "unused", "foreign"]
)
def test_shared_key_string_corruption_is_rejected(mutation):
    original, slots = data()
    packed = codec.project_reference(original, slots)
    index = next(
        i for i, value in enumerate(packed["objects"]) if isinstance(value, str)
    )
    if mutation == "bool_key":
        packed["objects"][index] = True
    elif mutation == "duplicate_key":
        packed["objects"][index] = next(
            value
            for i, value in enumerate(packed["objects"])
            if isinstance(value, str) and i != index
        )
    elif mutation == "forward":
        packed["objects"][index] = {"$gm_ref": len(packed["objects"])}
    elif mutation == "cycle":
        packed["objects"][index] = {"$gm_ref": index}
    elif mutation == "unused":
        packed["objects"].append("foreign unused key")
    else:
        packed["objects"][index] = "ChangedTypeName"
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec.expand_reference(packed, original, slots)


def test_wide_joint_optimization_respects_object_limit_without_losing_values(
    monkeypatch,
):
    original, slots = data()
    monkeypatch.setattr(codec, "MAX_OBJECTS", 1)
    packed = codec.project_reference(original, slots)
    assert len(packed["objects"]) <= 1
    assert dump(codec.expand_reference(packed, original, slots)) == dump(original)


def test_single_wide_map_without_profitable_shared_keys_stays_exact():
    original, slots = data()
    original["task_evidence"] = original["task_evidence"][:1]
    packed = codec.project_reference(original, slots[:1])
    assert not any(isinstance(node, str) for node in packed["objects"])
    assert dump(codec.expand_reference(packed, original, slots[:1])) == dump(original)


def test_equal_long_key_strings_do_not_merge_enum_or_manager_default_values():
    original, slots = data()
    key = "LongVisibleSchemaDefaultAndEnumDefinitionWithIndependentManagerReference"
    original["task_evidence"][0]["payload"]["type_manifest"][key] = {
        "kind": "input",
        "default": False,
        "values": ["A", "B"],
        "manager": "Owner",
    }
    original["task_evidence"][1]["payload"]["type_manifest"][key] = {
        "kind": "input",
        "default": 0,
        "values": ["A", "C"],
        "manager": "Related",
    }
    preserved = deepcopy(original)
    packed = codec.project_reference(original, slots)
    assert dump(original) == dump(preserved)
    assert dump(codec.expand_reference(packed, original, slots)) == dump(preserved)


def test_small_single_key_manifest_entries_are_shared_when_fully_profitable():
    original, slots = data()
    second = original["task_evidence"][1]["payload"]["type_manifest"]
    # Match real overviews: almost-identical key sets, unequal owning definitions.
    second_ordered = {
        key: second.get(key, {"kind": "reference", "manager": "Owner"})
        for key in original["task_evidence"][0]["payload"]["type_manifest"]
    }
    original["task_evidence"][1]["payload"]["type_manifest"] = second_ordered
    packed = codec.project_reference(original, slots)
    assert {"kind": "input"} in packed["objects"]
    assert dump(codec.expand_reference(packed, original, slots)) == dump(original)
    assert packed["occurrences"][0]["sha256"] != packed["occurrences"][1]["sha256"]


def test_near_equal_maps_share_complete_value_sequences_and_explicit_differences():
    original, slots = data()
    first = original["task_evidence"][0]["payload"]["type_manifest"]
    second = original["task_evidence"][1]["payload"]["type_manifest"]
    original["task_evidence"][1]["payload"]["type_manifest"] = {
        key: second.get(key, {"kind": "reference", "manager": "Owner"}) for key in first
    }
    packed = codec.project_reference(original, slots)
    assert packed["format"] == "gm.schema-data/3"
    assert "$gm_patch" in dump(packed)
    assert dump(codec.expand_reference(packed, original, slots)) == dump(original)
    wire = {
        **packed["reference"],
        "schema_transport": {k: v for k, v in packed.items() if k != "reference"},
    }
    assert len(json.dumps(dump(original))) - len(json.dumps(dump(wire))) > 18000


def patch_nodes(node):
    if isinstance(node, dict):
        if "$gm_patch" in node:
            yield node
        for child in node.values():
            yield from patch_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from patch_nodes(child)


@pytest.mark.parametrize(
    "mutation",
    [
        "base_bool",
        "base_forward",
        "entry_bool",
        "entry_negative",
        "entry_outside",
        "duplicate",
        "empty",
        "foreign",
        "extra",
        "nonlist",
    ],
)
def test_corrupt_sequence_patch_is_rejected(mutation):
    original, slots = data()
    first = original["task_evidence"][0]["payload"]["type_manifest"]
    second = original["task_evidence"][1]["payload"]["type_manifest"]
    original["task_evidence"][1]["payload"]["type_manifest"] = {
        key: second.get(key, {"kind": "reference", "manager": "Owner"}) for key in first
    }
    packed = codec.project_reference(original, slots)
    node = next(patch_nodes(packed["reference"]))
    _base, changes = node["$gm_patch"]
    if mutation == "base_bool":
        node["$gm_patch"][0] = True
    elif mutation == "base_forward":
        node["$gm_patch"][0] = len(packed["objects"])
    elif mutation == "entry_bool":
        changes[0][0] = True
    elif mutation == "entry_negative":
        changes[0][0] = -1
    elif mutation == "entry_outside":
        changes[0][0] = 999999
    elif mutation == "duplicate":
        changes.append(deepcopy(changes[0]))
    elif mutation == "empty":
        node["$gm_patch"][1] = []
    elif mutation == "foreign":
        changes[0][1] = {"kind": "foreign"}
    elif mutation == "extra":
        node["extra"] = True
    else:
        node["$gm_patch"][1] = {"changes": changes}
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec.expand_reference(packed, original, slots)


def test_new_patch_marker_is_literal_schema_data_and_old_version_two_literal_remains_valid():
    original, slots = data()
    original["task_evidence"][0]["payload"]["literal"] = {
        "$gm_patch": [True, [[0, "untrusted"]]]
    }
    packed = codec.project_reference(original, slots)
    assert dump(codec.expand_reference(packed, original, slots)) == dump(original)


def test_real_legacy_version_two_fixture_keeps_its_original_literal_semantics():
    from pathlib import Path

    fixture = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/schema_projection_v2_legacy_v45.json"
        ).read_text()
    )
    raw = fixture["slot"]
    slots = (codec.SchemaSlot(tuple(raw["path"]), raw["source"], raw["binding"]),)
    assert fixture["projection"]["format"] == "gm.schema-data/2"
    assert dump(
        codec.expand_reference(fixture["projection"], fixture["original"], slots)
    ) == dump(fixture["original"])
    changed = deepcopy(fixture["projection"])
    changed["format"] = codec.VERSION
    changed["instruction"] = codec.INSTRUCTION
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec.expand_reference(changed, fixture["original"], slots)
    from experiments.gm_eval.profiles import EvaluationProfile

    with pytest.raises(ValueError, match="unsupported_schema_transport_version"):
        EvaluationProfile(
            "strong-only", "weak", "strong", schema_transport_version="gm.schema-data/2"
        )


def test_near_equal_sequences_already_in_the_table_share_a_backward_base():
    original, slots = data()
    first = original["task_evidence"][0]["payload"]["type_manifest"]
    second = original["task_evidence"][1]["payload"]["type_manifest"]
    original["task_evidence"][1]["payload"]["type_manifest"] = {
        key: second.get(key, {"kind": "reference", "manager": "Owner"}) for key in first
    }
    second = original["task_evidence"][1]["payload"]["type_manifest"]
    original["task_evidence"][0]["payload"]["repeated_manifests"] = [first, second]
    packed = codec.project_reference(original, slots)
    assert any(list(patch_nodes(node)) for node in packed["objects"])
    assert dump(codec.expand_reference(packed, original, slots)) == dump(original)
