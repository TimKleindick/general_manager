"""Opt-in evidence follows the executable source getter, not field spelling."""

from functools import wraps
from types import SimpleNamespace
from typing import ClassVar

import graphene
import pytest

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_resolvers import (
    create_measurement_resolver,
    create_normal_resolver,
    create_root_list_resolver,
)
from general_manager.api.graphql_errors import MeasurementType
from general_manager.api.graphql_units import attach_unit_contracts
from general_manager.chat.schema_inspection import inspect_manager_schema
from general_manager.chat.tools import query
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta, common_attribute_descriptor
from general_manager.measurement.measurement import Measurement
from general_manager.utils.filter_parser import create_filter_function
from tests.unit.test_chat_category_value_binding_r26 import (
    category_schema as _category_fixture,
    _binding,
    _call,
    _lookup,
    _public_runner,
)
from tests.unit.test_chat_cross_query_scope import (
    public_population as _population_fixture,
    _setup,
)
from tests.unit.test_filter_execution_authority_r26 import _reject_net
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket

category_schema = _category_fixture
public_population = _population_fixture


def _rebuild_category(schema, monkeypatch):
    generated = GraphQL.graphql_type_registry[schema.manager]
    new_schema = graphene.Schema(query=schema.query_type)
    monkeypatch.setattr(GraphQL, "_schema", new_schema)
    attach_unit_contracts(
        new_schema.graphql_schema,
        {schema.manager: GraphQL.manager_registry[schema.manager]},
        {schema.manager: generated},
    )


@pytest.mark.parametrize("damage", ["native", "wraps", "initial", "wrong_common"])
def test_changed_category_getter_cannot_attest_empty_business_population(
    category_schema, monkeypatch, damage
):
    schema = category_schema
    native = GraphQL._schema.graphql_schema
    before = inspect_manager_schema(schema.manager, view="full")["snapshot"]
    field = native.get_type(schema.manager).fields["family"]
    if damage == "initial":
        generated = GraphQL.graphql_type_registry[schema.manager]
        monkeypatch.setattr(
            generated._meta.fields["family"], "resolver", lambda row, _info: row.name
        )
        _rebuild_category(schema, monkeypatch)
    elif damage == "wrong_common":
        monkeypatch.setattr(field, "resolve", create_normal_resolver("name"))
    elif damage == "wraps":
        original = field.resolve

        @wraps(original)
        def altered(row, info):
            return row.name

        monkeypatch.setattr(field, "resolve", altered)
    else:
        monkeypatch.setattr(field, "resolve", lambda row, _info: row.name)
    ordinary = query(
        manager=schema.manager,
        filters={},
        fields=["id", "family"],
        context=schema.context,
    )
    assert ordinary["complete"] is True
    assert ordinary["data"][0] == {"id": 1, "family": "Display Alpha"}
    assert inspect_manager_schema(schema.manager, view="full")["snapshot"] != before
    runner = _public_runner(schema.manager)
    _, valid = _lookup(runner, schema)
    if valid:
        calls_before = len(schema.calls)
        result, valid = _call(
            runner,
            "query",
            manager=schema.manager,
            fields=["id", "family"],
            filters={"family_Exact": "Display Alpha"},
            category_bindings=[_binding()],
        )
        assert len(schema.calls) == calls_before
        assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid


