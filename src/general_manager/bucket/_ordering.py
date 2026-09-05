"""Shared signed ordering grammar and stable Python execution helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from math import copysign, isinf, isnan
from numbers import Number
from typing import Protocol, TypeVar, cast
from uuid import UUID


_IDENTITY_UNAVAILABLE = object()


@dataclass(frozen=True)
class SortTerm:
    """One validated ordering field and its independent direction."""

    field: str
    descending: bool = False

    @property
    def signed_field(self) -> str:
        """Return the public signed-field representation."""
        return f"-{self.field}" if self.descending else self.field


class InvalidOrderingError(ValueError):
    """Raised for malformed, repeated, or unavailable ordering fields."""

    @classmethod
    def non_string(cls) -> "InvalidOrderingError":
        return cls("Ordering fields must be strings.")

    @classmethod
    def non_sequence(cls) -> "InvalidOrderingError":
        return cls("Ordering fields must be an iterable of field strings.")

    @classmethod
    def malformed(cls, field: object) -> "InvalidOrderingError":
        return cls(f"Invalid ordering field {field!r}.")

    @classmethod
    def duplicate(cls, field: str) -> "InvalidOrderingError":
        return cls(f"Ordering field {field!r} occurs more than once.")

    @classmethod
    def unknown(cls, field: str, manager_class: type[object]) -> "InvalidOrderingError":
        return cls(f"Unknown ordering field {field!r} for {manager_class.__name__}.")

    @classmethod
    def non_sortable(
        cls, field: str, manager_class: type[object]
    ) -> "InvalidOrderingError":
        return cls(
            f"Ordering field {field!r} is not sortable for {manager_class.__name__}."
        )


class UnsupportedOrderingValueError(TypeError):
    """Raised when one ordering category cannot compare its runtime values."""

    def __init__(self, left: object, right: object) -> None:
        super().__init__(
            "Cannot order values with incompatible runtime types "
            f"{type(left).__name__!r} and {type(right).__name__!r}."
        )


def normalize_ordering(fields: Iterable[str]) -> tuple[SortTerm, ...]:
    """Parse signed fields once, rejecting ambiguous ordering expressions."""
    if isinstance(fields, (str, bytes)):
        raise InvalidOrderingError.non_sequence()
    terms: list[SortTerm] = []
    seen: set[str] = set()
    for raw_field in fields:
        if not isinstance(raw_field, str):
            raise InvalidOrderingError.non_string()
        if raw_field.startswith("+"):
            raise InvalidOrderingError.malformed(raw_field)
        descending = raw_field.startswith("-")
        field = raw_field[1:] if descending else raw_field
        if (
            not field
            or field[:1] in {"-", "+"}
            or any(not segment for segment in field.split("__"))
        ):
            raise InvalidOrderingError.malformed(raw_field)
        if field in seen:
            raise InvalidOrderingError.duplicate(field)
        seen.add(field)
        terms.append(SortTerm(field=field, descending=descending))
    return tuple(terms)


def validate_ordering_fields(
    manager_class: type[object], terms: Sequence[SortTerm]
) -> None:
    """Reject unavailable declared paths before evaluation, including empty buckets."""
    for term in terms:
        current_manager = manager_class
        for position, part in enumerate(term.field.split("__")):
            interface = cast(
                _OrderingInterface,
                current_manager.Interface,  # type: ignore[attr-defined]
            )
            attribute_types = interface.get_attribute_types()
            properties = interface.get_graph_ql_properties()
            property_definition = properties.get(part)
            if property_definition is not None:
                if not getattr(property_definition, "sortable", False):
                    raise InvalidOrderingError.non_sortable(term.field, manager_class)
                if position + 1 != len(term.field.split("__")):
                    raise InvalidOrderingError.unknown(term.field, manager_class)
                continue
            metadata = attribute_types.get(part)
            if not isinstance(metadata, Mapping):
                raise InvalidOrderingError.unknown(term.field, manager_class)
            if position + 1 == len(term.field.split("__")):
                continue
            related_type = metadata.get("type")
            if not isinstance(related_type, type) or not hasattr(
                related_type, "Interface"
            ):
                raise InvalidOrderingError.unknown(term.field, manager_class)
            current_manager = related_type


T = TypeVar("T")


class _OrderingInterface(Protocol):
    @classmethod
    def get_attribute_types(cls) -> Mapping[str, object]: ...

    @classmethod
    def get_graph_ql_properties(cls) -> Mapping[str, object]: ...

    @classmethod
    def get_attributes(cls) -> Mapping[str, object]: ...


def sort_items(
    items: Iterable[T],
    terms: Sequence[SortTerm],
    *,
    value_for: Callable[[T, str], object] | None = None,
    identity_for: Callable[[T], object] | None = None,
) -> list[T]:
    """Sort with signed terms, nulls last, and explicit scalar categories.

    Booleans, numbers, temporal values, strings, and bytes have deterministic
    category precedence. Other values remain comparable only within their
    concrete runtime type, preserving domain-specific comparisons such as
    ``Measurement`` without coercing numbers or parsing date-looking strings.
    """
    ordered = list(items)
    if not terms:
        return ordered
    get_value = value_for or resolve_ordering_value
    get_identity = identity_for or _manager_identity_for_ordering
    identities = [get_identity(item) for item in ordered]
    identity_keys = [_ordering_identity(identity) for identity in identities]
    sortable_identity_items = [
        (position, item, key)
        for position, (item, identity, key) in enumerate(
            zip(ordered, identities, identity_keys, strict=True)
        )
        if identity is not _IDENTITY_UNAVAILABLE and key is not None
    ]
    sortable_identity_positions = {item[0] for item in sortable_identity_items}
    if sortable_identity_items:
        sorted_identity_iterator = iter(
            item
            for _position, item, _key in sorted(
                sortable_identity_items,
                key=lambda entry: entry[2],
            )
        )
        ordered = [
            next(sorted_identity_iterator)
            if position in sortable_identity_positions
            else item
            for position, item in enumerate(ordered)
        ]
    for term in reversed(terms):
        non_null = [item for item in ordered if get_value(item, term.field) is not None]
        nulls = [item for item in ordered if get_value(item, term.field) is None]
        non_null.sort(
            key=lambda item: _OrderingValue(get_value(item, term.field)),
            reverse=term.descending,
        )
        ordered = [*non_null, *nulls]
    return ordered


def _manager_identity_for_ordering(item: object) -> object:
    """Return a manager's complete identity when the item exposes one."""
    identification = getattr(item, "identification", None)
    if not isinstance(identification, Mapping):
        return _IDENTITY_UNAVAILABLE
    return {
        "class": _type_identity(item),
        "effective_search_date": getattr(
            item, "_effective_search_date", _IDENTITY_UNAVAILABLE
        ),
        "identification": identification,
    }


