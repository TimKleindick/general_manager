"""Targeted contract capture avoids work unrelated to its public definition."""

import sys

from graphql import get_named_type

from general_manager.api import (
    graphql_filter_contract,
    graphql_search,
    graphql_value_contract,
)
from general_manager.api.graphql import GraphQL
from general_manager.chat import graphql_contract as contract
from tests.unit.test_schema_inspection import exposed_schema as _exposed_fixture
from tests.unit.test_chat_category_value_binding_r26 import (
    category_schema as _category_fixture,
)

exposed_schema = _exposed_fixture
category_schema = _category_fixture


def test_capture_does_not_enumerate_foreign_manager_output_fields(
    exposed_schema, monkeypatch
):
    schema, _other = exposed_schema
    expected = contract.capture_manager_contract("Part")

    class UnobservedFields(dict):
        def items(self):
            message = "A manager reference must not expand foreign output definitions"
            raise AssertionError(message)

    other = schema.get_type("Other")
    monkeypatch.setattr(other, "fields", UnobservedFields(other.fields))
    assert contract.capture_manager_contract("Part") == expected


def test_non_identifier_predicates_do_not_request_identity_conversion(category_schema):
    schema = GraphQL._schema.graphql_schema
    root = schema.query_type.fields["exposedItems"]
    native = get_named_type(root.args["filter"].type)
    calls = []
    converter_code = graphql_search._identity_filter_converter.__code__

    def record(frame, event, _arg):
        if event == "call" and frame.f_code is converter_code:
            calls.append(frame.f_locals["attribute_name"])

    previous = sys.getprofile()
    try:
        sys.setprofile(record)
        observed = graphql_filter_contract.public_filter_contracts(native)
    finally:
        sys.setprofile(previous)
    assert observed
    assert not calls


def test_one_output_observation_uses_one_consistent_source_signature(category_schema):
    native = GraphQL._schema.graphql_schema.get_type(category_schema.manager)
    owners = []
    signature_code = graphql_search.filter_source_signature.__code__

    def record(frame, event, _arg):
        if event == "call" and frame.f_code is signature_code:
            owners.append(frame.f_locals["field_type"])

    previous = sys.getprofile()
    try:
        sys.setprofile(record)
        observed = graphql_value_contract.public_value_contracts(native)
    finally:
        sys.setprofile(previous)
    assert {value["field"] for value in observed.values()} == {
        "id",
        "name",
        "family",
        "rank",
    }
    assert owners == [GraphQL.manager_registry[category_schema.manager]]


def test_each_output_observation_rechecks_changed_source_inputs(
    category_schema, monkeypatch
):
    native = GraphQL._schema.graphql_schema.get_type(category_schema.manager)
    expected = graphql_value_contract.public_value_contracts(native)
    assert expected
    owner = GraphQL.manager_registry[category_schema.manager]
    with monkeypatch.context() as change:
        change.setattr(owner.Interface.input_fields["id"], "type", str)
        assert not graphql_value_contract.public_value_contracts(native)
    assert graphql_value_contract.public_value_contracts(native) == expected
