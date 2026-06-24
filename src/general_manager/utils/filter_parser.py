"""Utilities for parsing filter keyword arguments into structured callables."""

from __future__ import annotations

from collections.abc import Container, Mapping
from typing import Callable, Literal, TypedDict, TypeGuard, cast

from general_manager.manager.input import Input

LookupName = Literal[
    "exact",
    "lt",
    "lte",
    "gt",
    "gte",
    "contains",
    "startswith",
    "endswith",
    "in",
]
FilterFunction = Callable[[object], bool]
InputFieldMap = Mapping[str, Input[type[object]]]


class ParsedFilterCriteria(TypedDict, total=False):
    """Structured filters generated for one input field."""

    filter_kwargs: dict[str, object]
    filter_funcs: list[FilterFunction]


ParsedFilters = dict[str, ParsedFilterCriteria]
_SUPPORTED_LOOKUPS: frozenset[LookupName] = frozenset(
    {
        "exact",
        "lt",
        "lte",
        "gt",
        "gte",
        "contains",
        "startswith",
        "endswith",
        "in",
    }
)


class UnknownInputFieldError(ValueError):
    """Raised when a filter references an unknown input field."""

    def __init__(self, field_name: str) -> None:
        """Build an error for the unknown filter field name.

        Args:
            field_name: Field name parsed from the filter key.

        Attributes:
            field_name: The unknown field name parsed from the filter key.
        """
        self.field_name = field_name
        super().__init__(f"Unknown input field '{field_name}' in filter.")


def parse_filters(
    filter_kwargs: Mapping[str, object],
    possible_values: InputFieldMap,
) -> ParsedFilters:
    """Parse raw filter keyword arguments into bucket or Python filter criteria.

    ``filter_kwargs`` uses Django-style keys of ``field`` or
    ``field__lookup``. Inputs whose type is a
    :class:`general_manager.GeneralManager` subclass are returned as
    ``filter_kwargs`` for downstream bucket filtering. The lookup suffix is
    preserved for manager inputs, so ``project__name__startswith`` becomes a
    manager criterion with lookup ``name__startswith``. All other inputs are
    always returned as ``filter_funcs`` predicates produced by
    :func:`create_filter_function`; non-manager inputs never emit
    ``filter_kwargs``.

    Manager input aliases ending in ``_id`` are accepted when the alias without
    ``_id`` is a configured manager input. The alias is translated to an ``id``
    lookup, so ``project_id__in=[1, 2]`` becomes ``{"id__in": [1, 2]}``. Alias
    values are passed through unchanged because the alias already targets the
    downstream id lookup. For manager inputs, any suffix after the field name is
    an explicit downstream lookup path, whether or not its final segment is one
    of the predicate lookup names. Such suffixes preserve the raw value
    unchanged for downstream bucket filtering. A direct manager filter without
    a suffix, such as ``project=raw_value``, is cast through the manager
    ``Input`` when the value is not already a manager instance. Values that are
    already ``GeneralManager`` instances skip casting. In both cases the final
    downstream value is ``getattr(value, "id", value)``.

    Non-manager list and tuple values are cast element-by-element when they are
    not already instances of the configured input type; other non-manager
    values are cast once. Multiple criteria for the same field are appended in
    the iteration order produced by ``filter_kwargs.items()``. Manager filter
    kwargs are stored in a mapping, so normalized duplicate lookup keys for the
    same field overwrite earlier values; for example ``project_id__in`` and
    ``project__id__in`` both normalize to ``id__in`` and the later item wins.
    Non-manager duplicate criteria append another predicate. Empty criteria
    entries are not emitted. ``Input.cast`` conversion behavior follows the
    public ``Input`` contract documented in the core API reference.

    Args:
        filter_kwargs: Raw filter expressions keyed by ``field`` or
            ``field__lookup``.
        possible_values: Mapping of valid input field names to ``Input``
            definitions used for type detection and value casting.

    Returns:
        A mapping by input field. Each entry contains ``filter_kwargs`` for
        manager inputs or ``filter_funcs`` for non-manager inputs.

    Raises:
        UnknownInputFieldError: If a filter references a field that is not in
            ``possible_values`` and is not a valid manager ``_id`` alias.
        ValueError: Propagated from ``Input.cast`` when a value cannot be
            converted.
        TypeError: Propagated from input type checks or custom casts.
    """
    from general_manager.manager.general_manager import GeneralManager

    filters: ParsedFilters = {}
    for kwarg, value in filter_kwargs.items():
        parts = kwarg.split("__")
        field_name = parts[0]
        id_alias_lookup_prefix = ""
        if field_name not in possible_values and field_name.endswith("_id"):
            alias_field_name = field_name.removesuffix("_id")
            if alias_field_name in possible_values and issubclass(
                possible_values[alias_field_name].type,
                GeneralManager,
            ):
                field_name = alias_field_name
                id_alias_lookup_prefix = "id"
        if field_name not in possible_values:
            raise UnknownInputFieldError(field_name)
        input_field = possible_values[field_name]

        lookup = "__".join(parts[1:]) if len(parts) > 1 else ""
        if id_alias_lookup_prefix:
            lookup = (
                id_alias_lookup_prefix
                if lookup == ""
                else f"{id_alias_lookup_prefix}__{lookup}"
            )

        if issubclass(input_field.type, GeneralManager):
            # Collect filter keyword arguments for the input field
            if lookup == "":
                lookup = "id"
                if not isinstance(value, GeneralManager):
                    value = input_field.cast(value)
                value = getattr(value, "id", value)
            filter_kwargs_entry = _ensure_filter_kwargs(filters, field_name)
            filter_kwargs_entry[lookup] = value
        else:
            # Build filter functions for non-bucket types
            if isinstance(value, (list, tuple)) and not isinstance(
                value, input_field.type
            ):
                casted_value: object = [input_field.cast(v) for v in value]
            else:
                casted_value = input_field.cast(value)
            filter_func = create_filter_function(lookup, casted_value)
            filter_funcs_entry = _ensure_filter_funcs(filters, field_name)
            filter_funcs_entry.append(filter_func)
    return filters


