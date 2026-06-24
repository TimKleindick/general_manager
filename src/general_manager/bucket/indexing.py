"""Run-scoped bucket index support for `Bucket.index_by` and `index_many`.

The stable public bucket API exposes the bucket methods and bucket-index
exception classes. The aliases and functions in this module are importable
implementation helpers used by bucket and run-cache internals; they are not
documented as public API exports.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Iterable
from typing import TypeVar

T = TypeVar("T")
BucketIndexKeySpec = str | tuple[str, ...]
NormalizedBucketIndexKeySpec = tuple[str, tuple[str, ...], bool]


def _frozen_pair_sort_key(pair: tuple[Hashable, Hashable]) -> tuple[str, str]:
    """Return a string-only sort key for frozen mapping pairs."""
    return (str(pair[0]), str(pair[1]))


class UnsupportedBucketIndexKeySpecError(TypeError):
    """Raised when a bucket index key spec cannot produce a stable run key.

    The exception type is public; constructor arguments and message text are
    diagnostic details rather than a stable inspection API.
    """

    def __init__(self, key_spec: object) -> None:
        """Build an error that names the unsupported key specification."""
        super().__init__(
            "Bucket index key specs must be a field name or a non-empty tuple "
            f"of field names; got {key_spec!r}."
        )


class MissingBucketIndexKeyError(AttributeError):
    """Raised when an indexed row does not expose a requested key field.

    The exception type is public; constructor arguments and message text are
    diagnostic details rather than a stable inspection API.
    """

    def __init__(self, field_name: str, row: object) -> None:
        """Build an error that identifies the missing row field."""
        super().__init__(
            f"Cannot build bucket index: {row!r} is missing key field {field_name!r}."
        )


class DuplicateBucketIndexKeyError(ValueError):
    """Raised when a unique bucket index sees more than one row for a key.

    The exception type is public; constructor arguments and message text are
    diagnostic details rather than a stable inspection API.
    """

    def __init__(self, key: Hashable) -> None:
        """Build an error that identifies the duplicated frozen index key."""
        super().__init__(f"Bucket index key {key!r} matched more than one row.")


class BucketIndexTooLargeError(ValueError):
    """Raised when index construction exceeds the configured row guardrail.

    The exception type is public; constructor arguments and message text are
    diagnostic details rather than a stable inspection API.
    """

    def __init__(self, max_rows: int) -> None:
        """Build an error that reports the row guardrail that was exceeded."""
        super().__init__(
            f"Bucket index construction exceeded the configured limit of {max_rows} rows."
        )


class UnhashableBucketIndexKeyError(TypeError):
    """Raised when a key value cannot be converted to a hashable identity.

    The exception type is public; constructor arguments and message text are
    diagnostic details rather than a stable inspection API.
    """

    def __init__(self, key: object) -> None:
        """Build an error that reports the unfreezable key value."""
        super().__init__(f"Bucket index key {key!r} is not hashable.")


class InvalidBucketIndexMaxRowsError(TypeError):
    """Internal `TypeError` for unsupported bucket index row guardrail values."""

    def __init__(self) -> None:
        """Build an error that explains the supported row guardrail values."""
        super().__init__("Bucket index max_rows must be an int or None.")


def validate_bucket_index_max_rows(max_rows: object) -> int | None:
    """Return a supported row guardrail value or raise a deliberate error.

    Args:
        max_rows: Candidate maximum row count. `None` disables the guardrail.

    Returns:
        The supplied integer value, or `None`.

    Raises:
        TypeError: If `max_rows` is not an integer or `None`. Booleans are
            rejected even though `bool` subclasses `int`.
    """
    if max_rows is None:
        return None
    if isinstance(max_rows, bool) or not isinstance(max_rows, int):
        raise InvalidBucketIndexMaxRowsError
    return max_rows


def normalize_bucket_index_key_spec(
    key_spec: object,
) -> NormalizedBucketIndexKeySpec:
    """Return a stable normalized representation for a supported key spec.

    Args:
        key_spec: Either one string field name or a non-empty tuple of string
            field names. Empty strings are accepted and resolved like any other
            attribute name.

    Returns:
        A tuple containing the key mode, requested field names, and whether the
        key is composite.

    Raises:
        UnsupportedBucketIndexKeySpecError: If the key spec is not a string or
            a non-empty tuple containing only strings.
    """
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
    """Return a stable hashable identity for values used as index keys.

    Manager instances are represented by their class and identification, and
    containers are recursively frozen so equivalent values share a run-cache key.

    Args:
        value: Field value to normalize into an index key component.

    Returns:
        A hashable representation of the value that can be used in dictionaries
        and run-cache signatures: managers become
        `(class, sorted_identification_pairs)`, dictionaries become sorted
        tuples of frozen key/value pairs using a stable string sort key for
        mixed comparable types, with equal sort keys preserving the source
        mapping's iteration order; lists and tuples become tuples; sets become
        frozensets; and already-hashable values, including frozensets and
        dataclass instances that define a hash, are returned unchanged.
        Subclasses of `dict`, `list`, `tuple`, and `set` follow their parent
        container behavior; other mapping and sequence implementations are
        treated as ordinary objects. Only instances of
        `general_manager.manager.general_manager.GeneralManager` use manager
        identity handling. Errors raised while reading manager identification or
        iterating its `.items()` propagate unchanged; malformed non-mapping
        identification values fail through their normal Python errors, and
        unhashable identification contents raise
        `UnhashableBucketIndexKeyError` when freezing reaches them. The
        representation is intended for same-process lookup identity, not
        persistent serialization.

    Raises:
        UnhashableBucketIndexKeyError: If a non-container value still cannot be
            hashed after normalization.
    """
    from general_manager.manager.general_manager import GeneralManager

    if isinstance(value, GeneralManager):
        return (
            value.__class__,
            tuple(
                sorted(
                    (
                        (
                            freeze_bucket_index_value(key),
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
                        freeze_bucket_index_value(key),
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
    return value


def resolve_bucket_index_key(
    row: object,
    key_spec: BucketIndexKeySpec,
) -> Hashable:
    """Resolve the requested field value or composite key for one row.

    Args:
        row: Object exposing the fields named by `key_spec` through attribute
            lookup. Mapping keys are not read unless the row also exposes them
            as attributes. `AttributeError` from `getattr`, including one raised
            by a descriptor or property getter, is reported as a missing bucket
            index key. Other descriptor or property getter exceptions propagate
            unchanged.
        key_spec: One field name or a non-empty tuple of field names. Empty
            strings are accepted and passed to `getattr`.

    Returns:
        The frozen field value for a single-field spec, or a tuple of frozen
        field values for a composite spec.

    Raises:
        MissingBucketIndexKeyError: If `row` lacks a requested field.
        UnsupportedBucketIndexKeySpecError: If `key_spec` is not supported.
        UnhashableBucketIndexKeyError: If a field value cannot be converted to a
            hashable identity.
    """
    _, field_names, composite = normalize_bucket_index_key_spec(key_spec)
    return _resolve_normalized_bucket_index_key(row, field_names, composite)


def _resolve_normalized_bucket_index_key(
    row: object,
    field_names: tuple[str, ...],
    composite: bool,
) -> Hashable:
    """Resolve a row key after the caller has normalized the key spec."""
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
    """Yield rows until `max_rows` is exceeded, then raise a guardrail error."""
    max_rows = validate_bucket_index_max_rows(max_rows)
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
    """Build a unique index from frozen row keys to rows.

    Args:
        rows: Source rows to index.
        key_spec: One field name or a non-empty tuple of field names. Empty
            strings are accepted and passed to `getattr`.
        max_rows: Maximum number of rows allowed before construction fails, or
            `None` to disable the guardrail. Runtime callers must pass an
            integer or `None`; booleans and other values raise `TypeError`.

        Returns:
        A dictionary mapping each frozen key to the single matching row.
        Empty source iterables return an empty dictionary.

    Raises:
        BucketIndexTooLargeError: If more than `max_rows` rows are read.
        DuplicateBucketIndexKeyError: If two rows resolve to the same key.
        MissingBucketIndexKeyError: If a row lacks a requested key field.
        UnsupportedBucketIndexKeySpecError: If `key_spec` is not supported.
        TypeError: If `max_rows` is not an integer or `None`.
        UnhashableBucketIndexKeyError: If a key value cannot be frozen.
        Exception: Exceptions raised by source iteration propagate unchanged.
            Validation order is `max_rows`, then `key_spec`, then source
            iteration. During iteration, the row guardrail is checked before key
            resolution for each row, so guardrail errors take precedence over
            duplicate, missing-field, or unhashable-key errors on the first row
            past the limit.
    """
    _, field_names, composite = normalize_bucket_index_key_spec(key_spec)
    indexed: dict[Hashable, T] = {}
    for row in _iter_guarded_rows(rows, max_rows):
        key = _resolve_normalized_bucket_index_key(row, field_names, composite)
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
    """Build a grouped index from frozen row keys to matching row tuples.

    Args:
        rows: Source rows to index.
        key_spec: One field name or a non-empty tuple of field names. Empty
            strings are accepted and passed to `getattr`.
        max_rows: Maximum number of rows allowed before construction fails, or
            `None` to disable the guardrail. Runtime callers must pass an
            integer or `None`; booleans and other values raise `TypeError`.

    Returns:
        A dictionary mapping each frozen key to matching rows in source order.
        Empty source iterables return an empty dictionary.

    Raises:
        BucketIndexTooLargeError: If more than `max_rows` rows are read.
        MissingBucketIndexKeyError: If a row lacks a requested key field.
        UnsupportedBucketIndexKeySpecError: If `key_spec` is not supported.
        TypeError: If `max_rows` is not an integer or `None`.
        UnhashableBucketIndexKeyError: If a key value cannot be frozen.
        Exception: Exceptions raised by source iteration propagate unchanged.
            Validation order is `max_rows`, then `key_spec`, then source
            iteration. During iteration, the row guardrail is checked before key
            resolution for each row, so guardrail errors take precedence over
            missing-field or unhashable-key errors on the first row past the
            limit.
    """
    _, field_names, composite = normalize_bucket_index_key_spec(key_spec)
    grouped: defaultdict[Hashable, list[T]] = defaultdict(list)
    for row in _iter_guarded_rows(rows, max_rows):
        key = _resolve_normalized_bucket_index_key(row, field_names, composite)
        grouped[key].append(row)
    return {key: tuple(values) for key, values in grouped.items()}
