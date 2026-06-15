"""Pure helpers for dependency-index lookup parsing and matching."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

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


@dataclass(frozen=True, slots=True)
class LookupSpec:
    lookup: str
    attr_path: tuple[str, ...]
    operator: LookupOperator


def normalize_dependency_value(value: Any) -> Any:
    """Return a deterministic JSON-compatible representation for dependency data."""
    if isinstance(value, dict):
        return {
            str(key): normalize_dependency_value(val)
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [normalize_dependency_value(item) for item in value]
    if isinstance(value, set):
        return [normalize_dependency_value(item) for item in sorted(value, key=str)]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    get_state = getattr(value, "__getstate__", None)
    if callable(get_state):
        state = get_state()
        if isinstance(state, dict):
            return {"__state__": normalize_dependency_value(state)}
    return {"__repr__": repr(value)}


def serialize_normalized_value(value: Any) -> str:
    """Serialize dependency values in the canonical dependency format."""
    return json.dumps(normalize_dependency_value(value), sort_keys=True)


def stable_value_hash(value: Any) -> str:
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


def parse_dependency_identifier(identifier: str) -> Any:
    """Parse a JSON-serialized dependency identifier, returning None on failure."""
    try:
        return json.loads(identifier)
    except (json.JSONDecodeError, ValueError):
        return None


def current_value_for_path(instance: object, attr_path: tuple[str, ...]) -> Any:
    """Resolve a nested attribute path from an instance."""
    current: object = instance
    for attr in attr_path:
        current = getattr(current, attr, UNDEFINED)
        if current is UNDEFINED:
            return None
    return current


def _json_loads_val_key(val_key: Any) -> Any:
    if isinstance(val_key, str):
        try:
            return json.loads(val_key)
        except (json.JSONDecodeError, ValueError):
            return val_key
    return val_key


def _repr_marker(raw: Any) -> str | None:
    if isinstance(raw, dict) and set(raw.keys()) == {"__repr__"}:
        marker = raw.get("__repr__")
        return marker if isinstance(marker, str) else None
    return None


def _coerce_to_type(sample: Any, raw: Any) -> Any | None:
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
    if isinstance(raw, dict) and set(raw.keys()) == {"__state__"}:
        state = raw["__state__"]
        if isinstance(state, dict):
            try:
                return type(sample)(**state)
            except (TypeError, ValueError):
                if {"magnitude", "unit"} <= set(state):
                    try:
                        return type(sample)(state["magnitude"], state["unit"])
                    except (TypeError, ValueError):
                        return None
        return None
    try:
        return type(sample)(raw)
    except (TypeError, ValueError):
        if isinstance(raw, type(sample)):
            return raw
        return None


def matches_lookup_value(operator: str, value: Any, stored_value: Any) -> bool:
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
        try:
            sequence = json.loads(stored_value)
        except (json.JSONDecodeError, ValueError, TypeError):
            return False
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
        if operator == "gt":
            return value > threshold
        if operator == "gte":
            return value >= threshold
        if operator == "lt":
            return value < threshold
        return value <= threshold
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