def test_old_category_lookup_and_details_cannot_survive_getter_change(
    category_schema, monkeypatch
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    observed, valid = _lookup(runner, schema)
    assert valid and observed["data"][0]["family"] == schema.actual
    field = GraphQL._schema.graphql_schema.get_type(schema.manager).fields["family"]
    monkeypatch.setattr(field, "resolve", lambda row, _info: row.name)
    before = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert not valid
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert len(schema.calls) == before


@pytest.mark.parametrize(
    "type_name,field_name,attribute",
    [
        ("GrossType", "id", "id"),
        ("GrossType", "amount", "amount"),
        ("GrossType", "eventDate", "event_date"),
        ("GrossType", "unitLabel", "unit_label"),
        ("GrossType", "subject", "subject"),
        ("SubjectType", "id", "id"),
    ],
)
def test_cross_query_revalidates_all_output_source_paths(
    public_population, monkeypatch, type_name, field_name, attribute
):
    runner, runtime, binding = _setup(public_population)
    manager = "Subject" if type_name == "SubjectType" else "Gross"
    before = inspect_manager_schema(manager, view="full")["snapshot"]
    field = public_population[0].graphql_schema.get_type(type_name).fields[field_name]
    monkeypatch.setattr(field, "resolve", lambda row, _info: getattr(row, attribute))
    assert inspect_manager_schema(manager, view="full")["snapshot"] != before
    _reject_net(runner, runtime, binding)
    _reject_net(*_setup(public_population))


@pytest.mark.parametrize("path", ["items", "pageInfo", "totalCount"])
def test_custom_list_envelope_getter_revokes_common_population(
    public_population, monkeypatch, path
):
    schema = public_population[0].graphql_schema
    runner, runtime, binding = _setup(public_population)
    before = inspect_manager_schema("Gross", view="full")["snapshot"]
    root = schema.query_type.fields["grossList"]
    from graphql import get_named_type

    page = get_named_type(root.type)
    field = (
        get_named_type(page.fields["pageInfo"].type).fields["totalCount"]
        if path == "totalCount"
        else page.fields[path]
    )
    original = field.resolve
    monkeypatch.setattr(
        field, "resolve", lambda *args, **kwargs: original(*args, **kwargs)
    )
    assert inspect_manager_schema("Gross", view="full")["snapshot"] != before
    _reject_net(runner, runtime, binding)


def test_common_attribute_resolver_keeps_observed_category_binding(
    category_schema, monkeypatch
):
    schema = category_schema
    generated = GraphQL.graphql_type_registry[schema.manager]
    monkeypatch.setattr(
        generated._meta.fields["family"], "resolver", create_normal_resolver("family")
    )
    _rebuild_category(schema, monkeypatch)
    runner = _public_runner(schema.manager)
    observed, valid = _lookup(runner, schema)
    assert valid and observed["data"][0]["family"] == schema.actual
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert valid and result["complete"] is True
    assert [row["id"] for row in result["data"]] == [1, 2]


@pytest.fixture
def real_category_source(category_schema, monkeypatch):
    """Hydrate actual managers through their shared descriptor implementation."""
    schema = category_schema
    manager = GraphQL.manager_registry[schema.manager]
    interface = manager.Interface

    class SourceRows(SimpleBucket):
        def __iter__(self):
            return iter(self._manager_class(id=row.id) for row in self._data)

        def __getitem__(self, item):
            if isinstance(item, slice):
                return SourceRows(self._manager_class, self._data[item])
            return self._manager_class(id=self._data[item].id)

        def filter(self, **kwargs):
            return SourceRows(
                self._manager_class,
                [
                    row
                    for row in self._data
                    if all(
                        create_filter_function(key, value)(row)
                        for key, value in kwargs.items()
                    )
                ],
            )

    def attributes(cls):
        return {
            field: lambda instance, name=field: next(
                row[name]
                for row in schema.rows
                if row["id"] == instance.identification["id"]
            )
            for field in ("id", "name", "family", "rank")
        }

    def filtered(cls, **kwargs):
        schema.calls.append(kwargs)
        return SourceRows(
            cls._parent_class, [SimpleNamespace(**row) for row in schema.rows]
        ).filter(**kwargs)

    monkeypatch.setattr(interface, "get_attributes", classmethod(attributes))
    monkeypatch.setattr(interface, "filter", classmethod(filtered))
    GeneralManagerMeta.ensure_attributes_initialized(manager)
    generated = GraphQL.graphql_type_registry[schema.manager]
    for name in ("id", "name", "family", "rank"):
        monkeypatch.setattr(
            generated._meta.fields[name], "resolver", create_normal_resolver(name)
        )
    monkeypatch.setattr(
        schema.query_type._meta.fields["exposed_items"],
        "resolver",
        create_root_list_resolver(manager),
    )
    _rebuild_category(schema, monkeypatch)
    return schema, manager


@pytest.mark.parametrize(
    "damage",
    [
        "property",
        "attribute_source",
        "attribute_source_defaults",
        "getattribute",
        "descriptor_selector",
    ],
)
def test_real_manager_source_change_invalidates_complete_category_proof(
    real_category_source, monkeypatch, damage
):
    schema, manager = real_category_source
    runner = _public_runner(schema.manager)
    result, valid = _lookup(runner, schema)
    assert valid and result["data"][0]["family"] == schema.actual
    before = inspect_manager_schema(schema.manager, view="full")["snapshot"]
    if damage == "property":
        monkeypatch.setattr(manager, "family", property(lambda row: row.name))
    elif damage == "attribute_source":
        monkeypatch.setitem(manager._attributes, "family", manager._attributes["name"])
    elif damage == "attribute_source_defaults":
        monkeypatch.setattr(manager._attributes["family"], "__defaults__", ("name",))
    elif damage == "getattribute":
        original = manager.__getattribute__
        monkeypatch.setattr(
            manager,
            "__getattribute__",
            lambda row, name: original(row, "name" if name == "family" else name),
        )
    else:
        descriptor = vars(manager)["family"]
        monkeypatch.setattr(descriptor, "_attr_name", "name")
    assert inspect_manager_schema(schema.manager, view="full")["snapshot"] != before
    ordinary = query(
        manager=schema.manager,
        filters={},
        fields=["id", "family"],
        context=schema.context,
    )
    assert ordinary["data"][0] == {"id": 1, "family": "Display Alpha"}
    before_calls = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert not valid and result == {
        "status": "error",
        "code": "invalid_category_binding",
    }
    assert len(schema.calls) == before_calls


def test_real_manager_shared_descriptor_keeps_business_category_population(
    real_category_source,
):
    schema, _manager = real_category_source
    runner = _public_runner(schema.manager)
    _, valid = _lookup(runner, schema)
    assert valid
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert valid and result["complete"] is True
    assert [row["id"] for row in result["data"]] == [1, 2]


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "nested_getter",
        "nested_codec",
        "conversion_code",
        "payload_code",
        "resolver_default",
    ],
)
def test_shared_measurement_output_keeps_source_and_public_conversion(
    category_schema, monkeypatch, damage
):
    schema = category_schema
    interface = GraphQL.manager_registry[schema.manager].Interface
    original = interface.get_attribute_types
    monkeypatch.setattr(
        interface,
        "get_attribute_types",
        classmethod(lambda _cls: {**original(), "weight": {"type": Measurement}}),
    )
    for row in schema.rows:
        row["weight"] = Measurement(2, "kg")
    generated = GraphQL.graphql_type_registry[schema.manager]
    monkeypatch.setitem(
        generated._meta.fields,
        "weight",
        graphene.Field(
            MeasurementType,
            target_unit=graphene.String(),
            resolver=create_measurement_resolver("weight"),
        ),
    )
    _rebuild_category(schema, monkeypatch)
    current = inspect_manager_schema(schema.manager, view="full")
    assert current["types"][schema.manager]["fields"]["weight"]["value_contract"] == {
        "source": "interface_attribute",
        "field": "weight",
        "transform": "measurement",
    }
    ordinary = query(
        manager=schema.manager,
        filters={},
        fields=["id", {"weight": ["value", "unit"]}],
        context=schema.context,
    )
    assert ordinary["complete"] is True
    assert ordinary["data"][0]["weight"]["value"] == 2
    assert ordinary["data"][0]["weight"]["unit"] == "kilogram"
    if damage is not None:
        from general_manager.api import graphql_resolvers
        from graphql import get_named_type

        if damage == "resolver_default":
            target = (
                GraphQL._schema.graphql_schema.get_type(schema.manager)
                .fields["weight"]
                .resolve
            )
            monkeypatch.setattr(target, "__defaults__", ("g",))
        elif damage in ("conversion_code", "payload_code"):

            def changed(value, target_unit=None):
                return {"value": 999, "unit": "kilogram"}

            target = (
                graphql_resolvers.resolve_measurement_output
                if damage == "conversion_code"
                else graphql_resolvers.measurement_to_graphql_payload
            )
            monkeypatch.setattr(target, "__code__", changed.__code__)
        else:
            field = GraphQL._schema.graphql_schema.get_type(schema.manager).fields[
                "weight"
            ]
            value = get_named_type(field.type).fields["value"]
            target = value if damage == "nested_getter" else get_named_type(value.type)
            operation = "resolve" if damage == "nested_getter" else "serialize"
            original_operation = getattr(target, operation)
            monkeypatch.setattr(
                target,
                operation,
                lambda *args, **kwargs: original_operation(*args, **kwargs),
            )
        changed = inspect_manager_schema(schema.manager, view="full")
        assert changed["snapshot"] != current["snapshot"]
        assert (
            "value_contract" not in changed["types"][schema.manager]["fields"]["weight"]
        )


