"""Codec rewrites retain exact wire bytes without copying replaced trees."""

from copy import deepcopy
from hashlib import sha256

import pytest

from general_manager.chat.planned import schema_projection as codec


def _rewrite_fixture(kind, whole_reference):
    if kind == "small_containers":
        payload = {str(i): {"values": [1, 2, 3, 4]} for i in range(40)}
    elif kind == "key_shapes":
        payload = {
            "rows": [
                {"long_field_name_alpha": i, "long_field_name_beta": i + 1}
                for i in range(20)
            ]
        }
    elif kind == "wide_key_strings":
        keys = [
            f"long_shared_field_name_with_an_additional_namespace_segment_{i:03d}"
            for i in range(64)
        ]
        payload = {
            "left": dict.fromkeys(keys, 1),
            "right": dict.fromkeys(reversed(keys), 2),
        }
    else:
        values = [f"distinct_list_item_{i:03d}_with_long_text" for i in range(64)]
        other = values.copy()
        other[31] = "changed list item"
        payload = {"left": values, "right": other}
    payload["table"] = {"$gm_ref": 0}
    reference = {
        "task_evidence": [{"kind": "schema", "payload": payload}],
        "query_evidence": {"rows": [[True, 1, 0.0, -0.0]], "error": "~0"},
    }
    path = () if whole_reference else ("task_evidence", 0, "payload")
    slots = (codec.SchemaSlot(path, "reference_data", "{}"),)
    projection = {
        "format": codec.VERSION,
        "instruction": codec.INSTRUCTION,
        "original_sha256": "0" * 64,
        "objects": [{"nested": [[True, 1, 0.0, -0.0], "~literal"]}],
        "occurrences": [{"path": list(path), "binding": {"owner": ["alice"]}}],
        "reference": reference,
    }
    return projection, slots


@pytest.mark.parametrize("whole_reference", [False, True])
@pytest.mark.parametrize(
    "kind",
    ["small_containers", "key_shapes", "wide_key_strings", "similar_lists"],
)
def test_rewrites_do_not_copy_tables_or_roots_that_they_replace(
    monkeypatch, kind, whole_reference
):
    projection, slots = _rewrite_fixture(kind, whole_reference)
    original = codec._dump(projection)
    copied_tables = []
    copied_roots = []
    copied_selected_roots = []

    def measured_copy(value):
        # Measure the real copy operation; every codec rewrite still runs normally.
        if isinstance(value, dict) and {"objects", "reference"} <= value.keys():
            copied_tables.append(len(value["objects"]))
            copied_roots.append(len(value["reference"]))
            if not whole_reference:
                copied_selected_roots.append(
                    len(codec._get(value["reference"], slots[0].path))
                )
        return deepcopy(value)

    monkeypatch.setattr(codec, "deepcopy", measured_copy)
    result = getattr(codec, "_share_" + kind)(projection, slots)
    assert codec._dump(projection) == original
    assert codec._dump(result) != original, "exercise a profitable rewrite"
    assert (
        sha256(codec._dump(result).encode()).hexdigest()
        == WIRE_HASHES[kind, whole_reference]
    )
    result["occurrences"][0]["binding"]["owner"].append("bob")
    result["reference"]["query_evidence"]["rows"][0].append("changed")
    assert codec._dump(projection) == original
    assert copied_tables, "exercise the candidate copy operation"
    assert sum(copied_tables) == 0
    if whole_reference:
        assert sum(copied_roots) == 0
    else:
        assert sum(copied_selected_roots) == 0


@pytest.mark.parametrize("whole_reference", [False, True])
@pytest.mark.parametrize(
    "kind",
    ["small_containers", "key_shapes", "wide_key_strings", "similar_lists"],
)
def test_rewritten_wire_order_and_mutable_data_remain_independent(
    kind, whole_reference
):
    projection, slots = _rewrite_fixture(kind, whole_reference)
    result = getattr(codec, "_share_" + kind)(projection, slots)
    assert list(result) == list(projection)
    assert (
        sha256(codec._dump(result).encode()).hexdigest()
        == WIRE_HASHES[kind, whole_reference]
    )
    before = codec._dump(projection)
    result["occurrences"][0]["binding"]["owner"].append("bob")
    result["reference"]["query_evidence"]["rows"][0].append("changed")
    nested = next(
        item
        for item in result["objects"]
        if isinstance(item, dict) and "nested" in item
    )
    nested["nested"].append("changed")
    result["objects"].append({"new": ["changed"]})
    assert codec._dump(projection) == before


