from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest

from general_manager.dataframes.measurements import (
    InvalidDataFrameMeasurementValueError,
    MeasurementDataFrameColumnCollisionError,
    MissingMeasurementDataFrameColumnError,
    collapse_measurements,
    expand_measurements,
)
from general_manager.measurement import Measurement


class TruthinessDisabledIterable:
    def __iter__(self) -> Iterator[str]:
        return iter(["height"])

    def __bool__(self) -> bool:
        raise TypeError


def test_expand_measurements_splits_measurement_objects() -> None:
    rows = [
        {"name": "Alice", "height": Measurement(180, "cm"), "age": 31},
        {"name": "Bob", "height": Measurement(170, "cm"), "age": 29},
    ]

    assert expand_measurements(rows) == [
        {
            "name": "Alice",
            "height_value": Decimal("180"),
            "height_unit": "centimeter",
            "age": 31,
        },
        {
            "name": "Bob",
            "height_value": Decimal("170"),
            "height_unit": "centimeter",
            "age": 29,
        },
    ]


def test_expand_measurements_parses_configured_measurement_strings() -> None:
    rows = [
        {"name": "Alice", "height": "180 cm"},
        {"name": "Bob", "height": "1.7 m"},
    ]

    assert expand_measurements(rows, measurement_fields={"height"}) == [
        {
            "name": "Alice",
            "height_value": Decimal("180"),
            "height_unit": "centimeter",
        },
        {
            "name": "Bob",
            "height_value": Decimal("1.7"),
            "height_unit": "meter",
        },
    ]


def test_expand_measurements_accepts_truthiness_disabled_field_iterable() -> None:
    rows = [{"height": "180 cm"}]

    assert expand_measurements(
        rows, measurement_fields=TruthinessDisabledIterable()
    ) == [{"height_value": Decimal("180"), "height_unit": "centimeter"}]


def test_expand_measurements_does_not_parse_unconfigured_strings() -> None:
    rows = [{"name": "Alice", "height": "180 cm", "age": "31"}]

    assert expand_measurements(rows) == [
        {"name": "Alice", "height": "180 cm", "age": "31"}
    ]


def test_expand_measurements_rejects_mixed_inferred_measurement_values() -> None:
    rows: list[dict[str, object]] = [
        {"name": "Alice", "height": Measurement(180, "cm")},
        {"name": "Bob", "height": "170 cm"},
    ]

    with pytest.raises(InvalidDataFrameMeasurementValueError) as exc_info:
        expand_measurements(rows)

    assert "height" in str(exc_info.value)
    assert "Measurement" in str(exc_info.value)


def test_expand_measurements_expands_nulls_for_measurement_fields() -> None:
    rows: list[dict[str, object]] = [
        {"name": "Alice", "height": Measurement(180, "cm")},
        {"name": "Bob", "height": None},
        {"name": "Cara"},
        {"name": "Dana", "height": float("nan")},
    ]

    assert expand_measurements(rows) == [
        {
            "name": "Alice",
            "height_value": Decimal("180"),
            "height_unit": "centimeter",
        },
        {"name": "Bob", "height_value": None, "height_unit": None},
        {"name": "Cara", "height_value": None, "height_unit": None},
        {"name": "Dana", "height_value": None, "height_unit": None},
    ]


def test_expand_measurements_rejects_generated_column_collisions() -> None:
    rows = [
        {
            "name": "Alice",
            "height": Measurement(180, "cm"),
            "height_value": 180,
        }
    ]

    with pytest.raises(MeasurementDataFrameColumnCollisionError) as exc_info:
        expand_measurements(rows)

    assert "height_value" in str(exc_info.value)


def test_collapse_measurements_rebuilds_measurement_objects() -> None:
    rows = [
        {
            "name": "Alice",
            "height_value": Decimal("180"),
            "height_unit": "centimeter",
            "age": 31,
        },
        {
            "name": "Bob",
            "height_value": Decimal("1.7"),
            "height_unit": "meter",
            "age": 29,
        },
    ]

    collapsed = collapse_measurements(rows, measurement_fields={"height"})

    assert list(collapsed[0]) == ["name", "height", "age"]
    assert collapsed[0]["name"] == "Alice"
    assert collapsed[0]["height"] == Measurement(180, "centimeter")
    assert collapsed[0]["age"] == 31
    assert collapsed[1]["name"] == "Bob"
    assert collapsed[1]["height"] == Measurement("1.7", "meter")
    assert collapsed[1]["age"] == 29
    assert "height_value" in rows[0]
    assert "height_unit" in rows[0]


def test_collapse_measurements_rejects_existing_target_field_collision() -> None:
    rows: list[dict[str, object]] = [
        {
            "name": "Alice",
            "height": "raw",
            "height_value": Decimal("180"),
            "height_unit": "centimeter",
        }
    ]

    with pytest.raises(MeasurementDataFrameColumnCollisionError) as exc_info:
        collapse_measurements(rows, measurement_fields={"height"})

    assert "height" in str(exc_info.value)


def test_collapse_measurements_restores_null_measurements() -> None:
    rows: list[dict[str, object]] = [
        {
            "name": "Alice",
            "height_value": None,
            "height_unit": None,
        },
        {
            "name": "Bob",
            "height_value": float("nan"),
            "height_unit": float("nan"),
        },
    ]

    assert collapse_measurements(rows, measurement_fields={"height"}) == [
        {"name": "Alice", "height": None},
        {"name": "Bob", "height": None},
    ]


def test_collapse_measurements_rejects_partial_null_measurements() -> None:
    rows = [
        {
            "name": "Alice",
            "height_value": Decimal("180"),
            "height_unit": None,
        }
    ]

    with pytest.raises(InvalidDataFrameMeasurementValueError) as exc_info:
        collapse_measurements(rows, measurement_fields={"height"})

    assert "height" in str(exc_info.value)


def test_collapse_measurements_rejects_non_string_units() -> None:
    rows = [
        {
            "name": "Alice",
            "height_value": Decimal("180"),
            "height_unit": 1,
        }
    ]

    with pytest.raises(InvalidDataFrameMeasurementValueError) as exc_info:
        collapse_measurements(rows, measurement_fields={"height"})

    assert "height" in str(exc_info.value)


def test_collapse_measurements_rejects_invalid_unit_strings() -> None:
    rows = [
        {
            "name": "Alice",
            "height_value": Decimal("180"),
            "height_unit": "not_a_real_unit",
        }
    ]

    with pytest.raises(InvalidDataFrameMeasurementValueError) as exc_info:
        collapse_measurements(rows, measurement_fields={"height"})

    assert "height" in str(exc_info.value)


def test_collapse_measurements_requires_value_and_unit_columns() -> None:
    rows = [{"name": "Alice", "height_value": Decimal("180")}]

    with pytest.raises(MissingMeasurementDataFrameColumnError) as exc_info:
        collapse_measurements(rows, measurement_fields={"height"})

    assert "height_unit" in str(exc_info.value)
