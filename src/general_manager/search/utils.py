"""Utility helpers for search indexing."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager


def normalize_identification(identification: Mapping[str, object]) -> str:
    """
    Serialize manager identification into a deterministic document-id fragment.

    This is a thin `json.dumps()` wrapper using `sort_keys=True` and
    `default=str`; all other encoder options keep their Python defaults,
    including spaces after separators, `ensure_ascii=True`, and
    `allow_nan=True`. Keys are sorted to make equivalent identification
    mappings produce the same string regardless of insertion order when their
    keys are comparable. Non-string keys follow `json.dumps(sort_keys=True)`
    key handling; Python's JSON encoder still restricts mapping key types, and
    `default=str` applies to values rather than unsupported keys. Mixed key
    types may raise `TypeError` when they cannot be sorted. Values that the
    standard JSON encoder cannot serialize are converted with `str()`, so
    custom value determinism depends on a stable `__str__`.

    Parameters:
        identification: Mapping of identifying fields to include in the
            document ID.

    Returns:
        JSON string with sorted keys.

    Raises:
        TypeError: Propagated from `json.dumps()` for unsupported mapping keys
            or values that cannot be represented even after `default=str`.
        ValueError: Propagated from `json.dumps()` for invalid JSON encoding
            inputs.
    """
    return json.dumps(identification, sort_keys=True, default=str)


def build_document_id(type_label: str, identification: Mapping[str, object]) -> str:
    """
    Create a stable, namespaced document identifier for search indexing.

    The result combines the manager type label and normalized identification
    with a colon separator. The function accepts any string `type_label`,
    including an empty string or labels containing colons, and does not escape
    or disambiguate that value. Custom backends that need a restricted
    identifier character set should normalize the returned string at the
    adapter boundary.

    Parameters:
        type_label: Manager type label used as the document namespace.
        identification: Manager identification mapping to normalize.

    Returns:
        String in the form `"type_label:normalized_identification"`.

    Raises:
        TypeError: Propagated from `normalize_identification()`.
        ValueError: Propagated from `normalize_identification()`.
    """
    normalized = normalize_identification(identification)
    return f"{type_label}:{normalized}"


def _normalize_scalar(value: object) -> object:
    """
    Convert a scalar field value into a form suitable for indexing.

    Parameters:
        value: Value to normalize. `GeneralManager` instances are replaced with
            their `identification` mapping. Every other value, including
            mappings, buckets, and collections, is returned unchanged.

    Returns:
        `value.identification` for manager instances, otherwise the original
        value.
    """
    if isinstance(value, GeneralManager):
        return value.identification
    return value


def _extract_list(values: Iterable[object], remaining: str | None) -> list[object]:
    """
    Apply optional nested extraction to each item in an iterable and normalize each result for indexing.

    Parameters:
        values: Iterable of items to process.
        remaining: Django-style field path to extract from each item. When
            `None`, each item itself is normalized.

    Returns:
        Normalized values in the same order as the input iterable.

    Raises:
        Exception: Exceptions raised while iterating `values`, reading bucket
            entries, resolving attributes, or recursively extracting nested
            values propagate unchanged.
    """
    results: list[object] = []
    for entry in values:
        if remaining:
            extracted = extract_value(entry, remaining)
        else:
            extracted = entry
        results.append(_normalize_scalar(extracted))
    return results


def extract_value(obj: object, field_path: str) -> object:
    """
    Extract a nested value from an object using a Django-style `__` path.

    Traversal precedence is `Bucket`, then list/tuple/set collection, then
    mapping, then object attribute. Only `Bucket`, `list`, `tuple`, and `set`
    collections are expanded; arbitrary iterables are treated as plain objects.
    When traversal reaches a bucket or supported collection, the remaining path
    is applied to each yielded item and a list is returned. Bucket item order
    and item shape follow the concrete bucket's iterator. Nested collections
    produce nested lists; extraction does not flatten. Set ordering follows the
    set's iteration order and is not normalized. Missing mapping keys, mapping
    keys present with value `None`, attributes present with value `None`,
    missing attributes, `None` intermediates, and empty paths are handled
    deterministically: missing/`None` returns `None`, and an empty path returns
    `_normalize_scalar(obj)` without traversing into mappings, collections,
    buckets, or plain objects, so non-manager buckets and collections are
    returned unchanged. Mapping lookup uses `current.get(part)` with the string
    path component and does not fall through to attribute lookup after a missing
    key. Empty path components are literal names, so `"a__"` looks up an empty
    key/attribute after `a` and `"__a"` starts with an empty key/attribute.
    Attribute traversal first calls `hasattr(current, part)` and then
    `getattr(current, part)`, so an `AttributeError` from a property can make
    the attribute look missing while other property/descriptor exceptions
    propagate.

    Parameters:
        obj: Root object to traverse.
        field_path: Dot-less path where components are separated by `__`.

    Returns:
        Extracted and normalized value. Collections produce a list. Final
        `GeneralManager` instances become their exact `identification` mapping
        object as stored on the instance. A manager constructed with `id=1`
        commonly returns `{"id": 1}`, while composite identifiers return every
        identifying key/value pair.

    Raises:
        Exception: Exceptions from bucket iteration, custom `__iter__`,
            `hasattr()`, or property/descriptor access propagate unchanged.
    """
    parts = field_path.split("__") if field_path else []
    current: object = obj
    for idx, part in enumerate(parts):
        if current is None:
            return None
        if isinstance(current, Bucket):
            remaining = "__".join(parts[idx:])
            return _extract_list(current, remaining)
        if isinstance(current, (list, tuple, set)):
            remaining = "__".join(parts[idx:])
            return _extract_list(current, remaining)
        if isinstance(current, Mapping):
            current = current.get(part)
            continue
        if hasattr(current, part):
            current = getattr(current, part)
            continue
        return None
    return _normalize_scalar(current)
