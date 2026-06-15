from __future__ import annotations

import pytest

from general_manager.bucket.indexing import (
    MissingBucketIndexKeyError,
    UnsupportedBucketIndexKeySpecError,
    freeze_bucket_index_value,
    normalize_bucket_index_key_spec,
    resolve_bucket_index_key,
)
from general_manager.manager.general_manager import GeneralManager


class RelatedManager(GeneralManager):
    def __init__(self, pk: int) -> None:
        """Create a manager with a simple integer identification value."""
        self._interface = object()
        self._GeneralManager__id = {"id": pk}
        self._manager_state_valid = True
        self._manager_state_reason = None


class MixedIdentificationManager(GeneralManager):
    def __init__(self) -> None:
        """Create a manager whose identification keys use mixed hashable types."""
        self._interface = object()
        self._GeneralManager__id = {"id": 10, 2: "external"}
        self._manager_state_valid = True
        self._manager_state_reason = None


class Row:
    def __init__(self, day: str | None, related: object = None) -> None:
        """Create a row object exposing fields used by index-key resolution."""
        self.day = day
        self.related = related


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


def test_resolve_bucket_index_key_allows_none_key_values() -> None:
    """Allow None as a valid resolved key value."""
    assert resolve_bucket_index_key(Row(None), "day") is None


def test_resolve_bucket_index_key_raises_clear_error_for_missing_field() -> None:
    """Raise a bucket-index-specific error when a row field is missing."""
    with pytest.raises(MissingBucketIndexKeyError, match="missing"):
        resolve_bucket_index_key(Row("2026-06-15"), "missing")


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
