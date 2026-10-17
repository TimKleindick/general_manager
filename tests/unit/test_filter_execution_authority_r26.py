"""Generated predicate authority follows actual coercion and relation meaning."""

from general_manager.chat.graphql_contract import clear_contract_cache
from copy import deepcopy
from datetime import date
from enum import Enum
from types import SimpleNamespace

import pytest
import graphene
from django.db import models
from graphql import GraphQLInputField, GraphQLNonNull

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_search import create_filter_options
from general_manager.api.graphql_units import attach_unit_contracts
from general_manager.chat.planned.calculation_scope import (
    validate_calculation_binding_evidence,
)
from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate_evidence,
)
from general_manager.chat.schema_inspection import inspect_manager_schema
from general_manager.manager.input import Input
from tests.unit.test_chat_cross_query_scope import (
    public_population as _population_fixture,
    _setup,
)
from tests.unit.test_chat_category_value_binding_r26 import (
    category_schema as _category_fixture,
    _public_runner,
    _lookup,
    _call,
    _binding,
)

public_population = _population_fixture
category_schema = _category_fixture


@pytest.fixture
def identity_category_schema(category_schema, monkeypatch):
    """Exercise a public ID attribute with its own declared constructor input."""
    manager = GraphQL.manager_registry[category_schema.manager]
    interface = manager.Interface
    original_attributes = interface.get_attribute_types

    def attributes(cls):
        value = deepcopy(original_attributes())
        value["family"]["is_identifier"] = True
        return value

    family_input = Input(str)
    monkeypatch.setattr(interface, "get_attribute_types", classmethod(attributes))
    monkeypatch.setattr(
        interface, "input_fields", {"id": Input(int), "family": family_input}
    )
    _install_category_schema(category_schema, monkeypatch, family=graphene.ID())
    return category_schema, family_input, interface


def _install_category_schema(category_schema, monkeypatch, **identity_fields):
    manager = GraphQL.manager_registry[category_schema.manager]
    filters = create_filter_options(manager, {}, GraphQL._map_field_to_graphene_read)
    item = type(
        category_schema.manager,
        (graphene.ObjectType,),
        {
            "id": graphene.Int(),
            "name": graphene.String(),
            "family": graphene.String(),
            "rank": graphene.Int(),
            **identity_fields,
        },
    )
    page_info = type(
        "IdentityPageInfo",
        (graphene.ObjectType,),
        {"total_count": graphene.Int(required=True)},
    )
    page = type(
        "IdentityPage",
        (graphene.ObjectType,),
        {
            "items": graphene.List(item, required=True),
            "pageInfo": graphene.Field(page_info, required=True),
        },
    )
    from general_manager.api.graphql_resolvers import create_root_list_resolver

    root = type(
        "IdentityCategoryQuery",
        (graphene.ObjectType,),
        {
            "exposed_items": graphene.Field(
                page,
                filter=filters(),
                page_size=graphene.Int(),
                page=graphene.Int(),
                resolver=create_root_list_resolver(manager),
            )
        },
    )
    schema = graphene.Schema(query=root)
    monkeypatch.setattr(GraphQL, "_schema", schema)
    monkeypatch.setattr(
        GraphQL, "graphql_type_registry", {category_schema.manager: item}
    )
    attach_unit_contracts(
        schema.graphql_schema,
        {category_schema.manager: manager},
        {category_schema.manager: item},
    )