def _ordering_identity(value: object) -> tuple[object, ...] | None:
    """Encode supported complete identities without repr or object-address ties."""
    if value is _IDENTITY_UNAVAILABLE:
        return ("missing",)
    if isinstance(value, Mapping):
        pairs: list[tuple[tuple[object, ...], tuple[object, ...]]] = []
        for key, item in value.items():
            normalized_key = _ordering_identity(key)
            normalized_item = _ordering_identity(item)
            if normalized_key is None or normalized_item is None:
                return None
            pairs.append((normalized_key, normalized_item))
        return ("mapping", tuple(sorted(pairs)))
    if isinstance(value, (list, tuple)):
        items = tuple(_ordering_identity(item) for item in value)
        return None if any(item is None for item in items) else ("sequence", items)
    if isinstance(value, (set, frozenset)):
        normalized_set_items: list[tuple[object, ...]] = []
        for item in value:
            normalized_item = _ordering_identity(item)
            if normalized_item is None:
                return None
            normalized_set_items.append(normalized_item)
        return ("set", tuple(sorted(normalized_set_items)))
    if value is None:
        return ("none",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, Decimal):
        if value.is_nan():
            return ("decimal-nan", value.is_signed())
        if value.is_infinite():
            return ("decimal-infinity", value.is_signed())
        return ("decimal", value)
    if isinstance(value, float):
        if isnan(value):
            return ("float-nan", copysign(1.0, value) < 0)
        if isinf(value):
            return ("float-infinity", value < 0)
        return ("float", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, bytes):
        return ("bytes", value)
    if isinstance(value, UUID):
        return ("uuid", value)
    if isinstance(value, (datetime, date, time)):
        return ("temporal", value.isoformat())
    identification = getattr(value, "identification", None)
    if isinstance(identification, Mapping):
        return _ordering_identity(
            {
                "class": _type_identity(value),
                "effective_search_date": getattr(
                    value, "_effective_search_date", _IDENTITY_UNAVAILABLE
                ),
                "identification": identification,
            }
        )
    return None


def _type_identity(value: object) -> str:
    """Return a comparable concrete type label without object representation."""
    value_type = value if isinstance(value, type) else type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def resolve_ordering_value(item: object, field: str) -> object:
    """Resolve a declared relation path, treating a missing relation as null."""
    value = item
    for segment in field.split("__"):
        if value is None:
            return None
        value = getattr(value, segment)
    return value


@dataclass(frozen=True)
class _OrderingValue:
    """Comparable wrapper that avoids unsafe cross-domain scalar comparison."""

    value: object

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _OrderingValue):
            return NotImplemented
        category = _ordering_category(self.value)
        other_category = _ordering_category(other.value)
        if category != other_category:
            return category < other_category
        if category == _OTHER_CATEGORY and type(self.value) is not type(other.value):
            raise UnsupportedOrderingValueError(self.value, other.value)
        try:
            return bool(self.value < other.value)  # type: ignore[operator]
        except TypeError as error:
            raise UnsupportedOrderingValueError(self.value, other.value) from error


_BOOL_CATEGORY = 0
_NUMBER_CATEGORY = 1
_TEMPORAL_CATEGORY = 2
_STRING_CATEGORY = 3
_BYTES_CATEGORY = 4
_OTHER_CATEGORY = 5


def _ordering_category(value: object) -> int:
    """Classify supported scalars without changing their value representation."""
    if isinstance(value, bool):
        return _BOOL_CATEGORY
    if isinstance(value, (Number, Decimal)):
        return _NUMBER_CATEGORY
    if isinstance(value, (datetime, date, time)):
        return _TEMPORAL_CATEGORY
    if isinstance(value, str):
        return _STRING_CATEGORY
    if isinstance(value, bytes):
        return _BYTES_CATEGORY
    return _OTHER_CATEGORY
