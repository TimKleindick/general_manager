"""Utility helpers for search indexing."""

from __future__ import annotations

import json
from typing import Any, Iterable

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager


def normalize_identification(identification: dict[str, Any]) -> str:
    """Serialize identification deterministically for use in document IDs."""
    return json.dumps(identification, sort_keys=True, default=str)


def build_document_id(type_label: str, identification: dict[str, Any]) -> str:
    """Build a stable document ID across managers and indexes."""
    normalized = normalize_identification(identification)
    return f"{type_label}:{normalized}"


def _normalize_scalar(value: Any) -> Any:
    """Normalize field values for indexing."""
    if isinstance(value, GeneralManager):
        return value.identification
    return value


def _extract_list(values: Iterable[Any], remaining: str | None) -> list[Any]:
    results: list[Any] = []
    for entry in values:
        if remaining:
            extracted = extract_value(entry, remaining)
        else:
            extracted = entry
        results.append(_normalize_scalar(extracted))
    return results


def extract_value(obj: Any, field_path: str) -> Any:
    """Extract a nested attribute value using Django-style path separators."""
    parts = field_path.split("__") if field_path else []
    current: Any = obj
    for idx, part in enumerate(parts):
        if current is None:
            return None
        if isinstance(current, Bucket):
            remaining = "__".join(parts[idx:])
            return _extract_list(current, remaining)
        if isinstance(current, (list, tuple, set)):
            remaining = "__".join(parts[idx:])
            return _extract_list(current, remaining)
        if isinstance(current, dict):
            current = current.get(part)
            continue
        if hasattr(current, part):
            current = getattr(current, part)
            continue
        return None
    return _normalize_scalar(current)
