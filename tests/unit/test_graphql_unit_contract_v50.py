"""Public units cross the executable schema boundary without private metadata."""

from decimal import Decimal
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from graphql import build_schema

from general_manager.api.graphql import GraphQL
from general_manager.chat import graphql_contract as contract
from general_manager.chat.schema_inspection import (
    inspect_manager_schema,
    SchemaInspectionError,
)
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.unit_contract import FieldUnitContract, UNIT_EXTENSION


@pytest.fixture
def unit_schema(monkeypatch: pytest.MonkeyPatch) -> Any:
    schema = build_schema("""
      type Item { id: ID!, amount: Float, unitLabel: String, other: Other }
      type Other { id: ID!, rate: Float }
      type Info { totalCount: Int }
      type ItemPage { items: [Item!]!, pageInfo: Info! }
      type OtherPage { items: [Other!]!, pageInfo: Info! }
      type Query { itemList: ItemPage, otherList: OtherPage }
    """)

    class QuantityInterface(InterfaceBase):
        input_fields: ClassVar = {"id": SimpleNamespace(type=str, required=True)}
        field_unit_contracts: ClassVar = {
            "amount": FieldUnitContract.quantity(
                unit_field="unit_label", units={"widgets": "count"}
            )
        }

        @classmethod
        def get_attribute_types(cls) -> Any:
            return {"amount": {"type": Decimal}, "unit_label": {"type": str}}

    class FactorInterface(InterfaceBase):
        input_fields: ClassVar = {"id": SimpleNamespace(type=str, required=True)}
        field_unit_contracts: ClassVar = {
            "rate": FieldUnitContract.factor(source_unit="count", target_unit="g")
        }

        @classmethod
        def get_attribute_types(cls) -> Any:
            return {"rate": {"type": Decimal}}

    class Item:
        chat_exposed = True
        Interface = QuantityInterface

    class Other:
        chat_exposed = True
        Interface = FactorInterface

    class ItemType:
        _meta = SimpleNamespace(
            name="Item",
            fields={
                "id": SimpleNamespace(name=None),
                "amount": SimpleNamespace(name=None),
                "unit_label": SimpleNamespace(name=None),
            },
        )

    class OtherType:
        _meta = SimpleNamespace(
            name="Other",
            fields={
                "rate": SimpleNamespace(name=None),
                "id": SimpleNamespace(name=None),
            },
        )

    schema.get_type("Item").graphene_type = ItemType
    schema.get_type("Other").graphene_type = OtherType
    managers, types = (
        {"Item": Item, "Other": Other},
        {"Item": ItemType, "Other": OtherType},
    )
    monkeypatch.setattr(GraphQL, "manager_registry", managers)
    monkeypatch.setattr(GraphQL, "graphql_type_registry", types)
    monkeypatch.setattr(
        GraphQL, "get_schema", lambda: SimpleNamespace(graphql_schema=schema)
    )
    contract.clear_contract_cache()
    yield schema, managers, types
    contract.clear_contract_cache()


def _attach(fixture: Any) -> Any:
    from general_manager.api.graphql_units import attach_unit_contracts

    schema, managers, types = fixture
    attach_unit_contracts(schema, managers, types)
    return schema


def test_shared_declarations_bind_actual_graphql_names_and_own_manager_details(
    unit_schema: Any,
) -> None:
    schema = _attach(unit_schema)
    public = inspect_manager_schema("Item")
    assert (
        public["output_fields"]["amount"]["unit_contract"]["unit_field"] == "unitLabel"
    )
    assert public["type_manifest"]["Other"] == {"kind": "reference", "manager": "Other"}
    other = inspect_manager_schema("Other")
    detail = inspect_manager_schema(
        "Other", view="detail", types=["Other"], snapshot=other["snapshot"]
    )
    assert detail["types"]["Other"]["fields"]["rate"]["unit_contract"]["target"][
        "dimension"
    ] == {"[mass]": 1}
    assert (
        schema.get_type("Item").fields["amount"].extensions[UNIT_EXTENSION]
        == public["output_fields"]["amount"]["unit_contract"]
    )


def test_unit_change_invalidates_old_snapshot_and_captured_value_is_immutable(
    unit_schema: Any,
) -> None:
    schema = _attach(unit_schema)
    old = inspect_manager_schema("Other")
    schema.get_type("Other").fields["rate"].extensions[UNIT_EXTENSION] = (
        FieldUnitContract.factor(source_unit="count", target_unit="m").as_mapping()
    )
    new = inspect_manager_schema("Other")
    assert old["snapshot"] != new["snapshot"]
    assert old["output_fields"]["rate"]["unit_contract"]["target"]["dimension"] == {
        "[mass]": 1
    }
    with pytest.raises(SchemaInspectionError, match="schema_snapshot_mismatch"):
        inspect_manager_schema(
            "Other", view="detail", types=["Other"], snapshot=old["snapshot"]
        )


def test_hidden_related_unit_details_are_never_expanded(unit_schema: Any) -> None:
    _attach(unit_schema)
    old = inspect_manager_schema("Item")
    unit_schema[1]["Other"].chat_exposed = False
    current = inspect_manager_schema("Item")
    assert "other" not in current["output_fields"]
    assert current["snapshot"] != old["snapshot"]
    assert inspect_manager_schema("Other") is None


def test_private_extensions_are_not_copied_into_inspection(unit_schema: Any) -> None:
    class Private:
        def __deepcopy__(self, memo: Any) -> Any:
            message = "private extension must not be copied"
            raise AssertionError(message)

    schema = _attach(unit_schema)
    schema.get_type("Item").fields["amount"].extensions["private"] = Private()
    full = contract.manager_schema("Item")
    assert "private" not in full["types"]["Item"]["fields"]["amount"]
    assert (
        full["types"]["Item"]["fields"]["amount"]["unit_contract"]["kind"] == "quantity"
    )
