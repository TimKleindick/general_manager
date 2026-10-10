"""Explicit public dimensions for numeric attributes, shared by every backend."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import Any, NoReturn, cast

from pint.errors import PintError

from general_manager.measurement.measurement import ureg, _unit_uses_offset

UNIT_EXTENSION = "gm_unit_contract"
IDENTITY_EXTENSION = "gm_unit_identity"


def _invalid() -> NoReturn:
    message = "invalid public numeric field unit contract"
    raise ValueError(message)


def _term(unit: object) -> dict[str, Any]:
    if not isinstance(unit, str) or not unit.strip():
        _invalid()
    try:
        parsed = ureg.parse_units(unit)
        if _unit_uses_offset(unit):
            _invalid()
    except (PintError, TypeError, SyntaxError) as exc:
        message = "invalid or unsupported declared unit"
        raise ValueError(message) from exc
    dimensions = dict(parsed.dimensionality)
    if any(type(power) is not int for power in dimensions.values()):
        _invalid()
    return {
        "unit": "count" if parsed == ureg.parse_units("count") else str(parsed),
        "dimension": dimensions,
    }


def _normalized(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid()
    if value.get("kind") == "quantity" and set(value) == {
        "kind",
        "unit_field",
        "units",
    }:
        field, units = value["unit_field"], value["units"]
        if (
            not isinstance(field, str)
            or not field.isidentifier()
            or not isinstance(units, Mapping)
            or not units
        ):
            _invalid()
        result = {}
        for label, term in units.items():
            if (
                not isinstance(label, str)
                or not label.strip()
                or not isinstance(term, Mapping)
            ):
                _invalid()
            result[label] = _term(term.get("unit"))
        return {"kind": "quantity", "unit_field": field, "units": result}
    if value.get("kind") == "factor" and set(value) == {"kind", "source", "target"}:
        if not isinstance(value["source"], Mapping) or not isinstance(
            value["target"], Mapping
        ):
            _invalid()
        return {
            "kind": "factor",
            "source": _term(value["source"].get("unit")),
            "target": _term(value["target"].get("unit")),
        }
    _invalid()


@dataclass(frozen=True)
class FieldUnitContract:
    """Immutable application declaration, never inferred from row unit strings."""

    payload_json: str

    def __post_init__(self) -> None:
        try:
            value = json.loads(self.payload_json)
            normalized = _normalized(value)
            if json.dumps(value, sort_keys=True, allow_nan=False) != json.dumps(
                normalized, sort_keys=True, allow_nan=False
            ):
                _invalid()
        except (TypeError, json.JSONDecodeError) as exc:
            message = "invalid unit declaration serialization"
            raise ValueError(message) from exc

    def as_mapping(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.payload_json))

    @classmethod
    def quantity(
        cls, *, unit_field: str, units: Mapping[str, str]
    ) -> FieldUnitContract:
        if not isinstance(units, Mapping):
            _invalid()
        value = {
            "kind": "quantity",
            "unit_field": unit_field,
            "units": {label: _term(unit) for label, unit in units.items()},
        }
        return cls(json.dumps(value, sort_keys=True, allow_nan=False))

    @classmethod
    def factor(cls, *, source_unit: str, target_unit: str) -> FieldUnitContract:
        value = {
            "kind": "factor",
            "source": _term(source_unit),
            "target": _term(target_unit),
        }
        return cls(json.dumps(value, sort_keys=True, allow_nan=False))

    @classmethod
    def from_mapping(cls, value: object) -> FieldUnitContract:
        if not isinstance(value, Mapping):
            _invalid()
        return cls(json.dumps(dict(value), sort_keys=True, allow_nan=False))


def public_unit_contract(field: object) -> dict[str, Any] | None:
    """Read only the explicitly public extension, validating its complete shape."""
    extensions = getattr(field, "extensions", None)
    if not isinstance(extensions, Mapping) or UNIT_EXTENSION not in extensions:
        return None
    return FieldUnitContract.from_mapping(extensions[UNIT_EXTENSION]).as_mapping()


def public_unit_identity(field: object) -> dict[str, str] | None:
    """Explicit common-interface constructor binding, not a scalar-name guess."""
    extensions = getattr(field, "extensions", None)
    if not isinstance(extensions, Mapping) or IDENTITY_EXTENSION not in extensions:
        return None
    value = extensions[IDENTITY_EXTENSION]
    if (
        not isinstance(value, dict)
        or set(value) != {"source", "input_name", "input_type"}
        or value["source"] != "interface_input"
        or value["input_name"] != "id"
        or value["input_type"] not in ("int", "str")
    ):
        _invalid()
    return dict(value)