@pytest.mark.parametrize("native_type", [int, str])
@pytest.mark.parametrize("input_kind", ["missing", "matching", "mismatched"])
def test_native_identity_filter_requires_matching_declared_input_type(
    category_schema, monkeypatch, native_type, input_kind
):
    manager = GraphQL.manager_registry[category_schema.manager]
    interface = manager.Interface
    original = interface.get_attribute_types

    def attributes(cls):
        value = deepcopy(original())
        value["family"] = {"type": native_type, "is_identifier": True}
        return value

    monkeypatch.setattr(interface, "get_attribute_types", classmethod(attributes))
    inputs = {"id": Input(int)}
    if input_kind != "missing":
        input_type = (
            native_type
            if input_kind == "matching"
            else (str if native_type is int else int)
        )
        inputs["family"] = Input(input_type)
    monkeypatch.setattr(interface, "input_fields", inputs)
    assert getattr(interface, "_model", None) is None
    for row in category_schema.rows:
        row["family"] = native_type(1 if row["id"] in (1, 2) else 2)
    category_schema.actual = "1"
    _install_category_schema(category_schema, monkeypatch, family=graphene.ID())
    runner = _public_runner(category_schema.manager)
    lookup, valid = _lookup(runner, category_schema)
    assert valid and lookup["complete"] is True
    assert [row["family"] for row in lookup["data"]] == ["1", "1", "2"]
    current = inspect_manager_schema(category_schema.manager, view="full")
    definition = next(
        entry["fields"]["family_Exact"]
        for entry in current["types"].values()
        if "family_Exact" in entry.get("fields", {})
    )
    assert ("filter_contract" in definition) is (input_kind == "matching")
    calls = len(category_schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=category_schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": "1"},
        category_bindings=[_binding()],
    )
    if input_kind == "matching":
        assert valid and result["data"] == [
            {"id": 1, "family": "1"},
            {"id": 2, "family": "1"},
        ]
        assert len(category_schema.calls) > calls
    else:
        assert not valid and result == {
            "status": "error",
            "code": "invalid_category_binding",
        }
        assert len(category_schema.calls) == calls


@pytest.mark.parametrize(
    "damage",
    [
        "normalizer",
        "input_replacement",
        "cast",
        "normalize",
        "cast_code",
        "normalize_code",
        "keyword_default",
        "input_type",
    ],
)
def test_non_id_identity_input_drift_revokes_observed_category_filter(
    identity_category_schema, monkeypatch, damage
):
    schema, family_input, interface = identity_category_schema
    runner = _public_runner(schema.manager)
    lookup, valid = _lookup(runner, schema)
    assert valid and lookup["complete"] is True
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert valid and [row["id"] for row in result["data"]] == [1, 2]
    before = inspect_manager_schema(schema.manager, view="full")["snapshot"]

    def changed(self, value, identification=None, *, cache_context=None):
        return "other"

    if damage == "normalizer":
        monkeypatch.setattr(family_input, "normalizer", lambda _value: "other")
    elif damage == "input_replacement":
        monkeypatch.setitem(
            interface.input_fields,
            "family",
            Input(str, normalizer=lambda _value: "other"),
        )
    elif damage in {"cast", "normalize"}:
        monkeypatch.setattr(family_input, damage, lambda _value, **_kwargs: "other")
    elif damage in {"cast_code", "normalize_code"}:
        monkeypatch.setattr(
            getattr(Input, damage.removesuffix("_code")), "__code__", changed.__code__
        )
    elif damage == "keyword_default":
        monkeypatch.setitem(
            Input.cast.__kwdefaults__, "cache_context", {"changed": True}
        )
    else:
        monkeypatch.setattr(family_input, "type", int)

    clear_contract_cache()
    assert inspect_manager_schema(schema.manager, view="full")["snapshot"] != before
    calls = len(schema.calls)
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
    assert len(schema.calls) == calls


