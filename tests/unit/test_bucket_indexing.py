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
        self._interface = object()
        self._GeneralManager__id = {"id": pk}
        self._manager_state_valid = True
        self._manager_state_reason = None


class MixedIdentificationManager(GeneralManager):
    def __init__(self) -> None:
        self._interface = object()
        self._GeneralManager__id = {"id": 10, 2: "external"}
        self._manager_state_valid = True
        self._manager_state_reason = None


class Row:
    def __init__(self, day: str | None, related: object = None) -> None:
        self.day = day
        self.related = related


def test_normalize_bucket_index_key_spec_accepts_field_name() -> None:
    assert normalize_bucket_index_key_spec("day") == ("field", ("day",), False)


def test_normalize_bucket_index_key_spec_accepts_composite_tuple() -> None:
    assert normalize_bucket_index_key_spec(("day", "related")) == (
        "field",
        ("day", "related"),
        True,
    )


@pytest.mark.parametrize("key_spec", [(), ("day", 1), ["day"], object()])
def test_normalize_bucket_index_key_spec_rejects_unsupported_specs(
    key_spec: object,
) -> None:
    with pytest.raises(UnsupportedBucketIndexKeySpecError):
        normalize_bucket_index_key_spec(key_spec)


def test_resolve_bucket_index_key_allows_none_key_values() -> None:
    assert resolve_bucket_index_key(Row(None), "day") is None


def test_resolve_bucket_index_key_raises_clear_error_for_missing_field() -> None:
    with pytest.raises(MissingBucketIndexKeyError, match="missing"):
        resolve_bucket_index_key(Row("2026-06-15"), "missing")


def test_resolve_bucket_index_key_builds_composite_key() -> None:
    row = Row("2026-06-15", related="component")

    assert resolve_bucket_index_key(row, ("day", "related")) == (
        "2026-06-15",
        "component",
    )


def test_freeze_bucket_index_value_normalizes_manager_values() -> None:
    related = RelatedManager(10)

    assert freeze_bucket_index_value(related) == (
        RelatedManager,
        (("id", 10),),
    )


def test_freeze_bucket_index_value_normalizes_nested_containers() -> None:
    value = {"ids": [2, 1], "flags": {True, False}}

    assert freeze_bucket_index_value(value) == (
        ("flags", frozenset({False, True})),
        ("ids", (2, 1)),
    )


def test_freeze_bucket_index_value_normalizes_mixed_type_dict_keys() -> None:
    value = {"ids": [2, 1], 2: "external"}

    assert freeze_bucket_index_value(value) == (
        (2, "external"),
        ("ids", (2, 1)),
    )


def test_freeze_bucket_index_value_normalizes_mixed_type_manager_identity_keys() -> (
    None
):
    related = MixedIdentificationManager()

    assert freeze_bucket_index_value(related) == (
        MixedIdentificationManager,
        (
            (2, "external"),
            ("id", 10),
        ),
    )
