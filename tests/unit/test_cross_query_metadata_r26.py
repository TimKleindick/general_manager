"""New public authorities use the shared interface boundary and stay exposed."""

from decimal import Decimal
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock

from django.db import models
import pytest

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_filter_contract import FILTER_EXTENSION
from general_manager.chat.schema_inspection import inspect_manager_schema
from general_manager.interface import (
    CalculationInterface,
    DatabaseInterface,
    ReadOnlyInterface,
)
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.unit_contract import FieldUnitContract
from general_manager.manager.input import Input
from tests.unit.test_chat_cross_query_scope import (
    public_population as _population_fixture,
)

public_population = _population_fixture


@pytest.mark.parametrize("custom", [False, True])
def test_root_factory_keeps_custom_or_mock_hook_returns_unmarked(
    public_population, custom
):
    from general_manager.api.graphql_resolvers import create_root_list_resolver

    def custom_resolver(*_args, **_kwargs):
        return {"items": []}

    returned = custom_resolver if custom else Mock()
    resolver = create_root_list_resolver(
        public_population[1]["Gross"], resolver_factory=lambda *_args: returned
    )
    assert resolver is returned
    assert "_general_manager_population_owner" not in vars(resolver)


def test_root_factory_common_graphql_hook_retains_population_authority(
    public_population,
):
    from general_manager.api.graphql_resolvers import create_root_list_resolver

    manager = public_population[1]["Gross"]
    resolver = create_root_list_resolver(
        manager, resolver_factory=GraphQL._create_list_resolver
    )
    from general_manager.api.graphql_filter_contract import public_population_contract

    field = public_population[0].graphql_schema.query_type.fields["grossList"]
    field.resolve = resolver
    assert public_population_contract(field, {"Gross": manager}) == {
        "source": "interface_list",
        "manager": "Gross",
        "filter_source": "interface_filter",
    }


def test_root_factory_custom_validator_cannot_receive_population_authority(
    public_population,
):
    from general_manager.api.graphql_resolvers import (
        create_list_resolver,
        create_root_list_resolver,
    )
    from general_manager.api.graphql_search import normalize_filter_input

    def custom_factory(base_getter, manager):
        return create_list_resolver(
            base_getter,
            manager,
            normalize_filter_input,
            grouping_validator=lambda *_args: None,
        )

    resolver = create_root_list_resolver(
        public_population[1]["Gross"], resolver_factory=custom_factory
    )
    assert "_general_manager_population_owner" not in vars(resolver)


@pytest.mark.parametrize(
    "interface_kind", ["database", "readonly", "calculation", "custom"]
)
def test_fixed_quantity_validates_actual_numeric_attribute_through_read_capability(
    interface_kind,
):
    class NumericModel(models.Model):
        amount = models.IntegerField()
        text = models.CharField(max_length=30)

        class Meta:
            abstract = True
            app_label = "cross_query_unit_contract_tests"

    if interface_kind in {"database", "readonly"}:
        parent = (
            DatabaseInterface if interface_kind == "database" else ReadOnlyInterface
        )

        class Declared(parent):
            _model = NumericModel
    elif interface_kind == "calculation":

        class Declared(CalculationInterface):
            input_fields: ClassVar = {"amount": Input(int), "text": Input(str)}
    else:

        class Read(BaseCapability):
            name: ClassVar = "read"

            def get_attribute_types(self, interface):
                return {"amount": {"type": Decimal}, "text": {"type": str}}

        class Declared(InterfaceBase):
            pass

        Read().setup(Declared)
    Declared.field_unit_contracts = {
        "amount": FieldUnitContract.fixed_quantity(unit="count", label="widgets")
    }
    assert Declared.get_field_unit_contracts()["amount"]["term"]["dimension"] == {
        "[piece_count]": 1
    }
    Declared.field_unit_contracts = {
        "text": FieldUnitContract.fixed_quantity(unit="count", label="widgets")
    }
    with pytest.raises(ValueError, match="numeric attribute"):
        Declared.get_field_unit_contracts()


def test_hidden_relation_filter_contract_cannot_publish_target_manager(
    public_population,
):
    public_population[1]["Subject"].chat_exposed = False
    public = inspect_manager_schema("Gross")
    assert "subject" not in public["output_fields"]
    full = inspect_manager_schema("Gross", view="full")
    assert "subject" not in full["types"]["GrossFilterTypeDepth1"]["fields"]
    assert "Subject" not in str(full)


def test_filter_contract_capture_keeps_private_extensions_opaque(public_population):
    class Opaque:
        def __deepcopy__(self, memo):
            pytest.fail("private filter extensions must stay opaque")

    field = (
        public_population[0]
        .graphql_schema.get_type("GrossFilterTypeDepth1")
        .fields["eventDate_Gte"]
    )
    field.extensions["private"] = Opaque()
    public = inspect_manager_schema("Gross", view="full")
    definition = public["types"]["GrossFilterTypeDepth1"]["fields"]["eventDate_Gte"]
    assert "private" not in definition
    assert definition["filter_contract"] == field.extensions[FILTER_EXTENSION]


def test_custom_property_override_does_not_retain_generated_attribute_filter_authority(
    public_population, monkeypatch
):
    from general_manager.api.graphql_search import create_filter_options

    interface = public_population[1]["Gross"].Interface
    monkeypatch.setattr(
        interface,
        "get_graph_ql_properties",
        lambda: {
            "amount": SimpleNamespace(
                filterable=True, graphql_type_hint=int, is_identifier=False
            )
        },
    )
    generated = create_filter_options(
        public_population[1]["Gross"], {}, GraphQL._map_field_to_graphene_read
    )
    assert "amount__gte" not in generated._general_manager_filter_contracts


def test_custom_root_reusing_generated_filter_input_cannot_attest_cross_query_population(
    public_population,
):
    from general_manager.chat.planned.calculation_scope import (
        validate_calculation_binding_evidence,
    )
    from general_manager.chat.planned.calculations import CalculationError
    from tests.unit.test_chat_cross_query_scope import _setup

    field = public_population[0].graphql_schema.query_type.fields["returnedList"]
    original = field.resolve
    field.resolve = lambda *args, **kwargs: original(*args, **kwargs)
    runner, runtime, binding = _setup(public_population)
    with pytest.raises(CalculationError, match=r"root.*population"):
        validate_calculation_binding_evidence(
            runtime.task, "net", binding, runner.evidence
        )
