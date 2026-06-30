from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from general_manager.dataframes.measurements import (
    InvalidDataFrameMeasurementValueError,
    MeasurementDataFrameColumnCollisionError,
    MissingMeasurementDataFrameColumnError,
    PandasNotInstalledError,
    collapse_measurements,
    expand_measurements,
    from_dataframe,
    to_dataframe,
)
from general_manager.measurement import Measurement


class FakeDataFrame:
    def __init__(self, rows: list[dict[str, object]], **kwargs: object) -> None:
        self.rows = rows
        self.kwargs = kwargs

    def to_dict(self, orient: str = "dict") -> list[dict[str, object]]:
        assert orient == "records"
        return self.rows


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


def test_to_dataframe_uses_lazy_pandas_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported_modules: list[str] = []

    def fake_import_module(module_name: str) -> Any:
        imported_modules.append(module_name)
        return SimpleNamespace(DataFrame=FakeDataFrame)

    monkeypatch.setattr(
        "general_manager.dataframes.measurements.importlib.import_module",
        fake_import_module,
    )

    dataframe = to_dataframe(
        [{"height": Measurement(180, "cm")}],
        measurement_fields={"height"},
        index=["row-1"],
    )

    assert imported_modules == ["pandas"]
    assert isinstance(dataframe, FakeDataFrame)
    assert dataframe.rows == [
        {"height_value": Decimal("180"), "height_unit": "centimeter"}
    ]
    assert dataframe.kwargs == {"index": ["row-1"]}


def test_to_dataframe_reports_missing_pandas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_import_module(module_name: str) -> Any:
        assert module_name == "pandas"
        raise ModuleNotFoundError(name="pandas")

    monkeypatch.setattr(
        "general_manager.dataframes.measurements.importlib.import_module",
        fake_import_module,
    )

    with pytest.raises(PandasNotInstalledError) as exc_info:
        to_dataframe([{"height": Measurement(180, "cm")}])

    assert "pandas" in str(exc_info.value)


def test_from_dataframe_collapses_records() -> None:
    dataframe = FakeDataFrame(
        [{"height_value": Decimal("180"), "height_unit": "centimeter"}]
    )

    assert from_dataframe(dataframe, measurement_fields={"height"}) == [
        {"height": Measurement(180, "centimeter")}
    ]


def test_from_dataframe_rejects_non_list_records() -> None:
    class TupleRecordsDataFrame:
        def to_dict(self, orient: str = "dict") -> tuple[dict[str, object], ...]:
            assert orient == "records"
            return ({"height_value": Decimal("180"), "height_unit": "centimeter"},)

    with pytest.raises(TypeError, match="list"):
        from_dataframe(TupleRecordsDataFrame(), measurement_fields={"height"})


def test_from_dataframe_rejects_non_mapping_records() -> None:
    class NonMappingRecordsDataFrame:
        def to_dict(self, orient: str = "dict") -> list[object]:
            assert orient == "records"
            return [object()]

    with pytest.raises(TypeError, match="mapping"):
        from_dataframe(NonMappingRecordsDataFrame(), measurement_fields={"height"})
