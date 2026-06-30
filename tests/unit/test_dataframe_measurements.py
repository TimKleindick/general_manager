from __future__ import annotations

from decimal import Decimal

import pytest

from general_manager.dataframes.measurements import (
    InvalidDataFrameMeasurementValueError,
    MeasurementDataFrameColumnCollisionError,
    expand_measurements,
)
from general_manager.measurement import Measurement


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
