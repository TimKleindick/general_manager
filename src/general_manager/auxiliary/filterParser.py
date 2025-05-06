from typing import Any, Callable, List


def parse_filters(
    filter_kwargs: dict[str, Any], possible_values: dict[str, Any]
) -> dict[str, dict]:
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


def apply_lookup(x: Any, lookup: str, value: Any) -> bool:
    try:
        if lookup == "exact":
            return x == value
        elif lookup == "lt":
            return x < value
        elif lookup == "lte":
            return x <= value
        elif lookup == "gt":
            return x > value
        elif lookup == "gte":
            return x >= value
        elif lookup == "contains" and isinstance(x, str):
            return value in x
        elif lookup == "startswith" and isinstance(x, str):
            return x.startswith(value)
        elif lookup == "endswith" and isinstance(x, str):
            return x.endswith(value)
        elif lookup == "in":
            return x in value
        else:
            return False
    except TypeError as e:
        return False
