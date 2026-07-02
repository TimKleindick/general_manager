"""Pure helpers for dependency-index lookup parsing and matching."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass
from datetime import date, datetime
from json.encoder import encode_basestring_ascii
from typing import Literal, Protocol, cast

LookupOperator = Literal[
    "eq",
    "in",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "startswith",
    "endswith",
    "regex",
]

EXACT_OPERATORS = frozenset({"eq"})
SCAN_OPERATORS = frozenset(
    {"in", "gt", "gte", "lt", "lte", "contains", "startswith", "endswith", "regex"}
)
SUPPORTED_LOOKUP_OPERATORS = EXACT_OPERATORS | SCAN_OPERATORS
UNDEFINED = object()
_SCALAR_NORMALIZATION_MISS = object()
_SCALAR_SERIALIZATION_MISS = object()
_MAPPING_SERIALIZATION_MISS = object()


type NormalizedDependencyValue = (
    str
    | int
    | float
    | bool
    | None
    | list[NormalizedDependencyValue]
    | dict[str, NormalizedDependencyValue]
)


class SupportsDependencyComparison(Protocol):
    """Protocol for values that support dependency range comparisons."""

    def __lt__(self, other: object, /) -> bool:
        """Return whether this value is less than the other value."""
        ...

    def __le__(self, other: object, /) -> bool:
        """Return whether this value is less than or equal to the other value."""
        ...

    def __gt__(self, other: object, /) -> bool:
        """Return whether this value is greater than the other value."""
        ...

    def __ge__(self, other: object, /) -> bool:
        """Return whether this value is greater than or equal to the other value."""
        ...


def _normalize_scalar_dependency_value(value: object) -> object:
    """
    Return the scalar-only equivalent of `normalize_dependency_value`.

    This avoids the slower general path for simple scalars only, and its output
    must remain equivalent to full normalization for every value it accepts.
    """

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _SCALAR_NORMALIZATION_MISS


def _serialize_scalar_dependency_value(value: object) -> str | object:
    """
    Serialize simple scalars without the slower generic JSON path.

    Outputs must remain equivalent to full normalization followed by
    `json.dumps(..., sort_keys=True)`. Floats are intentionally excluded so the
    caller can preserve Python's JSON spelling for finite and non-finite values.
    """

    if isinstance(value, str):
        return encode_basestring_ascii(value)
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, date):
        return encode_basestring_ascii(value.isoformat())
    return _SCALAR_SERIALIZATION_MISS


def _serialize_simple_dependency_value(value: object) -> str | object:
    """Serialize only values matching the full encoder, else return the miss sentinel."""

    normalized_value = _normalize_scalar_dependency_value(value)
    if normalized_value is not _SCALAR_NORMALIZATION_MISS:
        serialized_value = _serialize_scalar_dependency_value(normalized_value)
        if serialized_value is not _SCALAR_SERIALIZATION_MISS:
            return serialized_value
        if isinstance(normalized_value, float):
            return json.dumps(normalized_value)
    if isinstance(value, Mapping):
        return _serialize_simple_dependency_mapping(value)
    return _MAPPING_SERIALIZATION_MISS


def _serialize_simple_dependency_mapping(
    value: Mapping[object, object],
) -> str | object:
    """Serialize mappings or return `_MAPPING_SERIALIZATION_MISS` on ambiguity."""

    if not value:
        return "{}"
    if len(value) == 1:
        raw_key, raw_value = next(iter(value.items()))
        serialized_value = _serialize_simple_dependency_value(raw_value)
        if serialized_value is _MAPPING_SERIALIZATION_MISS:
            return _MAPPING_SERIALIZATION_MISS
        return (
            "{"
            f"{encode_basestring_ascii(str(raw_key))}: {cast(str, serialized_value)}"
            "}"
        )
    if len(value) == 2:
        iterator = iter(value.items())
        first_key, first_value = next(iterator)
        second_key, second_value = next(iterator)
        first_key_str = str(first_key)
        second_key_str = str(second_key)
        if first_key_str == second_key_str:
            return _MAPPING_SERIALIZATION_MISS
        if first_key_str < second_key_str:
            first_serialized = _serialize_simple_dependency_value(first_value)
            if first_serialized is _MAPPING_SERIALIZATION_MISS:
                return _MAPPING_SERIALIZATION_MISS
            second_serialized = _serialize_simple_dependency_value(second_value)
            if second_serialized is _MAPPING_SERIALIZATION_MISS:
                return _MAPPING_SERIALIZATION_MISS
            return (
                "{"
                f"{encode_basestring_ascii(first_key_str)}: "
                f"{cast(str, first_serialized)}, "
                f"{encode_basestring_ascii(second_key_str)}: "
                f"{cast(str, second_serialized)}"
                "}"
            )
    parts: list[str] = []
    seen_keys: set[str] = set()
    for raw_key, raw_value in sorted(value.items(), key=lambda item: str(item[0])):
        key = str(raw_key)
        if key in seen_keys:
            return _MAPPING_SERIALIZATION_MISS
        seen_keys.add(key)
        serialized_value = _serialize_simple_dependency_value(raw_value)
        if serialized_value is _MAPPING_SERIALIZATION_MISS:
            return _MAPPING_SERIALIZATION_MISS
        parts.append(f"{encode_basestring_ascii(key)}: {cast(str, serialized_value)}")
    return "{" + ", ".join(parts) + "}"


@dataclass(frozen=True, slots=True)
class LookupSpec:
    """Parsed dependency lookup path and operator."""

    lookup: str
    attr_path: tuple[str, ...]
    operator: LookupOperator


def normalize_dependency_value(value: object) -> NormalizedDependencyValue:
    """Return a deterministic JSON-compatible representation for dependency data."""
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        if len(mapping) == 1:
            key, val = next(iter(mapping.items()))
            normalized_value = _normalize_scalar_dependency_value(val)
            if normalized_value is not _SCALAR_NORMALIZATION_MISS:
                return {str(key): cast(NormalizedDependencyValue, normalized_value)}
        return {
            str(key): normalize_dependency_value(val)
            for key, val in sorted(mapping.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        sequence = cast(Sequence[object], value)
        return [normalize_dependency_value(item) for item in sequence]
    if isinstance(value, AbstractSet):
        value_set = cast(AbstractSet[object], value)
        return [normalize_dependency_value(item) for item in sorted(value_set, key=str)]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    get_state = getattr(value, "__getstate__", None)
    if callable(get_state):
        state = get_state()
        if isinstance(state, Mapping):
            return {"__state__": normalize_dependency_value(state)}
    return {"__repr__": repr(value)}


def serialize_normalized_value(value: object) -> str:
    """Serialize dependency values in the canonical dependency format."""
    serialized_scalar = _serialize_scalar_dependency_value(value)
    if serialized_scalar is not _SCALAR_SERIALIZATION_MISS:
        return cast(str, serialized_scalar)
    if isinstance(value, float):
        return json.dumps(value)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        serialized_mapping = _serialize_simple_dependency_mapping(mapping)
        if serialized_mapping is not _MAPPING_SERIALIZATION_MISS:
            return cast(str, serialized_mapping)
    return json.dumps(normalize_dependency_value(value), sort_keys=True)


def stable_value_hash(value: object) -> str:
    """Return a stable hash suitable for shard keys."""
    payload = serialize_normalized_value(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def lookup_spec_from_key(lookup: str) -> LookupSpec:
    """Split a dependency lookup into an attribute path and lookup operator."""
    parts = tuple(lookup.split("__"))
    tail = parts[-1]
    if tail in SUPPORTED_LOOKUP_OPERATORS - {"eq"}:
        return LookupSpec(
            lookup=lookup,
            attr_path=parts[:-1],
            operator=tail,  # type: ignore[arg-type]
        )
    return LookupSpec(lookup=lookup, attr_path=parts, operator="eq")


def parse_dependency_identifier(identifier: str) -> object | None:
    """Parse a JSON-serialized dependency identifier, returning None on failure."""
    try:
        return cast(object, json.loads(identifier))
    except (json.JSONDecodeError, ValueError):
        return None


def current_value_for_path(
    instance: object, attr_path: tuple[str, ...]
) -> object | None:
    """Resolve a nested attribute path from an instance."""
    current: object = instance
    for attr in attr_path:
        current = getattr(current, attr, UNDEFINED)
        if current is UNDEFINED:
            return None
    return current


def _json_loads_val_key(val_key: object) -> object:
    if isinstance(val_key, str):
        try:
            return cast(object, json.loads(val_key))
        except (json.JSONDecodeError, ValueError):
            return val_key
    return val_key


def _repr_marker(raw: object) -> str | None:
    if isinstance(raw, Mapping) and set(raw.keys()) == {"__repr__"}:
        marker = raw.get("__repr__")
        return marker if isinstance(marker, str) else None
    return None


def _coerce_to_type(sample: object, raw: object) -> object | None:
    if sample is None:
        return None
    if isinstance(sample, datetime):
        if isinstance(raw, datetime):
            parsed = raw
        elif isinstance(raw, str):
            candidate = raw.replace("Z", "+00:00").replace(" ", "T", 1)
            try:
                parsed = datetime.fromisoformat(candidate)
            except ValueError:
                return None
        else:
            return None
        if sample.tzinfo and parsed.tzinfo is None:
            return parsed.replace(tzinfo=sample.tzinfo)
        if not sample.tzinfo and parsed.tzinfo is not None:
            return parsed.replace(tzinfo=None)
        return parsed
    if isinstance(sample, date) and not isinstance(sample, datetime):
        if isinstance(raw, date) and not isinstance(raw, datetime):
            return raw
        if isinstance(raw, str):
            try:
                return date.fromisoformat(raw)
            except ValueError:
                return None
        return None
    if isinstance(sample, bool):
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, int):
            return bool(raw)
        if isinstance(raw, str):
            normalized = raw.strip().lower()
            if normalized in {"true", "1", "yes", "y", "t"}:
                return True
            if normalized in {"false", "0", "no", "n", "f"}:
                return False
        return None
    if isinstance(raw, Mapping) and set(raw.keys()) == {"__state__"}:
        state = raw["__state__"]
        if isinstance(state, Mapping):
            state_mapping = cast(Mapping[str, object], state)
            sample_constructor = cast(Callable[..., object], type(sample))
            try:
                return sample_constructor(**state_mapping)
            except (TypeError, ValueError):
                if {"magnitude", "unit"} <= set(state_mapping):
                    try:
                        return sample_constructor(
                            state_mapping["magnitude"],
                            state_mapping["unit"],
                        )
                    except (TypeError, ValueError):
                        return None
        return None
    try:
        sample_constructor = cast(Callable[[object], object], type(sample))
        return sample_constructor(raw)
    except (TypeError, ValueError):
        if isinstance(raw, type(sample)):
            return raw
        return None


def matches_lookup_value(operator: str, value: object, stored_value: object) -> bool:
    """Return whether a runtime value matches a serialized dependency value."""
    if operator == "eq":
        literal_val = _json_loads_val_key(stored_value)
        if literal_val is None:
            return value is None
        repr_marker = _repr_marker(literal_val)
        if repr_marker is not None:
            return repr(value) == repr_marker
        comparable = _coerce_to_type(value, literal_val)
        if comparable is None:
            return repr(value) == stored_value
        return value == comparable
    if operator == "in":
        if not isinstance(stored_value, str | bytes | bytearray):
            return False
        try:
            raw_sequence = json.loads(stored_value)
        except (json.JSONDecodeError, ValueError, TypeError):
            return False
        if not isinstance(raw_sequence, list):
            return False
        sequence = cast(list[object], raw_sequence)
        for item in sequence:
            if item is None and value is None:
                return True
            repr_marker = _repr_marker(item)
            if repr_marker is not None and repr(value) == repr_marker:
                return True
            comparable = _coerce_to_type(value, item)
            if comparable is not None and value == comparable:
                return True
            if comparable is None and repr(value) == repr(item):
                return True
        return False
    if operator in {"gt", "gte", "lt", "lte"}:
        if value is None:
            return False
        threshold = _coerce_to_type(value, _json_loads_val_key(stored_value))
        if threshold is None:
            return False
        comparable = cast(SupportsDependencyComparison, value)
        if operator == "gt":
            return comparable > threshold
        if operator == "gte":
            return comparable >= threshold
        if operator == "lt":
            return comparable < threshold
        return comparable <= threshold
    if operator in {"contains", "startswith", "endswith", "regex"}:
        if value is None:
            return False
        literal = _json_loads_val_key(stored_value)
        text = str(value)
        pattern_text = literal if isinstance(literal, str) else str(literal)
        if operator == "contains":
            return pattern_text in text
        if operator == "startswith":
            return text.startswith(pattern_text)
        if operator == "endswith":
            return text.endswith(pattern_text)
        try:
            return bool(re.compile(pattern_text).search(text))
        except re.error:
            return False
    return False
