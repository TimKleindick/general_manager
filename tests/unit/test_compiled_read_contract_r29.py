"""A schema generation owns one public contract graph and detached exports."""

from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from graphql import GraphQLEnumValue, GraphQLSchema, build_schema
import pytest

from general_manager.api.graphql import GraphQL
from general_manager.chat import graphql_contract as contract


@pytest.fixture
def generation(monkeypatch):
    schema = build_schema("""
        enum State { OPEN CLOSED }
        input Filter { state: State = OPEN }
        type Part { code: String!, state: State, other: Other }
        type Other { code: String }
        type Info { totalCount: Int }
        type PartPage { items: [Part!]!, pageInfo: Info! }
        type OtherPage { items: [Other!]!, pageInfo: Info! }
        type Query { partList(filter: Filter, pageSize: Int = 25): PartPage,
                     otherList: OtherPage }
    """)
    managers = {
        name: type(name + "Manager", (), {"chat_exposed": True})
        for name in ("Part", "Other")
    }
    types = {name: type(name + "Type", (), {}) for name in managers}
    for name, source in types.items():
        schema.get_type(name).graphene_type = source
    monkeypatch.setattr(GraphQL, "manager_registry", managers)
    monkeypatch.setattr(GraphQL, "graphql_type_registry", types)
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=schema)
    )
    contract.clear_contract_cache()
    yield schema, managers
    contract.clear_contract_cache()


def test_manager_requests_reuse_one_compiled_graph(generation, monkeypatch):
    copies = []
    original = contract._copy_contract_schema

    def observed_copy(*args, **kwargs):
        copies.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(contract, "_copy_contract_schema", observed_copy)
    part, first = contract.capture_manager_contract("Part")
    other, _ = contract.capture_manager_contract("Other")
    repeated, current = contract.capture_manager_contract("Part")
    assert part == repeated and first == current
    assert part["types"]["Other"] == {"kind": "reference", "manager": "Other"}
    assert other["types"]["Other"]["fields"]["code"] == {"type": "String"}
    assert len(copies) == 1


def test_mutating_an_export_does_not_change_later_exports(generation):
    first, token = contract.capture_manager_contract("Part")
    first["types"]["State"]["values"].append("FORGED")
    first["root_fields"]["partList"]["arguments"]["pageSize"]["default"] = 999
    current, current_token = contract.capture_manager_contract("Part")
    assert current_token == token
    assert current["types"]["State"]["values"] == ["OPEN", "CLOSED"]
    assert current["root_fields"]["partList"]["arguments"]["pageSize"]["default"] == 25


def test_definition_changes_take_effect_only_after_explicit_rebuild(generation):
    schema, _ = generation
    original, token = contract.capture_manager_contract("Part")
    schema.get_type("State").values["NEW"] = GraphQLEnumValue("NEW")
    cached, cached_token = contract.capture_manager_contract("Part")
    assert cached == original and cached_token == token
    contract.clear_contract_cache()
    rebuilt, rebuilt_token = contract.capture_manager_contract("Part")
    assert rebuilt_token != token
    assert rebuilt["types"]["State"]["values"] == ["OPEN", "CLOSED", "NEW"]


def test_explicit_rebuild_invalidates_even_equal_definitions(generation):
    original, token = contract.capture_manager_contract("Part")
    contract.clear_contract_cache()
    rebuilt, rebuilt_token = contract.capture_manager_contract("Part")
    assert rebuilt == original
    assert rebuilt_token != token


def test_reentrant_compilation_is_rejected_without_partial_publication(
    generation, monkeypatch
):
    original = contract._serialize_argument_info

    def reenter(argument):
        contract.capture_manager_contract("Part")
        return original(argument)

    with monkeypatch.context() as nested:
        nested.setattr(contract, "_serialize_argument_info", reenter)
        with pytest.raises(contract.ChatReadContractError, match="compilation"):
            contract.capture_manager_contract("Part")
    assert contract.capture_manager_contract("Part")[0]["manager"] == "Part"


def test_reset_during_compilation_does_not_publish_a_mixed_generation(
    generation, monkeypatch
):
    original = contract._serialize_argument_info
    triggered = False

    def reset(argument):
        nonlocal triggered
        if not triggered:
            triggered = True
            contract.clear_contract_cache()
        return original(argument)

    with monkeypatch.context() as nested:
        nested.setattr(contract, "_serialize_argument_info", reset)
        with pytest.raises(contract.ChatReadContractError, match="interrupted"):
            contract.capture_manager_contract("Part")
    assert contract.capture_manager_contract("Part")[0]["manager"] == "Part"


def test_concurrent_manager_observations_share_one_compilation(generation, monkeypatch):
    original = contract._copy_contract_schema
    copies = []

    def observed_copy(*args, **kwargs):
        copies.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(contract, "_copy_contract_schema", observed_copy)
    managers = ["Part", "Other"] * 4
    with ThreadPoolExecutor(max_workers=8) as executor:
        observations = list(executor.map(contract.capture_manager_contract, managers))
    assert len(copies) == 1
    for manager in ("Part", "Other"):
        selected = [
            value
            for name, value in zip(managers, observations, strict=True)
            if name == manager
        ]
        assert all(value == selected[0] for value in selected)
        assert len({id(value[0]) for value in selected}) == len(selected)