@pytest.mark.parametrize("damage", [False, True])
@pytest.mark.parametrize("converter_kind", ["backend", "undeclared_input"])
def test_undeclared_raw_reference_conversion_cannot_attest_category_filter(
    category_schema, monkeypatch, damage, converter_kind
):
    manager = GraphQL.manager_registry[category_schema.manager]
    interface = manager.Interface
    target = type(
        "IdentityTarget"
        + category_schema.manager
        + converter_kind
        + ("Drift" if damage else "Stable"),
        (models.Model,),
        {
            "__module__": __name__,
            "code": models.CharField(primary_key=True, max_length=32),
            "Meta": type("Meta", (), {"app_label": "general_manager"}),
        },
    )
    raw = type(
        "IdentityReference"
        + category_schema.manager
        + converter_kind
        + ("Drift" if damage else "Stable"),
        (models.Model,),
        {
            "__module__": __name__,
            "family": models.ForeignKey(target, on_delete=models.CASCADE),
            "Meta": type("Meta", (), {"app_label": "general_manager"}),
        },
    )
    target_field = raw._meta.get_field("family_id").target_field
    if converter_kind == "undeclared_input":
        # A backend may wrap a standard Input without declaring it as the
        # manager's constructor input. Its hidden state has no captured proof.
        monkeypatch.setattr(target_field, "to_python", Input(str).cast)
    monkeypatch.setattr(interface, "_model", raw, raising=False)
    original = interface.get_attribute_types

    def attributes(cls):
        value = deepcopy(original())
        value["family_id"] = {
            "type": str,
            "is_identifier": True,
            "filter_lookup": "family_id",
        }
        return value

    monkeypatch.setattr(interface, "get_attribute_types", classmethod(attributes))
    assert "family_id" not in interface.input_fields
    for row in category_schema.rows:
        row["family_id"] = row["family"]
    _install_category_schema(category_schema, monkeypatch, family_id=graphene.ID())
    full = inspect_manager_schema(category_schema.manager, view="full")
    definitions = [
        value["fields"]
        for value in full["types"].values()
        if "familyId_Exact" in value.get("fields", {})
    ]
    assert len(definitions) == 1
    assert "filter_contract" not in definitions[0]["familyId_Exact"]
    assert definitions[0]["family_Exact"]["filter_contract"]["field"] == "family"
    # Ordinary GraphQL execution retains the production ORM conversion path.
    from general_manager.chat.tools import query

    result = query(
        manager=category_schema.manager,
        fields=["id", "familyId"],
        filters={"familyId_Exact": category_schema.actual},
        context=category_schema.context,
    )
    assert [row["id"] for row in result["data"]] == [1, 2]
    runner = _public_runner(category_schema.manager)
    _call(runner, "get_manager_schema", manager=category_schema.manager, view="full")
    lookup, valid = _call(
        runner,
        "query",
        manager=category_schema.manager,
        fields=["id", "familyId"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    assert valid and lookup["complete"] is True
    if damage:
        monkeypatch.setattr(target_field, "to_python", lambda _value: "other")
    calls = len(category_schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=category_schema.manager,
        fields=["id", "familyId"],
        filters={"familyId_Exact": category_schema.actual},
        category_bindings=[
            _binding(field="familyId", path=["filters", "familyId_Exact"])
        ],
    )
    assert not valid and result == {
        "status": "error",
        "code": "invalid_category_binding",
    }
    assert len(category_schema.calls) == calls


def _reject_net(runner, runtime, binding):
    with pytest.raises(CalculationError):
        validate_calculation_binding_evidence(
            runtime.task, "net", binding, runner.evidence
        )
    with pytest.raises(CalculationError):
        calculate_evidence(
            "invalid-net",
            "t",
            "difference",
            [CalculationOperand(name, ("value",)) for name in ("gross", "returns")],
            runner.evidence,
            require_linked=True,
            binding=binding,
        )


@pytest.mark.parametrize(
    "damage",
    [
        "parse_value",
        "parse_literal",
        "serialize",
        "out_name",
        "out_type",
        "container",
        "field_identity",
        "type_wrapper",
        "default",
        "root_argument",
    ],
)
def test_changed_coercion_revokes_snapshot_and_calculation_admission(
    public_population, monkeypatch, damage
):
    schema, _managers, rows = public_population
    native = schema.graphql_schema
    before = inspect_manager_schema("Gross", view="full")
    existing = _setup(public_population) if damage == "root_argument" else None
    input_type = native.get_type("GrossFilterTypeDepth1")
    field = input_type.fields["eventDate_Gte"]
    scalar = native.get_type("Date")
    if damage in {"parse_value", "parse_literal", "serialize"}:
        original = getattr(scalar, damage)
        monkeypatch.setattr(
            scalar, damage, lambda *args, **kwargs: original(*args, **kwargs)
        )
    elif damage == "out_name":
        rows["Gross"].append(
            {
                **deepcopy(rows["Gross"][0]),
                "id": "boundary",
                "amount": 200,
                "event_date": date(2025, 1, 1),
            }
        )
        monkeypatch.setattr(field, "out_name", "event_date__gt")
    elif damage == "out_type":
        original = input_type.out_type
        monkeypatch.setattr(input_type, "out_type", lambda values: original(values))
    elif damage == "container":
        original = input_type.out_type.__init__
        monkeypatch.setattr(
            input_type.out_type,
            "__init__",
            lambda self, *args, **kwargs: original(self, *args, **kwargs),
        )
    elif damage == "field_identity":
        input_type.fields["eventDate_Gte"] = GraphQLInputField(
            field.type, out_name=field.out_name, extensions=dict(field.extensions)
        )
    elif damage == "type_wrapper":
        monkeypatch.setattr(field, "type", GraphQLNonNull(field.type))
    elif damage == "default":
        monkeypatch.setattr(field, "default_value", date(2025, 2, 1))
    else:
        argument = native.query_type.fields["grossList"].args["pageSize"]
        monkeypatch.setattr(argument, "out_name", "page")
    clear_contract_cache()
    after = inspect_manager_schema("Gross", view="full")
    assert after["snapshot"] != before["snapshot"]
    runner, runtime, binding = existing or _setup(public_population)
    _reject_net(runner, runtime, binding)


@pytest.mark.parametrize(
    "damage", ["out_name", "out_type", "parse_value", "parse_literal", "serialize"]
)
def test_category_business_filter_cannot_bind_changed_execution(
    category_schema, monkeypatch, damage
):
    schema = category_schema
    native = GraphQL._schema.graphql_schema
    before = inspect_manager_schema(schema.manager, view="full")
    root = native.query_type.fields["exposedItems"]
    input_type = root.args["filter"].type
    if damage == "out_name":
        monkeypatch.setattr(
            input_type.fields["family_Exact"], "out_name", "name__exact"
        )
    elif damage == "out_type":
        original = input_type.out_type
        monkeypatch.setattr(input_type, "out_type", lambda values: original(values))
    else:
        scalar = native.get_type("String")
        original = getattr(scalar, damage)
        monkeypatch.setattr(
            scalar, damage, lambda *args, **kwargs: original(*args, **kwargs)
        )
    clear_contract_cache()
    assert (
        inspect_manager_schema(schema.manager, view="full")["snapshot"]
        != before["snapshot"]
    )
    runner = _public_runner(schema.manager)
    _call(runner, "get_manager_schema", manager=schema.manager, view="full")
    # A canonical unfiltered root may still supply values when only one field's
    # coercion changed; binding its altered predicate must always fail closed.
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["family"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    if valid:
        result, valid = _call(
            runner,
            "query",
            manager=schema.manager,
            fields=["id", "family"],
            filters={"family_Exact": schema.actual},
            category_bindings=[_binding()],
        )
    assert (
        result == {"status": "error", "code": "invalid_category_binding"} and not valid
    )


def test_live_common_relation_lookup_drift_revokes_generated_authority(
    public_population, monkeypatch
):
    _schema, managers, rows = public_population
    before = inspect_manager_schema("Gross", view="full")
    rows["Gross"][0]["alternate"] = SimpleNamespace(id="p1", name="Alpha")
    rows["Gross"].append(
        {
            **deepcopy(rows["Gross"][0]),
            "id": "g2",
            "amount": 200,
            "alternate": SimpleNamespace(id="p2", name="Other"),
        }
    )
    interface = managers["Gross"].Interface
    original = interface.get_attribute_types

    def changed(cls):
        attributes = original()
        if cls is interface:
            attributes["subject"] = {
                **attributes["subject"],
                "filter_lookup": "alternate",
            }
        return attributes

    monkeypatch.setattr(interface, "get_attribute_types", classmethod(changed))
    clear_contract_cache()
    assert (
        inspect_manager_schema("Gross", view="full")["snapshot"] != before["snapshot"]
    )
    _reject_net(*_setup(public_population))


@pytest.mark.parametrize("public_population", ["different_namespace"], indirect=True)
def test_filter_relation_target_must_equal_each_exposed_output_hop(public_population):
    schema, _managers, _rows = public_population
    schema.graphql_schema.get_type("GrossType").fields[
        "subject"
    ].type = schema.graphql_schema.get_type("OtherSubjectType")
    runner, runtime, binding = _setup(public_population)
    mapping = binding.as_mapping()
    selectors = mapping["cross_query"]
    index = selectors["schema_requirement_ids"].index("subject_schema")
    selectors["schema_requirement_ids"].pop(index)
    selectors["schema_evidence_ids"].pop(index)
    from general_manager.chat.planned.models import CalculationBinding

    _reject_net(runner, runtime, CalculationBinding.from_mapping(mapping))


@pytest.fixture
def null_default_population(request, monkeypatch):
    original = GraphQL._map_field_to_graphene_read
    source_calls = 0

    def with_null_default(field_type, field_name, field_info=None):
        nonlocal source_calls
        if field_name == "source":
            source_calls += 1
            if source_calls == 2:
                return graphene.String(default_value=None)
        return original(field_type, field_name, field_info)

    monkeypatch.setattr(
        GraphQL, "_map_field_to_graphene_read", staticmethod(with_null_default)
    )
    yield from _population_fixture.__wrapped__(monkeypatch, request)


def test_fresh_builder_null_default_is_an_extra_effective_predicate(
    null_default_population,
):
    _schema, _managers, rows = null_default_population
    rows["Gross"][0]["source"] = None
    rows["Gross"].append(
        {**deepcopy(rows["Gross"][0]), "id": "g2", "amount": 200, "source": "a"}
    )
    full = inspect_manager_schema("Gross", view="full")
    predicate = full["types"]["GrossFilterTypeDepth1"]["fields"]["source_Exact"]
    assert predicate["default"] is None
    assert predicate["filter_contract"]["operator"] == "exact"
    runner, runtime, binding = _setup(null_default_population)
    assert runner.evidence.get("gross").payload()["value"] == 120
    _reject_net(runner, runtime, binding)


@pytest.fixture
def enum_category(category_schema, monkeypatch):
    schema = category_schema

    class Family(str, Enum):
        ALPHA = "class-x"
        OTHER = "other"

    category = graphene.Enum.from_enum(Family)
    manager = GraphQL.manager_registry[schema.manager]
    interface = manager.Interface
    original_attributes = interface.get_attribute_types

    def attributes(cls):
        return {**original_attributes(), "family": {"type": Family}}

    monkeypatch.setattr(interface, "get_attribute_types", classmethod(attributes))
    original_mapper = GraphQL._map_field_to_graphene_read

    def mapper(field_type, field_name, field_info=None):
        return (
            category()
            if field_type is Family
            else original_mapper(field_type, field_name, field_info)
        )

    generated = create_filter_options(manager, {}, mapper)
    output = GraphQL.graphql_type_registry[schema.manager]
    monkeypatch.setitem(output._meta.fields, "family", graphene.Field(category))
    monkeypatch.setitem(
        schema.query_type._meta.fields["exposed_items"].args,
        "filter",
        graphene.Argument(generated),
    )
    GraphQL._schema = graphene.Schema(query=schema.query_type)
    attach_unit_contracts(
        GraphQL._schema.graphql_schema,
        {schema.manager: manager},
        {schema.manager: output},
    )
    for row in schema.rows:
        row["family"] = Family.ALPHA if row["id"] in (1, 2) else Family.OTHER
    schema.actual = "ALPHA"
    return schema, GraphQL._schema.graphql_schema.get_type(category._meta.name), Family


def test_common_generated_enum_keeps_real_category_binding(enum_category):
    schema, _native, _family = enum_category
    runner = _public_runner(schema.manager)
    result, valid = _lookup(runner, schema)
    assert valid and [row["family"] for row in result["data"]] == [
        "ALPHA",
        "ALPHA",
        "OTHER",
    ]
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family": "ALPHA"},
        category_bindings=[_binding(path=["filters", "family"])],
    )
    assert valid and result["data"] == [
        {"id": 1, "family": "ALPHA"},
        {"id": 2, "family": "ALPHA"},
    ]


@pytest.mark.parametrize(
    "damage",
    [
        "value",
        "parse_value",
        "parse_literal",
        "serialize",
        "lookup",
        "raw_index",
        "member_value",
        "missing",
    ],
)
def test_changed_enum_codec_invalidates_observed_lookup_authority(
    enum_category, monkeypatch, damage
):
    schema, native, family = enum_category
    runner = _public_runner(schema.manager)
    result, valid = _lookup(runner, schema)
    assert valid and result["complete"] is True
    before = inspect_manager_schema(schema.manager, view="full")
    if damage == "value":
        monkeypatch.setattr(native.values["ALPHA"], "value", family.OTHER)
    elif damage == "lookup":
        monkeypatch.setattr(
            native, "_value_lookup", {family.ALPHA: "OTHER", family.OTHER: "ALPHA"}
        )
    elif damage == "raw_index":
        monkeypatch.setattr(
            family,
            "_value2member_map_",
            {family.ALPHA.value: family.OTHER, family.OTHER.value: family.ALPHA},
        )
    elif damage == "member_value":
        monkeypatch.setattr(family.ALPHA, "_value_", "other")
    elif damage == "missing":
        monkeypatch.setattr(
            family, "_missing_", classmethod(lambda cls, _value: cls.ALPHA)
        )
    else:
        original = getattr(native, damage)
        monkeypatch.setattr(
            native, damage, lambda *args, **kwargs: original(*args, **kwargs)
        )
    clear_contract_cache()
    assert (
        inspect_manager_schema(schema.manager, view="full")["snapshot"]
        != before["snapshot"]
    )
    call_count = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"family": "ALPHA"},
        category_bindings=[_binding(path=["filters", "family"])],
    )
    assert (
        result == {"status": "error", "code": "invalid_category_binding"} and not valid
    )
    assert len(schema.calls) == call_count