def test_whole_reference_copies_original_once_and_retains_independent_roundtrip(
    monkeypatch,
):
    reference = {
        "task": {"task_id": "t1"},
        "original_request": "~0",
        "evidence": {"rows": [[True, 1, -0.0]]},
    }
    binding = codec.ReferenceBinding("executor", "t1", codec._hash(reference))
    copies = []

    def measured_copy(value):
        if value is reference:
            copies.append(value)
        return deepcopy(value)

    monkeypatch.setattr(codec, "deepcopy", measured_copy)
    packed = codec.project_reference(reference, (), reference_binding=binding)
    assert (
        codec.expand_reference(packed, reference, (), reference_binding=binding)
        == reference
    )
    assert len(copies) == 1
    before = codec._dump(reference)
    packed["reference"]["evidence"]["rows"][0].append("changed")
    assert codec._dump(reference) == before


def test_selected_roots_share_ancestors_without_erasing_unselected_aliases():
    payload = {"fields": [{"name": "visible"}]}
    reference = {
        "task_evidence": [
            {"kind": "schema", "payload": payload},
            {"kind": "schema", "payload": payload},
            {"kind": "query", "payload": payload},
        ],
        "query_evidence": {"literal": payload},
    }
    projection = {
        "objects": [payload],
        "reference": reference,
        "occurrences": [{"binding": {"owner": ["alice"]}}],
    }
    slots = tuple(
        codec.SchemaSlot(("task_evidence", index, "payload"), "task_schema", "{}")
        for index in (0, 1)
    )
    before = codec._dump(projection)
    result = codec._copy_for_rewrite(projection, slots)
    expected = deepcopy(projection)
    expected["objects"] = []
    for slot in slots:
        codec._set(expected["reference"], slot.path, {})
    assert codec._dump(result) == codec._dump(expected)
    assert list(result) == list(projection)
    assert list(result["reference"]) == list(reference)
    assert list(result["reference"]["task_evidence"][0]) == ["kind", "payload"]
    for slot in slots:
        codec._set(result["reference"], slot.path, {"replacement": []})
    result["reference"]["query_evidence"]["literal"]["fields"].append("changed")
    result["occurrences"][0]["binding"]["owner"].append("bob")
    assert codec._dump(projection) == before
    assert result["reference"]["task_evidence"][0]["payload"] == {"replacement": []}
    assert result["reference"]["task_evidence"][1]["payload"] == {"replacement": []}


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_subclass_ancestors_keep_existing_copy_semantics(depth):
    class CustomDict(dict):
        pass

    class CustomList(list):
        pass

    projection, slots = _rewrite_fixture("key_shapes", False)
    reference = projection["reference"]
    if depth == 0:
        projection["reference"] = CustomDict(reference)
    elif depth == 1:
        reference["task_evidence"] = CustomList(reference["task_evidence"])
    else:
        reference["task_evidence"][0] = CustomDict(reference["task_evidence"][0])
    expected = deepcopy(projection)
    expected["objects"] = []
    result = codec._copy_for_rewrite(projection, slots)
    assert codec._dump(result) == codec._dump(expected)
    assert type(codec._get(result["reference"], slots[0].path[:depth])) is type(
        codec._get(projection["reference"], slots[0].path[:depth])
    )
    before = codec._dump(projection)
    codec._get(result["reference"], slots[0].path)["rows"].append("changed")
    assert codec._dump(projection) == before


# Captured from the pre-optimization codec at e6c58fc2; order is part of the wire.
WIRE_HASHES = {
    (
        "small_containers",
        False,
    ): "e01a02f3bc0ccd9f7d9609aa21d74a08f5f2ea1c4bc82234aeddcfa088d1287e",
    (
        "small_containers",
        True,
    ): "b65dae34e17111a7f1ec62f7c532a59e25c99f49787008bc92f8db0d58756422",
    (
        "key_shapes",
        False,
    ): "cfacdde2d9cada122ce3a950995c5e6c4f55f8dde6a7e0fe94ba9fd358672987",
    (
        "key_shapes",
        True,
    ): "f5a2f3576b88b64b94dfa669d0fbeb20ff87ed7cec54ac17b6e25c43d4a6c03d",
    (
        "wide_key_strings",
        False,
    ): "bcd97b8bd81d9dade7aaf8d7cd25f964f342e49544580cfdbe98e8578bf3f36b",
    (
        "wide_key_strings",
        True,
    ): "db9e1169ed17d073f5f557b8ad64e58538c8ab58ca257505c2279af4ee1bb8dc",
    (
        "similar_lists",
        False,
    ): "7ef545d08cd3b80bc387b3111868f6d7469409289d1437d2b00fb25ed6ac7549",
    (
        "similar_lists",
        True,
    ): "6958689b02c280e1b99c04eda527b385fda3a878413f3d4c9bec886d87be83b9",
}
