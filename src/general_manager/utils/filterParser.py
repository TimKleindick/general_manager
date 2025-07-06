from __future__ import annotations
from typing import Any, Callable
from general_manager.manager.input import Input


def parse_filters(
    filter_kwargs: dict[str, Any], possible_values: dict[str, Input]
) -> dict[str, dict]:
    """
    Parses filter keyword arguments and constructs filter criteria for input fields.
    
    For each filter key-value pair, determines the target field and lookup type, validates the field, and generates either filter keyword arguments or filter functions depending on the field's type. Returns a dictionary mapping field names to filter criteria, supporting both direct lookups and dynamic filter functions.
    
    Args:
        filter_kwargs: Dictionary of filter keys and their corresponding values.
        possible_values: Mapping of field names to Input definitions used for validation and casting.
    
    Returns:
        A dictionary where each key is a field name and each value is a dictionary containing either 'filter_kwargs' for direct lookups or 'filter_funcs' for dynamic filtering.
    """
    from general_manager.manager.generalManager import GeneralManager

    filters = {}
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
    Creates a filter function based on an attribute path and lookup operation.
    
    The returned function checks whether an object's nested attribute(s) satisfy a specified comparison or matching operation against a given value.
    
    Args:
        lookup_str: Attribute path and lookup operation, separated by double underscores (e.g., "age__gte", "name__contains").
        value: The value to compare against.
    
    Returns:
        A function that takes an object and returns True if the object's attribute(s) match the filter condition, otherwise False.
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

    def filter_func(x):
        for attr in attr_path:
            if hasattr(x, attr):
                x = getattr(x, attr)
            else:
                return False
        return apply_lookup(x, lookup, value)

    return filter_func


def apply_lookup(value_to_check: Any, lookup: str, filter_value: Any) -> bool:
    """
    Evaluates whether a value satisfies a specified lookup condition against a filter value.
    
    Supports comparison and string operations such as "exact", "lt", "lte", "gt", "gte", "contains", "startswith", "endswith", and "in". Returns False for unsupported lookups or if a TypeError occurs.
    
    Args:
        value_to_check: The value to be compared or checked.
        lookup: The lookup operation to perform.
        filter_value: The value to compare against.
    
    Returns:
        True if the lookup condition is satisfied; otherwise, False.
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