def test_common_enum_category_supports_raw_backend_values(enum_category):
    schema, _native, _family = enum_category
    for row in schema.rows:
        row["family"] = row["family"].value
    runner = _public_runner(schema.manager)
    result, valid = _lookup(runner, schema)
    assert valid and [row["family"] for row in result["data"]] == [
        "ALPHA",
        "ALPHA",
        "OTHER",
    ]
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family": "ALPHA"},
        category_bindings=[_binding(path=["filters", "family"])],
    )
    assert valid and result["data"] == [
        {"id": 1, "family": "ALPHA"},
        {"id": 2, "family": "ALPHA"},
    ]


@pytest.mark.parametrize("entrypoint", ["all", "filter"])
def test_changed_common_manager_entrypoint_revokes_population_authority(
    public_population, monkeypatch, entrypoint
):
    _schema, managers, rows = public_population
    manager = managers["Gross"]
    rows["Gross"].append({**deepcopy(rows["Gross"][0]), "id": "hidden", "amount": 200})
    before = inspect_manager_schema("Gross", view="full")
    original = getattr(manager, entrypoint)

    def narrowed(cls, **kwargs):
        return original(**kwargs).filter(amount__lt=200)

    monkeypatch.setattr(manager, entrypoint, classmethod(narrowed))
    clear_contract_cache()
    assert (
        inspect_manager_schema("Gross", view="full")["snapshot"] != before["snapshot"]
    )
    _reject_net(*_setup(public_population))