def create_filter_function(lookup_str: str, value: object) -> FilterFunction:
    """Build a predicate for an optional attribute path and lookup operation.

    ``lookup_str`` may be a lookup name such as ``gte`` or a nested attribute
    path such as ``address__city__exact``. If the final path segment is not a
    supported lookup, the whole string is treated as an attribute path and the
    lookup defaults to ``exact``. Attribute traversal uses ``getattr`` only; it
    does not read mapping keys or sequence indexes. Missing attributes return
    ``False``. Empty path segments are treated as literal attribute names, so
    malformed strings generally return ``False`` instead of raising.

    Args:
        lookup_str: Attribute path and optional lookup operator separated by
            double underscores.
        value: Reference value used by the lookup comparison.

    Returns:
        A predicate returning ``True`` when the target object or nested
        attribute satisfies the lookup.
    """
    parts = lookup_str.split("__") if lookup_str else []
    lookup: LookupName
    if parts and parts[-1] in _SUPPORTED_LOOKUPS:
        lookup = cast(LookupName, parts[-1])
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


def apply_lookup(value_to_check: object, lookup: str, filter_value: object) -> bool:
    """Evaluate one supported lookup operation against a candidate value.

    Supported lookups are ``exact``, ``lt``, ``lte``, ``gt``, ``gte``,
    ``contains``, ``startswith``, ``endswith``, and ``in``. String operations
    are case-sensitive and only match when both operands are strings;
    ``contains`` evaluates ``filter_value in value_to_check``. ``in`` accepts
    non-string containers such as lists, tuples, sets, and ranges; strings and
    bytes are rejected as membership containers to avoid accidental substring
    filtering. Rich comparisons that raise ``TypeError`` or return
    ``NotImplemented`` evaluate to ``False``.

    Args:
        value_to_check: Candidate value read from the object being filtered.
        lookup: Lookup operation name.
        filter_value: Reference value supplied by the filter expression.

    Returns:
        ``True`` when the lookup succeeds, otherwise ``False``. Unsupported
        lookups and incompatible operand types return ``False``.
    """
    try:
        if lookup == "exact":
            return value_to_check == filter_value
        elif lookup == "lt":
            return _compare(value_to_check, "__lt__", filter_value)
        elif lookup == "lte":
            return _compare(value_to_check, "__le__", filter_value)
        elif lookup == "gt":
            return _compare(value_to_check, "__gt__", filter_value)
        elif lookup == "gte":
            return _compare(value_to_check, "__ge__", filter_value)
        elif lookup == "contains" and isinstance(value_to_check, str):
            if not isinstance(filter_value, str):
                return False
            return filter_value in value_to_check
        elif lookup == "startswith" and isinstance(value_to_check, str):
            if not isinstance(filter_value, str):
                return False
            return value_to_check.startswith(filter_value)
        elif lookup == "endswith" and isinstance(value_to_check, str):
            if not isinstance(filter_value, str):
                return False
            return value_to_check.endswith(filter_value)
        elif lookup == "in":
            if not _is_membership_container(filter_value):
                return False
            return value_to_check in filter_value
        else:
            return False
    except TypeError:
        return False


def _ensure_filter_kwargs(
    filters: ParsedFilters,
    field_name: str,
) -> dict[str, object]:
    entry = filters.get(field_name)
    if entry is None:
        entry = {}
        filters[field_name] = entry
    filter_kwargs = entry.get("filter_kwargs")
    if filter_kwargs is None:
        filter_kwargs = {}
        entry["filter_kwargs"] = filter_kwargs
    return filter_kwargs


def _ensure_filter_funcs(
    filters: ParsedFilters,
    field_name: str,
) -> list[FilterFunction]:
    entry = filters.get(field_name)
    if entry is None:
        entry = {}
        filters[field_name] = entry
    filter_funcs = entry.get("filter_funcs")
    if filter_funcs is None:
        filter_funcs = []
        entry["filter_funcs"] = filter_funcs
    return filter_funcs


def _compare(
    value_to_check: object,
    method_name: Literal["__lt__", "__le__", "__gt__", "__ge__"],
    filter_value: object,
) -> bool:
    comparison = getattr(value_to_check, method_name, None)
    if not callable(comparison):
        return False
    result = comparison(filter_value)
    if result is NotImplemented:
        return False
    return bool(result)


def _is_membership_container(value: object) -> TypeGuard[Container[object]]:
    return isinstance(value, Container) and not isinstance(value, (str, bytes))
