"""Explicit dimensions come from shared declarations, never numeric field names."""

from decimal import Decimal
from typing import Any, ClassVar
from types import SimpleNamespace

import pytest

from general_manager.chat.planned.calculations import calculate
from general_manager.interface.base_interface import InterfaceBase


def _contracts() -> Any:
    from general_manager.interface.unit_contract import FieldUnitContract

    return FieldUnitContract


def test_quantity_and_factor_have_explicit_discrete_and_mass_dimensions() -> None:
    cls = _contracts()
    quantity = cls.quantity(unit_field="unit", units={"widgets": "count"})
    factor = cls.factor(source_unit="count", target_unit="g")
    assert quantity.as_mapping() == {
        "kind": "quantity",
        "unit_field": "unit",
        "units": {"widgets": {"unit": "count", "dimension": {"[piece_count]": 1}}},
    }
    assert factor.as_mapping() == {
        "kind": "factor",
        "source": {"unit": "count", "dimension": {"[piece_count]": 1}},
        "target": {"unit": "gram", "dimension": {"[mass]": 1}},
    }


@pytest.mark.parametrize("bad", ["", "unknown_zorblax", "degC", None, True])
def test_invalid_and_offset_units_are_not_declarations(bad: Any) -> None:
    cls = _contracts()
    with pytest.raises(ValueError):
        cls.factor(source_unit="count", target_unit=bad)


def test_shared_interface_default_is_empty_without_heuristics() -> None:
    class Bare(InterfaceBase):
        @classmethod
        def get_attribute_types(cls) -> Any:
            return {"kg_per_widget": {"type": Decimal}}

    assert Bare.get_field_unit_contracts() == {}


def test_shared_interface_explicit_contract_is_immutable() -> None:
    contracts = _contracts()

    class Declared(InterfaceBase):
        field_unit_contracts: ClassVar = {
            "amount": contracts.quantity(
                unit_field="unit_label", units={"widgets": "count"}
            ),
            "scale": contracts.factor(source_unit="count", target_unit="g"),
        }

        @classmethod
        def get_attribute_types(cls) -> Any:
            return {
                "amount": {"type": Decimal},
                "unit_label": {"type": str},
                "scale": {"type": Decimal},
            }

    result = Declared.get_field_unit_contracts()
    result["amount"]["units"]["widgets"]["dimension"]["[mass]"] = 1
    assert Declared.get_field_unit_contracts()["amount"]["units"]["widgets"][
        "dimension"
    ] == {"[piece_count]": 1}


@pytest.mark.parametrize("case", ["missing", "text", "missing_unit", "numeric_unit"])
def test_interface_rejects_invalid_attribute_unit_binding(case: str) -> None:
    contracts = _contracts()

    class Invalid(InterfaceBase):
        field_unit_contracts: ClassVar = {
            "amount": contracts.quantity(unit_field="unit", units={"widgets": "count"})
        }

        @classmethod
        def get_attribute_types(cls) -> Any:
            if case == "missing":
                return {"unit": {"type": str}}
            return {
                "amount": {"type": str if case == "text" else Decimal},
                **(
                    {}
                    if case == "missing_unit"
                    else {"unit": {"type": int if case == "numeric_unit" else str}}
                ),
            }

    with pytest.raises(ValueError):
        Invalid.get_field_unit_contracts()


def test_legacy_sum_is_unchanged_control() -> None:
    assert calculate("sum", ["1.25", "2.75"]) == Decimal(4)


@pytest.mark.parametrize("kind", [int, str])
def test_identity_authority_comes_from_shared_required_constructor(kind: Any) -> None:
    class Identified(InterfaceBase):
        input_fields: ClassVar = {"id": SimpleNamespace(type=kind, required=True)}

    assert Identified.get_unit_identity_contract() == {
        "source": "interface_input",
        "input_name": "id",
        "input_type": kind.__name__,
    }


@pytest.mark.parametrize("case", ["absent", "composite", "optional", "boolean"])
def test_unsupported_constructor_does_not_gain_identity_authority(case: str) -> None:
    class Unsupported(InterfaceBase):
        input_fields: ClassVar = {
            "id": SimpleNamespace(
                type=bool if case == "boolean" else int, required=case != "optional"
            )
        }

    if case == "absent":
        Unsupported.input_fields = {}
    elif case == "composite":
        Unsupported.input_fields["region"] = SimpleNamespace(type=str, required=True)
    assert Unsupported.get_unit_identity_contract() is None