@pytest.mark.parametrize(
    "helper",
    [
        "normalize_filter_input",
        "normalize_id_filter_value",
        "_cast_identity_scalar",
        "GraphQLID",
        "_filter_identity_inputs",
        "_identity_filter_converter",
    ],
)
def test_changed_common_normalizer_helper_cannot_attest_subset(
    public_population, monkeypatch, helper
):
    from general_manager.api import graphql_search
    from general_manager.api.graphql_resolvers import create_root_list_resolver

    schema, managers, rows = public_population
    before = inspect_manager_schema("Gross", view="full")
    rows["Gross"].append(
        {
            **deepcopy(rows["Gross"][0]),
            "id": "january",
            "amount": 200,
            "event_date": date(2025, 1, 15),
        }
    )
    original = getattr(graphql_search, helper)
    if helper == "normalize_filter_input":

        def changed(manager, values):
            plan = original(manager, values)
            if manager is managers["Gross"]:
                plan["filter"]["amount__lt"] = 200
            return plan
    elif helper == "normalize_id_filter_value":

        def changed(manager, key, value):
            if manager is managers["Gross"] and key == "event_date__gte":
                value = date(2025, 2, 1)
            return original(manager, key, value)

    elif helper == "GraphQLID":
        changed = SimpleNamespace(parse_value=lambda _value: "tampered-id")

    elif helper == "_filter_identity_inputs":

        def changed(manager):
            return {}

    elif helper == "_identity_filter_converter":

        def changed(manager, field):
            return original(manager, field)

    else:

        def changed(converter, value):
            return original(converter, "tampered-id")

    monkeypatch.setattr(graphql_search, helper, changed)
    if helper == "normalize_filter_input":
        schema.graphql_schema.query_type.fields[
            "grossList"
        ].resolve = create_root_list_resolver(managers["Gross"])
    clear_contract_cache()
    assert (
        inspect_manager_schema("Gross", view="full")["snapshot"] != before["snapshot"]
    )
    _reject_net(*_setup(public_population))


@pytest.mark.parametrize("flag", ["is_identifier", "is_primary_key"])
def test_changed_native_identity_metadata_revokes_captured_predicate_authority(
    public_population, monkeypatch, flag
):
    _schema, managers, _rows = public_population
    interface = managers["Gross"].Interface
    original = interface.get_attribute_types
    before = inspect_manager_schema("Gross", view="full")["snapshot"]

    def changed(cls):
        attributes = deepcopy(original())
        attributes["id"][flag] = True
        return attributes

    monkeypatch.setattr(interface, "get_attribute_types", classmethod(changed))
    clear_contract_cache()
    assert inspect_manager_schema("Gross", view="full")["snapshot"] != before
    _reject_net(*_setup(public_population))
