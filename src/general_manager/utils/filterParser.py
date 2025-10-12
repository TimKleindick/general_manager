"""Utilities for parsing filter keyword arguments into structured callables."""

from __future__ import annotations
from typing import Any, Callable
from general_manager.manager.input import Input


def parse_filters(
    filter_kwargs: dict[str, Any], possible_values: dict[str, Input]
) -> dict[str, dict]:
    """
    Parse raw filter keyword arguments into structured criteria for the configured input fields.

    Parameters:
        filter_kwargs (dict[str, Any]): Filter expressions keyed by `<field>[__lookup]` strings.
        possible_values (dict[str, Input]): Input definitions that validate, cast, and describe dependencies for each field.

    Returns:
        dict[str, dict[str, Any]]: Mapping of input field names to dictionaries containing either `filter_kwargs` or `filter_funcs` entries used when evaluating filters.

    Raises:
        ValueError: If a filter references an input field that is not defined in `possible_values`.
    """
    from general_manager.manager.generalManager import GeneralManager

    filters: dict[str, dict[str, Any]] = {}
    for kwarg, value in filter_kwargs.items():
        parts = kwarg.split("__")
        field_name = parts[0]
        if field_name not in possible_values:
            raise ValueError(f"Unknown input field '{field_name}' in filter")
        input_field = possible_values[field_name]

        lookup = "__".join(parts[1:]) if len(parts) > 1 else ""

        if issubclass(input_field.type, GeneralManager):
            # Sammle die Filter-Keyword-Argumente für das InputField
            if lookup == "":
                lookup = "id"
                if not isinstance(value, GeneralManager):
                    value = input_field.cast(value)
                value = getattr(value, "id", value)
            filters.setdefault(field_name, {}).setdefault("filter_kwargs", {})[
                lookup
            ] = value
        else:
            # Erstelle Filterfunktionen für Nicht-Bucket-Typen
            if isinstance(value, (list, tuple)) and not isinstance(
                value, input_field.type
            ):
                casted_value = [input_field.cast(v) for v in value]
            else:
                casted_value = input_field.cast(value)
            filter_func = create_filter_function(lookup, casted_value)
            filters.setdefault(field_name, {}).setdefault("filter_funcs", []).append(
                filter_func
            )
    return filters


def create_filter_function(lookup_str: str, value: Any) -> Callable[[Any], bool]:
    """
    Build a callable that evaluates whether an object's attribute satisfies a lookup expression.

    Parameters:
        lookup_str (str): Attribute path and lookup operator separated by double underscores (for example, `age__gte`).
        value (Any): Reference value used when applying the lookup comparison.

    Returns:
        Callable[[Any], bool]: Function returning True when the target object's attribute value passes the lookup test.
    """
    parts = lookup_str.split("__") if lookup_str else []
    if parts and parts[-1] in [
        "exact",
        "lt",
        "lte",
        "gt",
        "gte",
        "contains",
        "startswith",
        "endswith",
        "in",
    ]:
        lookup = parts[-1]
        attr_path = parts[:-1]
    else:
        lookup = "exact"
        attr_path = parts

    def filter_func(x: object) -> bool:
        for attr in attr_path:
            if hasattr(x, attr):
                x = getattr(x, attr)
            else:
                return False
        return apply_lookup(x, lookup, value)

    return filter_func


def apply_lookup(value_to_check: Any, lookup: str, filter_value: Any) -> bool:
    """
    Evaluate a lookup operation against a candidate value.

    Parameters:
        value_to_check (Any): Value that will be compared using the lookup expression.
        lookup (str): Name of the comparison operation (for example, `exact`, `gte`, or `contains`).
        filter_value (Any): Reference value supplied by the filter expression.

    Returns:
        bool: True if the comparison succeeds; otherwise, False.
    """
    try:
        if lookup == "exact":
            return value_to_check == filter_value
        elif lookup == "lt":
            return value_to_check < filter_value
        elif lookup == "lte":
            return value_to_check <= filter_value
        elif lookup == "gt":
            return value_to_check > filter_value
        elif lookup == "gte":
            return value_to_check >= filter_value
        elif lookup == "contains" and isinstance(value_to_check, str):
            return filter_value in value_to_check
        elif lookup == "startswith" and isinstance(value_to_check, str):
            return value_to_check.startswith(filter_value)
        elif lookup == "endswith" and isinstance(value_to_check, str):
            return value_to_check.endswith(filter_value)
        elif lookup == "in":
            return value_to_check in filter_value
        else:
            return False
    except TypeError as e:
        return False
