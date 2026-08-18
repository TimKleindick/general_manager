"""Shared resolution of normalized calculation input values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from general_manager.manager.general_manager import GeneralManager

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.calculation import CalculationInterface


def track_manager_input(value: object) -> None:
    """Replay the identification dependency for a cached manager input."""
    if isinstance(value, GeneralManager):
        value.__class__._track_identification_dependency(value.identification)


def resolve_calculation_input_value(
    interface_cls: type["CalculationInterface"],
    identification: Mapping[str, object],
    field_name: str,
    resolved_values: dict[str, object],
) -> object:
    """Resolve and cache one normalized calculation input value.

    Dependencies are resolved recursively before the requested input is cast,
    matching calculation interface accessors. The caller owns
    ``resolved_values`` so a row can reuse dependency results without sharing
    normalized state with another calculation identification.
    """
    if field_name in resolved_values:
        value = resolved_values[field_name]
        track_manager_input(value)
        return value

    field = interface_cls.input_fields[field_name]
    dependencies = {
        dependency_name: resolve_calculation_input_value(
            interface_cls,
            identification,
            dependency_name,
            resolved_values,
        )
        for dependency_name in field.depends_on
    }
    value = field.cast(
        identification.get(field_name),
        dependencies,
        cache_context=(interface_cls._parent_class, field_name),
    )
    track_manager_input(value)
    resolved_values[field_name] = value
    return value
