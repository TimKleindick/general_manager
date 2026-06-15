from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from general_manager.cache.dependency_matching import (
    EXACT_OPERATORS,
    SCAN_OPERATORS,
    LookupSpec,
    current_value_for_path,
    lookup_spec_from_key,
    matches_lookup_value,
    normalize_dependency_value,
    stable_value_hash,
)
from general_manager.measurement.measurement import Measurement


def test_lookup_spec_from_key_splits_operator_and_attribute_path() -> None:
    assert lookup_spec_from_key("status") == LookupSpec(
        lookup="status",
        attr_path=("status",),
        operator="eq",
    )
    assert lookup_spec_from_key("count__gte") == LookupSpec(
        lookup="count__gte",
        attr_path=("count",),
        operator="gte",
    )
    assert lookup_spec_from_key("owner__name__contains") == LookupSpec(
        lookup="owner__name__contains",
        attr_path=("owner", "name"),
        operator="contains",
    )


def test_operator_sets_classify_exact_and_scan_lookups() -> None:
    assert EXACT_OPERATORS == frozenset({"eq"})
    assert SCAN_OPERATORS == frozenset(
        {
            "in",
            "gt",
            "gte",
            "lt",
            "lte",
            "contains",
            "startswith",
            "endswith",
            "regex",
        }
    )


def test_normalize_dependency_value_and_hash_are_stable() -> None:
    payload = {"day": date(2024, 7, 24), "members": {"b", "a"}, "active": True}

    normalized = normalize_dependency_value(payload)

    assert normalized == {
        "active": True,
        "day": "2024-07-24",
        "members": ["a", "b"],
    }
    assert stable_value_hash(payload) == stable_value_hash(
        {"members": {"a", "b"}, "active": True, "day": date(2024, 7, 24)}
    )


def test_matches_lookup_value_covers_supported_scalar_types() -> None:
    aware = datetime(2024, 7, 24, 12, 0, tzinfo=timezone.utc)

    assert matches_lookup_value("eq", "active", '"active"')
    assert matches_lookup_value("eq", None, "null")
    assert matches_lookup_value("in", 3, "[1, 3, 5]")
    assert matches_lookup_value("gte", 5, "5")
    assert matches_lookup_value("lt", date(2024, 7, 23), '"2024-07-24"')
    assert matches_lookup_value("contains", "alpha bravo", '"bravo"')
    assert matches_lookup_value("startswith", "alpha bravo", '"alpha"')
    assert matches_lookup_value("endswith", "alpha bravo", '"bravo"')
    assert matches_lookup_value("regex", "alpha-123", '"^[a-z]+-[0-9]+$"')
    assert matches_lookup_value("eq", aware, '"2024-07-24T12:00:00Z"')
    assert matches_lookup_value("eq", True, '"true"')


def test_current_value_for_path_returns_none_for_missing_attributes() -> None:
    instance = SimpleNamespace(owner=SimpleNamespace(name="Leia"))

    assert current_value_for_path(instance, ("owner", "name")) == "Leia"
    assert current_value_for_path(instance, ("owner", "rank")) is None


def test_matches_lookup_value_coerces_stateful_values_for_range_comparison() -> None:
    stored_value = normalize_dependency_value(Measurement(1000, "EUR"))

    assert matches_lookup_value(
        "gte",
        Measurement(1500, "EUR"),
        stable_json(stored_value),
    )
    assert not matches_lookup_value(
        "gte",
        Measurement(900, "EUR"),
        stable_json(stored_value),
    )


def stable_json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True)