def test_equivalent_native_workers_keep_equal_startup_and_rebuild_snapshots():
    from tests.experiments.test_gm_eval_harness import _child

    program = """
import json
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=5,snapshot='RANK',variant='weight',seed=17)
from general_manager.chat.graphql_contract import capture_manager_contract,clear_contract_cache
before=capture_manager_contract('Customer')[1]
clear_contract_cache()
after=capture_manager_contract('Customer')[1]
print(json.dumps({'before':before,'after':after}))
runtime.close()
"""
    first = _child(program)
    second = _child(program)
    assert first == second
    assert first["before"] != first["after"]


def test_reentrant_other_schema_cannot_publish_a_partial_generation(
    generation, monkeypatch
):
    schema, _ = generation
    other = GraphQLSchema(query=schema.query_type)
    original = contract._serialize_argument_info
    triggered = False
    initial_generation = contract._GENERATION

    def reenter(argument):
        nonlocal triggered
        if not triggered:
            triggered = True
            contract.compile_read_contract(other)
        return original(argument)

    with monkeypatch.context() as nested:
        nested.setattr(contract, "_serialize_argument_info", reenter)
        with pytest.raises(contract.ChatReadContractError, match="Reentrant"):
            contract.compile_read_contract(schema)
    assert schema not in contract._COMPILED
    assert other not in contract._COMPILED
    assert contract._GENERATION == initial_generation
    assert contract.capture_manager_contract("Part")[0]["manager"] == "Part"
    second = contract.compile_read_contract(other)
    assert (
        second.snapshots["Part"]
        != contract.compile_read_contract(schema).snapshots["Part"]
    )
    assert contract._GENERATION == initial_generation + 2


def test_compilation_can_read_an_already_compiled_other_schema(generation, monkeypatch):
    schema, _ = generation
    other = GraphQLSchema(query=schema.query_type)
    cached = contract.compile_read_contract(other)
    original = contract._serialize_argument_info
    observed = []

    def read_cached(argument):
        observed.append(contract.compile_read_contract(other))
        return original(argument)

    monkeypatch.setattr(contract, "_serialize_argument_info", read_cached)
    compiled = contract.compile_read_contract(schema)
    assert observed and all(bundle is cached for bundle in observed)
    assert compiled.snapshots["Part"] != cached.snapshots["Part"]


def test_path_lookup_does_not_materialize_the_public_index(generation, monkeypatch):
    from general_manager.chat.schema_index import find_exposed_path

    contract.capture_manager_contract("Part")

    def reject_public_export(*_args, **_kwargs):
        message = "Path lookup must not detach the whole public index."
        raise AssertionError(message)

    monkeypatch.setattr(contract, "_export_public", reject_public_export)
    assert find_exposed_path("Part", "Other") == ["other"]
    assert find_exposed_path("Other", "Part") is None
    assert find_exposed_path("Part", "Part") == []
    assert find_exposed_path("Unknown", "Part") is None
    assert find_exposed_path("Part", "Unknown") is None


def test_public_index_mutations_cannot_change_internal_paths(generation):
    from general_manager.chat.schema_index import find_exposed_path

    expected = contract.compact_index()
    exported = contract.compact_index()
    assert type(exported) is dict
    assert type(exported["Part"]["relations"]) is list
    exported["Part"]["relations"][0]["path"].append("forged")
    exported["Part"]["relations"][0]["target"] = "Unknown"
    exported["Part"]["roots"].append("forgedRoot")
    exported["Part"]["fields"].append("forgedField")
    assert contract.compact_index() == expected
    assert find_exposed_path("Part", "Other") == ["other"]


def test_mutating_a_returned_path_cannot_change_the_compiled_view(generation):
    from general_manager.chat.schema_index import find_exposed_path

    first = find_exposed_path("Part", "Other")
    assert first == ["other"]
    first.append("forged")
    assert find_exposed_path("Part", "Other") == ["other"]


def test_path_membership_and_walk_use_one_generation_during_rebuild(
    generation, monkeypatch
):
    from general_manager.chat import schema_index

    _, managers = generation
    original = contract._compiled_index_view
    old_snapshot = contract.capture_manager_contract("Part")[1]

    def rebuild_after_entry():
        view = original()
        managers["Other"].chat_exposed = False
        contract.clear_contract_cache()
        return view

    with monkeypatch.context() as nested:
        nested.setattr(schema_index, "_compiled_index_view", rebuild_after_entry)
        assert schema_index.find_exposed_path("Part", "Other") == ["other"]
    assert schema_index.find_exposed_path("Part", "Other") is None
    assert contract.capture_manager_contract("Part")[1] != old_snapshot
