from __future__ import annotations

from collections.abc import Iterable
from typing import cast

import pytest

from general_manager.bucket.indexing import (
    MissingBucketIndexKeyError,
    UnsupportedBucketIndexKeySpecError,
    build_multi_bucket_index,
    build_unique_bucket_index,
    freeze_bucket_index_value,
    normalize_bucket_index_key_spec,
    resolve_bucket_index_key,
    validate_bucket_index_max_rows,
)
from general_manager.interface.base_interface import InterfaceBase
from general_manager.manager.general_manager import GeneralManager


class RelatedManager(GeneralManager):
    def __init__(self, pk: int) -> None:
        """Create a manager with a simple integer identification value."""
        self._interface = cast(InterfaceBase, object())
        self._GeneralManager__id = {"id": pk}
        self._manager_state_valid = True
        self._manager_state_reason = None


class MixedIdentificationManager(GeneralManager):
    def __init__(self) -> None:
        """Create a manager whose identification keys use mixed hashable types."""
        self._interface = cast(InterfaceBase, object())
        self._GeneralManager__id = {"id": 10, 2: "external"}
        self._manager_state_valid = True
        self._manager_state_reason = None


class Row:
    def __init__(self, day: str | None, related: object = None) -> None:
        """Create a row object exposing fields used by index-key resolution."""
        self.day = day
        self.related = related


class PropertyFailureError(RuntimeError):
    """Raised by a test property to verify non-AttributeError propagation."""

    def __init__(self) -> None:
        """Create the test property failure."""
        super().__init__("property failed")


class SourceIterationStartedError(RuntimeError):
    """Raised when a test source iterable is consumed unexpectedly."""

    def __init__(self) -> None:
        """Create the test source iteration failure."""
        super().__init__("source iteration started")


class PropertyErrorRow:
    @property
    def broken(self) -> str:
        """Raise a non-AttributeError to verify propagation."""
        raise PropertyFailureError


def test_normalize_bucket_index_key_spec_accepts_field_name() -> None:
    """Normalize a single field name into a non-composite field key spec."""
    assert normalize_bucket_index_key_spec("day") == ("field", ("day",), False)


def test_normalize_bucket_index_key_spec_accepts_composite_tuple() -> None:
    """Normalize a tuple of field names into a composite field key spec."""
    assert normalize_bucket_index_key_spec(("day", "related")) == (
        "field",
        ("day", "related"),
        True,
    )


@pytest.mark.parametrize("key_spec", [(), ("day", 1), ["day"], object()])
def test_normalize_bucket_index_key_spec_rejects_unsupported_specs(
    key_spec: object,
) -> None:
    """Reject key specs that cannot be represented as stable field lookups."""
    with pytest.raises(UnsupportedBucketIndexKeySpecError):
        normalize_bucket_index_key_spec(key_spec)


@pytest.mark.parametrize("max_rows", [None, 0, -1, 1000])
def test_validate_bucket_index_max_rows_accepts_supported_values(
    max_rows: int | None,
) -> None:
    """Accept integer row guardrails and None."""
    assert validate_bucket_index_max_rows(max_rows) == max_rows


@pytest.mark.parametrize("max_rows", [True, False, 1.5, "1000", object()])
def test_validate_bucket_index_max_rows_rejects_unsupported_values(
    max_rows: object,
) -> None:
    """Reject runtime row guardrails outside the public int-or-None contract."""
    with pytest.raises(TypeError, match="max_rows"):
        validate_bucket_index_max_rows(max_rows)


def test_build_unique_bucket_index_rejects_invalid_key_spec_on_empty_rows() -> None:
    """Reject invalid unique-index key specs even when rows are empty."""
    with pytest.raises(UnsupportedBucketIndexKeySpecError):
        build_unique_bucket_index([], ["day"], max_rows=1000)  # type: ignore[arg-type]


def test_build_multi_bucket_index_rejects_invalid_key_spec_on_empty_rows() -> None:
    """Reject invalid grouped-index key specs even when rows are empty."""
    with pytest.raises(UnsupportedBucketIndexKeySpecError):
        build_multi_bucket_index([], ["day"], max_rows=1000)  # type: ignore[arg-type]


def test_build_unique_bucket_index_rejects_key_spec_before_iteration() -> None:
    """Validate unique-index key specs before consuming source iterables."""

    def rows() -> Iterable[Row]:
        raise SourceIterationStartedError
        yield Row("2026-06-15")  # pragma: no cover

    with pytest.raises(UnsupportedBucketIndexKeySpecError):
        build_unique_bucket_index(rows(), ["day"], max_rows=1000)  # type: ignore[arg-type]


def test_build_multi_bucket_index_rejects_key_spec_before_iteration() -> None:
    """Validate grouped-index key specs before consuming source iterables."""

    def rows() -> Iterable[Row]:
        raise SourceIterationStartedError
        yield Row("2026-06-15")  # pragma: no cover

    with pytest.raises(UnsupportedBucketIndexKeySpecError):
        build_multi_bucket_index(rows(), ["day"], max_rows=1000)  # type: ignore[arg-type]


def test_resolve_bucket_index_key_allows_none_key_values() -> None:
    """Allow None as a valid resolved key value."""
    assert resolve_bucket_index_key(Row(None), "day") is None


def test_resolve_bucket_index_key_raises_clear_error_for_missing_field() -> None:
    """Raise a bucket-index-specific error when a row field is missing."""
    with pytest.raises(MissingBucketIndexKeyError, match="missing"):
        resolve_bucket_index_key(Row("2026-06-15"), "missing")


def test_resolve_bucket_index_key_propagates_non_attribute_property_errors() -> None:
    """Propagate property failures that are not AttributeError."""
    with pytest.raises(RuntimeError, match="property failed"):
        resolve_bucket_index_key(PropertyErrorRow(), "broken")


def test_resolve_bucket_index_key_builds_composite_key() -> None:
    """Resolve composite key specs into ordered tuples of field values."""
    row = Row("2026-06-15", related="component")

    assert resolve_bucket_index_key(row, ("day", "related")) == (
        "2026-06-15",
        "component",
    )


def test_freeze_bucket_index_value_normalizes_manager_values() -> None:
    """Represent manager values by class and frozen identification."""
    related = RelatedManager(10)

    assert freeze_bucket_index_value(related) == (
        RelatedManager,
        (("id", 10),),
    )


def test_freeze_bucket_index_value_normalizes_nested_containers() -> None:
    """Recursively freeze nested containers into stable hashable values."""
    value = {"ids": [2, 1], "flags": {True, False}}

    assert freeze_bucket_index_value(value) == (
        ("flags", frozenset({False, True})),
        ("ids", (2, 1)),
    )


def test_freeze_bucket_index_value_normalizes_mixed_type_dict_keys() -> None:
    """Sort frozen dictionary pairs even when original keys have mixed types."""
    value = {"ids": [2, 1], 2: "external"}

    assert freeze_bucket_index_value(value) == (
        (2, "external"),
        ("ids", (2, 1)),
    )


def test_freeze_bucket_index_value_normalizes_mixed_type_manager_identity_keys() -> (
    None
):
    """Sort frozen manager identity pairs when identity keys have mixed types."""
    related = MixedIdentificationManager()

    assert freeze_bucket_index_value(related) == (
        MixedIdentificationManager,
        (
            (2, "external"),
            ("id", 10),
        ),
    )
