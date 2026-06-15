"""Run-scoped bucket index helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable
from typing import TypeVar, cast

T = TypeVar("T")
BucketIndexKeySpec = str | tuple[str, ...]
NormalizedBucketIndexKeySpec = tuple[str, tuple[str, ...], bool]


def _frozen_pair_sort_key(pair: tuple[Hashable, Hashable]) -> tuple[str, str]:
    return (str(pair[0]), str(pair[1]))


class UnsupportedBucketIndexKeySpecError(TypeError):
    """Raised when a bucket index key spec cannot produce a stable run key."""

    def __init__(self, key_spec: object) -> None:
        super().__init__(
            "Bucket index key specs must be a field name or a non-empty tuple "
            f"of field names; got {key_spec!r}."
        )


class MissingBucketIndexKeyError(AttributeError):
    """Raised when an indexed row does not expose a requested key field."""

    def __init__(self, field_name: str, row: object) -> None:
        super().__init__(
            f"Cannot build bucket index: {row!r} is missing key field {field_name!r}."
        )


class DuplicateBucketIndexKeyError(ValueError):
    """Raised when a unique bucket index sees more than one row for a key."""

    def __init__(self, key: Hashable) -> None:
        super().__init__(f"Bucket index key {key!r} matched more than one row.")


class BucketIndexTooLargeError(ValueError):
    """Raised when index construction exceeds the configured row guardrail."""

    def __init__(self, max_rows: int) -> None:
        super().__init__(
            f"Bucket index construction exceeded the configured limit of {max_rows} rows."
        )


class UnhashableBucketIndexKeyError(TypeError):
    """Raised when a key value cannot be converted to a hashable identity."""

    def __init__(self, key: object) -> None:
        super().__init__(f"Bucket index key {key!r} is not hashable.")


def normalize_bucket_index_key_spec(
    key_spec: object,
) -> NormalizedBucketIndexKeySpec:
    """Return a stable normalized representation for a supported key spec."""
    if isinstance(key_spec, str):
        return ("field", (key_spec,), False)
    if (
        isinstance(key_spec, tuple)
        and len(key_spec) > 0
        and all(isinstance(part, str) for part in key_spec)
    ):
        return ("field", key_spec, True)
    raise UnsupportedBucketIndexKeySpecError(key_spec)


def freeze_bucket_index_value(value: object) -> Hashable:
    """Return a hashable identity for values used as bucket index keys."""
    from general_manager.manager.general_manager import GeneralManager

    if isinstance(value, GeneralManager):
        return (
            value.__class__,
            tuple(
                sorted(
                    (
                        (
                            cast(Hashable, freeze_bucket_index_value(key)),
                            freeze_bucket_index_value(identifier),
                        )
                        for key, identifier in value.identification.items()
                    ),
                    key=_frozen_pair_sort_key,
                )
            ),
        )
    if isinstance(value, dict):
        return tuple(
            sorted(
                (
                    (
                        cast(Hashable, freeze_bucket_index_value(key)),
                        freeze_bucket_index_value(item),
                    )
                    for key, item in value.items()
                ),
                key=_frozen_pair_sort_key,
            )
        )
    if isinstance(value, (list, tuple)):
        return tuple(freeze_bucket_index_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze_bucket_index_value(item) for item in value)
    try:
        hash(value)
    except TypeError as error:
        raise UnhashableBucketIndexKeyError(value) from error
    return cast(Hashable, value)


def resolve_bucket_index_key(
    row: object,
    key_spec: BucketIndexKeySpec,
) -> Hashable:
    """Resolve and freeze the index key for one row."""
    _, field_names, composite = normalize_bucket_index_key_spec(key_spec)
    values: list[Hashable] = []
    for field_name in field_names:
        try:
            value = getattr(row, field_name)
        except AttributeError as error:
            raise MissingBucketIndexKeyError(field_name, row) from error
        values.append(freeze_bucket_index_value(value))
    if composite:
        return tuple(values)
    return values[0]


def _iter_guarded_rows(
    rows: Iterable[T],
    max_rows: int | None,
) -> Iterable[T]:
    for index, row in enumerate(rows):
        if max_rows is not None and index >= max_rows:
            raise BucketIndexTooLargeError(max_rows)
        yield row


def build_unique_bucket_index(
    rows: Iterable[T],
    key_spec: BucketIndexKeySpec,
    *,
    max_rows: int | None,
) -> dict[Hashable, T]:
    """Build a unique index and fail when duplicate keys are found."""
    indexed: dict[Hashable, T] = {}
    for row in _iter_guarded_rows(rows, max_rows):
        key = resolve_bucket_index_key(row, key_spec)
        if key in indexed:
            raise DuplicateBucketIndexKeyError(key)
        indexed[key] = row
    return indexed


def build_multi_bucket_index(
    rows: Iterable[T],
    key_spec: BucketIndexKeySpec,
    *,
    max_rows: int | None,
) -> dict[Hashable, tuple[T, ...]]:
    """Build an index that preserves all rows for duplicate keys."""
    grouped: defaultdict[Hashable, list[T]] = defaultdict(list)
    for row in _iter_guarded_rows(rows, max_rows):
        grouped[resolve_bucket_index_key(row, key_spec)].append(row)
    return {key: tuple(values) for key, values in grouped.items()}
