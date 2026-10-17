"""One captured generation checks each shared field definition once."""

from collections import Counter
from types import SimpleNamespace
import hashlib
import json

from graphql import GraphQLEnumValue, build_schema
import pytest

from general_manager.api.graphql import GraphQL
from general_manager.chat import graphql_contract as contract


def _install(monkeypatch, count):
    fields = " ".join(f'value{i}: String = "v{i}"' for i in range(12))
    types = " ".join(
        f"type Item{i} {{ code: String, peer: Item{(i + 1) % count} }} "
        f"type Item{i}Page {{ items: [Item{i}], pageInfo: Info }}"
        for i in range(count)
    )
    roots = " ".join(f"item{i}List(filter: Shared): Item{i}Page" for i in range(count))
    schema = build_schema(
        f"enum State {{ OPEN CLOSED }} "
        f'input Payload {{ labels: [String] = ["base"] }} '
        f"input Shared {{ {fields} state: State = OPEN payload: Payload = {{}} }} "
        f"type Info {{ totalCount: Int }} {types} type Query {{ {roots} }}"
    )
    managers = {
        f"Item{i}": type(f"Item{i}Manager", (), {"chat_exposed": True})
        for i in range(count)
    }
    registry = {name: type(name + "Type", (), {}) for name in managers}
    for name, source in registry.items():
        schema.get_type(name).graphene_type = source
    monkeypatch.setattr(GraphQL, "manager_registry", managers)
    monkeypatch.setattr(GraphQL, "graphql_type_registry", registry)
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=schema)
    )
    return schema, managers


@pytest.fixture(autouse=True)
def clean_generation():
    contract.clear_contract_cache()
    yield
    contract.clear_contract_cache()


@pytest.mark.parametrize("count", [2, 8, 24])
def test_shared_input_field_work_is_independent_of_manager_count(monkeypatch, count):
    schema, _ = _install(monkeypatch, count)
    observed = Counter()
    original = contract._contract_fields

    def fields(named, *args, **kwargs):
        observed[named.name] += 1
        return original(named, *args, **kwargs)

    monkeypatch.setattr(contract, "_contract_fields", fields)
    contract.compile_read_contract(schema)
    for i in range(count):
        full, snapshot = contract.capture_manager_contract(f"Item{i}")
        assert snapshot and full["manager"] == f"Item{i}"
        assert full["types"]["Shared"]["fields"]["value11"] == {
            "type": "String",
            "default": "v11",
            "default_graphql": '"v11"',
        }
        assert full["types"]["Shared"]["fields"]["state"]["default"] == "OPEN"
        assert full["types"][f"Item{(i + 1) % count}"] == {
            "kind": "reference",
            "manager": f"Item{(i + 1) % count}",
        }
    # Original exposure capture plus one inspection of the private frozen graph.
    # Manager-specific references and exports still happen separately.
    assert observed["Shared"] == 2
    assert observed["Payload"] == 2


def test_cached_definition_fields_preserve_export_default_isolation(monkeypatch):
    _install(monkeypatch, 3)
    first, snapshot = contract.capture_manager_contract("Item0")
    first["types"]["Shared"]["fields"]["value0"]["default"] = "forged"
    first["types"]["Payload"]["fields"]["labels"]["default"].append("forged")
    other, _ = contract.capture_manager_contract("Item1")
    again, current = contract.capture_manager_contract("Item0")
    assert current == snapshot
    for full in (other, again):
        assert full["types"]["Shared"]["fields"]["value0"]["default"] == "v0"
        assert full["types"]["Payload"]["fields"]["labels"]["default"] == ["base"]
    first["types"]["Shared"]["fields"]["payload"]["default"]["labels"].append("x")
    assert first["types"]["Payload"]["fields"]["labels"]["default"] == [
        "base",
        "forged",
    ]


def test_definition_field_reuse_is_scoped_to_one_generation(monkeypatch):
    schema, managers = _install(monkeypatch, 3)
    before, snapshot = contract.capture_manager_contract("Item0")
    schema.get_type("Shared").fields["value0"].default_value = "changed"
    schema.get_type("State").values["NEW"] = GraphQLEnumValue("NEW")
    managers["Item1"].chat_exposed = False
    contract.clear_contract_cache()
    after, fresh = contract.capture_manager_contract("Item0")
    assert fresh != snapshot
    assert before["types"]["Shared"]["fields"]["value0"]["default"] == "v0"
    assert after["types"]["Shared"]["fields"]["value0"]["default"] == "changed"
    assert after["types"]["State"]["values"] == ["OPEN", "CLOSED", "NEW"]
    assert "Item1" not in after["types"]
    assert "peer" not in after["types"]["Item0"]["fields"]


def test_shared_field_view_preserves_full_values_and_snapshot_hashes(monkeypatch):
    schema, managers = _install(monkeypatch, 8)
    captured = []
    original = contract._copy_contract_schema

    def copy(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(contract, "_copy_contract_schema", copy)
    compiled = contract.compile_read_contract(schema)
    frozen = captured[0]
    manager_types = {name: frozen.get_type(name) for name in managers}
    exposure = {name: True for name in managers}
    visibility = {value.name: True for value in manager_types.values()}
    for name, summary in compiled.index.items():
        # A separate projection gets a fresh field view, never the shared one.
        expected = contract._manager_contract(
            name, contract._export_public(summary), frozen, manager_types, visibility
        )
        observed, snapshot = contract.capture_manager_contract(name)
        assert observed == expected
        raw = json.dumps(
            {
                "projection_version": contract.INSPECTION_VERSION,
                "generation": contract._GENERATION,
                "manager": name,
                "contract": expected,
                "exposure": exposure,
                "visibility": visibility,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        assert snapshot == hashlib.sha256(raw).hexdigest()
