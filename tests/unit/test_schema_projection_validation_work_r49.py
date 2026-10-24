"""Repeated schema names must not repeat JSON work or weaken logical limits."""

import importlib
from copy import deepcopy

import pytest


def codec():
    return importlib.import_module("general_manager.chat.planned.schema_projection")


def test_repeated_schema_names_have_bounded_serialization_work(monkeypatch):
    api = codec()
    original = api._dump
    serialized_strings = []

    def observed(value):
        if type(value) is str:
            serialized_strings.append(value)
        return original(value)

    monkeypatch.setattr(api, "_dump", observed)
    fields = [{"field": "String", "type": "String"} for _ in range(1000)]
    api._bounded(fields)
    # Three distinct immutable strings describe all 1,000 synthetic fields.
    assert len(serialized_strings) <= 3
    assert set(serialized_strings) == {"field", "type", "String"}


@pytest.mark.parametrize("budget", ["characters", "nodes", "depth"])
def test_repeated_shared_fields_still_consume_every_logical_limit(monkeypatch, budget):
    api = codec()
    field = {"field": "String"}
    fields = [field] * 4
    # Logical size: list 6 + four dicts of (2 + key 7 + separator 2 + value 8).
    api._bounded(fields, max_chars=82)
    if budget == "characters":
        with pytest.raises(ValueError, match="invalid_schema_projection"):
            api._bounded(fields, max_chars=81)
    elif budget == "nodes":
        monkeypatch.setattr(api, "MAX_NODES", 9)
        api._bounded(fields)
        monkeypatch.setattr(api, "MAX_NODES", 8)
        with pytest.raises(ValueError, match="invalid_schema_projection"):
            api._bounded(fields)
    else:
        monkeypatch.setattr(api, "MAX_DEPTH", 2)
        api._bounded(fields)
        monkeypatch.setattr(api, "MAX_DEPTH", 1)
        with pytest.raises(ValueError, match="invalid_schema_projection"):
            api._bounded(fields)


def test_each_validation_rechecks_mutated_schema_data_and_string_lengths(monkeypatch):
    api = codec()
    original = api._dump
    serialized_strings = []

    def observed(value):
        if type(value) is str:
            serialized_strings.append(value)
        return original(value)

    monkeypatch.setattr(api, "_dump", observed)
    field = {"field": "String"}
    api._bounded(field, max_chars=19)
    serialized_strings.clear()
    api._bounded(field, max_chars=19)
    assert serialized_strings == ["field", "String"]
    field["field"] = "String\n"
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        api._bounded(field, max_chars=19)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), object()])
def test_repeated_string_work_does_not_admit_unsupported_scalar_values(value):
    with pytest.raises(ValueError):
        codec()._bounded([{"field": "String"}, {"field": value}])


def test_repeated_string_work_does_not_admit_non_string_keys():
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        codec()._bounded([{"field": "String"}, {1: "String"}])


@pytest.mark.parametrize("version", ["gm.reference-data/2", "gm.reference-data/3"])
def test_string_only_pooling_does_not_serialize_containers_for_string_matching(
    monkeypatch, version
):
    api = codec()
    rows = [
        {
            "label": "common schema-type-value",
            "children": {"label": "common schema-type-value"},
        }
        for _ in range(50)
    ]
    reference = {"rows": rows}
    original = deepcopy(reference)
    source_containers = (
        {id(reference), id(rows)}
        | {id(row) for row in rows}
        | {id(row["children"]) for row in rows}
    )
    original_dump = api._dump
    encoded_source_containers = []

    def observed(value):
        if id(value) in source_containers:
            encoded_source_containers.append(value)
        return original_dump(value)

    monkeypatch.setattr(api, "_dump", observed)
    projection = {"format": version, "objects": [], "reference": reference}
    slots = (api.SchemaSlot((), "reference_data", "{}"),)
    result = api._share_small_containers(projection, slots, strings_only=True)
    assert reference == original
    assert api._transport_size(result) < api._transport_size(projection)
    # A dictionary/list JSON representation cannot equal a selected JSON string.
    assert encoded_source_containers == []
