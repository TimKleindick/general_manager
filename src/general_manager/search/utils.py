"""Utility helpers for search indexing."""

from __future__ import annotations

import json
from typing import Any, Iterable

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager


def normalize_identification(identification: dict[str, Any]) -> str:
    """
    Produce a deterministic JSON string for an identification dictionary suitable for document IDs.

    Parameters:
        identification (dict[str, Any]): Mapping of identifying fields to include in the ID.

    Returns:
        serialized (str): JSON string with keys sorted; non-JSON-serializable values are converted using `str()`.
    """
    return json.dumps(identification, sort_keys=True, default=str)


def build_document_id(type_label: str, identification: dict[str, Any]) -> str:
    """
    Create a stable, namespaced document identifier for search indexing.

    Returns:
        document_id (str): A string in the form "type_label:normalized_identification" where `normalized_identification` is a deterministic JSON serialization of the `identification` dictionary with keys sorted and non-serializable values converted to strings.
    """
    normalized = normalize_identification(identification)
    return f"{type_label}:{normalized}"


def _normalize_scalar(value: Any) -> Any:
    """
    Convert a scalar field value into a form suitable for indexing.

    Parameters:
        value: The value to normalize; if it's a GeneralManager, its `identification` is returned.

    Returns:
        The normalized value: the `identification` for GeneralManager instances, otherwise the original `value`.
    """
    if isinstance(value, GeneralManager):
        return value.identification
    return value


def _extract_list(values: Iterable[Any], remaining: str | None) -> list[Any]:
    """
    Apply optional nested extraction to each item in an iterable and normalize each result for indexing.

    Parameters:
        values (Iterable[Any]): Iterable of items to process.
        remaining (str | None): Django-style field path to extract from each item; if None the item itself is used.

    Returns:
        list[Any]: List of normalized values, in the same order as the input iterable.
    """
    results: list[Any] = []
    for entry in values:
        if remaining:
            extracted = extract_value(entry, remaining)
        else:
            extracted = entry
        results.append(_normalize_scalar(extracted))
    return results


def extract_value(obj: Any, field_path: str) -> Any:
    """
    Extract a nested value from an object using a Django-style `__` path.

    Parameters:
        obj: Root object to traverse. May be a mapping, object with attributes, iterable, or a Bucket-like collection.
        field_path: Dot-less path where components are separated by `__`. An empty string returns the normalized `obj`.

    Returns:
        The extracted value. If traversal encounters a collection, a list of normalized values is returned. If any path segment is missing or an intermediate value is `None`, returns `None`. If the final value is a `GeneralManager` instance, its identification is returned instead of the object itself.
    """
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
