from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from general_manager.cache.dependency_matching import (
    EXACT_OPERATORS,
    SCAN_OPERATORS,
    LookupSpec,
    current_value_for_path,
    lookup_spec_from_key,
    matches_lookup_value,
    normalize_dependency_value,
    serialize_normalized_value,
    stable_value_hash,
)
from general_manager.measurement.measurement import Measurement


class ReprOnly:
    def __repr__(self) -> str:
        return "ReprOnly(token)"


class BadStateValue:
    def __init__(self) -> None:
        pass


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
    moment = datetime(2024, 7, 24, 12, 0, tzinfo=timezone.utc)
    payload = {
        "day": date(2024, 7, 24),
        "members": {"b", "a"},
        "active": True,
        "moment": moment,
        "opaque": ReprOnly(),
    }

    normalized = normalize_dependency_value(payload)

    assert normalized == {
        "active": True,
        "day": "2024-07-24",
        "members": ["a", "b"],
        "moment": moment.isoformat(),
        "opaque": {"__repr__": "ReprOnly(token)"},
    }
    assert stable_value_hash(payload) == stable_value_hash(
        {
            "members": {"a", "b"},
            "active": True,
            "day": date(2024, 7, 24),
            "moment": moment,
            "opaque": ReprOnly(),
        }
    )


def test_single_key_scalar_dependency_mapping_skips_sorting() -> None:
    with patch(
        "general_manager.cache.dependency_matching.sorted",
        side_effect=AssertionError("single-key scalar mapping does not need sorting"),
        create=True,
    ):
        assert normalize_dependency_value({"id": 7}) == {"id": 7}


def test_single_key_scalar_dependency_mapping_serialization_skips_json_dumps() -> None:
    with (
        patch(
            "general_manager.cache.dependency_matching.json.dumps",
            side_effect=AssertionError("single-key scalar mapping uses fast path"),
        ),
        patch(
            "general_manager.cache.dependency_matching.sorted",
            side_effect=AssertionError(
                "single-key scalar mapping does not need sorting"
            ),
            create=True,
        ),
    ):
        assert serialize_normalized_value({"id": 7}) == '{"id": 7}'
        assert serialize_normalized_value({"day": date(2026, 1, 2)}) == (
            '{"day": "2026-01-02"}'
        )


def test_multi_key_simple_dependency_mapping_serialization_skips_generic_path() -> None:
    payload = {
        "target_date": date(2026, 1, 2),
        "derivative": {"id": 7},
        "search_date": None,
    }

    with (
        patch(
            "general_manager.cache.dependency_matching.json.dumps",
            side_effect=AssertionError("simple mapping uses fast path"),
        ),
        patch(
            "general_manager.cache.dependency_matching.normalize_dependency_value",
            side_effect=AssertionError("simple mapping does not need normalization"),
        ),
    ):
        assert serialize_normalized_value(payload) == (
            '{"derivative": {"id": 7}, "search_date": null, '
            '"target_date": "2026-01-02"}'
        )


def test_sorted_two_key_simple_dependency_mapping_serialization_skips_sorting() -> None:
    payload = {
        "bill_of_material": {"id": 7},
        "search_date": None,
    }

    with patch(
        "general_manager.cache.dependency_matching.sorted",
        side_effect=AssertionError("already-sorted two-key mapping skips sorting"),
        create=True,
    ):
        assert serialize_normalized_value(payload) == (
            '{"bill_of_material": {"id": 7}, "search_date": null}'
        )


def test_primitive_dependency_serialization_matches_json_contract() -> None:
    assert serialize_normalized_value("abc") == '"abc"'
    assert serialize_normalized_value(3) == "3"
    assert serialize_normalized_value(True) == "true"
    assert serialize_normalized_value(None) == "null"


def test_scalar_dependency_serialization_skips_json_dumps() -> None:
    with patch(
        "general_manager.cache.dependency_matching.json.dumps",
        side_effect=AssertionError("scalar dependency values use fast path"),
    ):
        assert serialize_normalized_value("abc") == '"abc"'
        assert serialize_normalized_value(date(2026, 1, 2)) == '"2026-01-02"'
        assert serialize_normalized_value(3) == "3"
        assert serialize_normalized_value(True) == "true"
        assert serialize_normalized_value(None) == "null"


def test_nested_dependency_serialization_remains_sorted_and_recursive() -> None:
    value = {"b": 2, "a": [date(2026, 1, 2), {"z": "last"}]}

    assert normalize_dependency_value(value) == {
        "a": ["2026-01-02", {"z": "last"}],
        "b": 2,
    }
    assert serialize_normalized_value(value) == (
        '{"a": ["2026-01-02", {"z": "last"}], "b": 2}'
    )


def test_datetime_dependency_serialization_uses_isoformat() -> None:
    assert serialize_normalized_value(date(2026, 1, 2)) == '"2026-01-02"'
    assert serialize_normalized_value(datetime(2026, 1, 2, 3, 4, 5)) == (
        '"2026-01-02T03:04:05"'
    )


def test_stable_value_hash_is_unchanged_for_equivalent_mapping_order() -> None:
    assert stable_value_hash({"b": 2, "a": 1}) == stable_value_hash({"a": 1, "b": 2})


def test_collection_dependency_serialization_remains_normalized() -> None:
    assert serialize_normalized_value(("b", date(2026, 1, 2))) == (
        '["b", "2026-01-02"]'
    )
    assert serialize_normalized_value({"b", "a"}) == '["a", "b"]'


def test_stateful_dependency_serialization_remains_normalized() -> None:
    assert serialize_normalized_value(Measurement(1000, "EUR")) == (
        '{"__state__": {"magnitude": "1000", "unit": "EUR"}}'
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


def test_matches_lookup_value_covers_coercion_and_fallback_edges() -> None:
    aware = datetime(2024, 7, 24, 12, 0, tzinfo=timezone.utc)

    assert matches_lookup_value("eq", aware, aware)
    assert not matches_lookup_value("eq", aware, "42")
    assert not matches_lookup_value("gt", date(2024, 7, 24), "42")
    assert matches_lookup_value("eq", False, "false")
    assert matches_lookup_value("eq", True, "1")
    assert matches_lookup_value("eq", False, '"FALSE"')
    assert not matches_lookup_value("eq", True, '"maybe"')
    assert matches_lookup_value("eq", ReprOnly(), '{"__repr__": "ReprOnly(token)"}')
    assert matches_lookup_value("in", ReprOnly(), '[{"__repr__": "ReprOnly(token)"}]')
    assert not matches_lookup_value(
        "eq",
        BadStateValue(),
        '{"__state__": {"magnitude": 1, "unit": "EUR"}}',
    )
    assert not matches_lookup_value("eq", BadStateValue(), '{"__state__": "bad"}')
    assert not matches_lookup_value("unknown", "value", '"value"')


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