@pytest.mark.parametrize("shape", ["plain", "cycle", "deep", "opaque", "shared"])
def test_lazy_descriptor_preserves_backend_default_without_unbounded_proof(
    monkeypatch, shape
):
    for name in (
        "all_classes",
        "pending_graphql_interfaces",
        "pending_attribute_initialization",
    ):
        monkeypatch.setattr(
            GeneralManagerMeta, name, list(getattr(GeneralManagerMeta, name))
        )
    payload = {}
    if shape == "cycle":
        payload["self"] = payload
    elif shape == "opaque":
        payload["backend"] = object()
    elif shape == "shared":
        for _ in range(14):
            payload = [payload, payload]
    elif shape == "deep":
        for _ in range(2000):
            payload = {"child": payload}

    def family_value(_interface, backend_context=payload):
        return "class-x"

    class SourceInterface(BaseTestInterface):
        input_fields: ClassVar = {"id": Input(int)}

        @classmethod
        def get_attributes(cls):
            return {"family": family_value}

        @classmethod
        def get_attribute_types(cls):
            return {"family": {"type": str}}

    class Inventory(GeneralManager):
        Interface = SourceInterface

    assert Inventory(id=1).family == "class-x"
    descriptor = vars(Inventory)["family"]
    assert common_attribute_descriptor(Inventory, "family", descriptor) is (
        shape == "plain"
    )


@pytest.mark.parametrize("helper", ["permission", "as_of"])
def test_same_output_helper_function_with_changed_code_revokes_old_category(
    category_schema, monkeypatch, helper
):
    from general_manager.api import graphql_resolvers

    schema = category_schema
    generated = GraphQL.graphql_type_registry[schema.manager]
    monkeypatch.setattr(
        generated._meta.fields["family"], "resolver", create_normal_resolver("family")
    )
    _rebuild_category(schema, monkeypatch)
    runner = _public_runner(schema.manager)
    _, valid = _lookup(runner, schema)
    assert valid
    before = inspect_manager_schema(schema.manager, view="full")["snapshot"]
    if helper == "permission":

        def changed(instance, info, field_name, value_factory):
            return instance.name if field_name == "family" else value_factory()

        target = graphql_resolvers.resolve_with_read_permission
    else:

        def changed(value):
            return None

        target = graphql_resolvers._ensure_as_of_compatible
    monkeypatch.setattr(target, "__code__", changed.__code__)
    assert inspect_manager_schema(schema.manager, view="full")["snapshot"] != before
    before_calls = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert not valid and result == {
        "status": "error",
        "code": "invalid_category_binding",
    }
    assert len(schema.calls) == before_calls
