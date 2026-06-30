"""Helpers for expanding row-level Measurement values into dataframe columns."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
import importlib
import math
from typing import Any, Protocol

import pint

from general_manager.measurement import Measurement

Row = Mapping[str, object]
MutableRow = dict[str, object]

__all__ = [
    "DataFrameMeasurementError",
    "InvalidDataFrameMeasurementValueError",
    "MeasurementDataFrameColumnCollisionError",
    "MissingMeasurementDataFrameColumnError",
    "PandasNotInstalledError",
    "collapse_measurements",
    "expand_measurements",
    "from_dataframe",
    "to_dataframe",
]


class DataFrameLike(Protocol):
    """Minimal dataframe interface needed to collapse measurement columns."""

    def to_dict(self, orient: str = "dict") -> Any:
        """Return dataframe rows as dictionaries."""


class DataFrameMeasurementError(ValueError):
    """Base error for dataframe measurement expansion failures."""


class PandasNotInstalledError(ImportError):
    """Raised when pandas-specific dataframe helpers are used without pandas."""

    def __init__(self) -> None:
        super().__init__(
            "pandas is required to create dataframes; install pandas to use "
            "to_dataframe()."
        )


class _InvalidDataFrameRecordsError(TypeError):
    """Raised when dataframe records are not list[Mapping[str, object]]."""

    def __init__(self) -> None:
        super().__init__(
            "dataframe.to_dict(orient='records') must return a list of mapping rows."
        )


class InvalidDataFrameMeasurementValueError(DataFrameMeasurementError):
    """Raised when a configured measurement field contains an invalid value."""

    def __init__(
        self,
        field: str,
        *,
        value_type: str | None = None,
        allows_strings: bool = False,
    ) -> None:
        expected = "Measurement values or nulls"
        if allows_strings:
            expected = "Measurement values, parseable measurement strings, or nulls"
        details = f"; got {value_type}" if value_type else ""
        super().__init__(f"Field {field!r} must contain {expected}{details}.")


class MeasurementDataFrameColumnCollisionError(DataFrameMeasurementError):
    """Raised when measurement expansion or collapse would overwrite row data."""

    def __init__(
        self,
        field: str,
        generated_field: str,
        *,
        operation: str = "Expanding",
    ) -> None:
        super().__init__(
            f"{operation} measurement field "
            f"{field!r} would overwrite existing column {generated_field!r}."
        )


class MissingMeasurementDataFrameColumnError(DataFrameMeasurementError):
    """Raised when a generated measurement column is required but absent."""

    def __init__(self, column: str) -> None:
        super().__init__(
            f"Collapsing measurement dataframe rows requires column {column!r}."
        )


def expand_measurements(
    rows: Iterable[Row],
    *,
    measurement_fields: Iterable[str] | None = None,
) -> list[MutableRow]:
    """Expand Measurement values in mapping-like rows into value/unit columns."""

    copied_rows = [dict(row) for row in rows]
    explicit_fields = _ordered_unique(
        () if measurement_fields is None else measurement_fields
    )
    inferred_fields = _infer_measurement_fields(copied_rows)
    expanded_fields = _ordered_unique((*explicit_fields, *inferred_fields))
    if not expanded_fields:
        return copied_rows

    _check_generated_column_collisions(copied_rows, expanded_fields)
    explicit_field_names = set(explicit_fields)
    expanded_field_names = set(expanded_fields)
    return [
        _expand_row(
            row,
            expanded_fields=expanded_fields,
            expanded_field_names=expanded_field_names,
            explicit_field_names=explicit_field_names,
        )
        for row in copied_rows
    ]


def collapse_measurements(
    rows: Iterable[Row],
    *,
    measurement_fields: Iterable[str],
) -> list[MutableRow]:
    """Collapse value/unit dataframe columns back into Measurement values."""

    collapsed_fields = _ordered_unique(measurement_fields)
    value_columns: dict[str, str] = {}
    unit_columns: set[str] = set()
    for field in collapsed_fields:
        value_field, unit_field = _generated_field_names(field)
        value_columns[value_field] = field
        unit_columns.add(unit_field)

    return [
        _collapse_row(
            dict(row),
            collapsed_fields=collapsed_fields,
            value_columns=value_columns,
            unit_columns=unit_columns,
        )
        for row in rows
    ]


def to_dataframe(
    rows: Iterable[Row],
    *,
    measurement_fields: Iterable[str] | None = None,
    **dataframe_kwargs: object,
) -> Any:
    """Expand measurement values and build a pandas DataFrame."""

    expanded_rows = expand_measurements(rows, measurement_fields=measurement_fields)
    pandas = _import_pandas()
    return pandas.DataFrame(expanded_rows, **dataframe_kwargs)


def from_dataframe(
    dataframe: DataFrameLike,
    *,
    measurement_fields: Iterable[str],
) -> list[MutableRow]:
    """Collapse measurement value/unit columns from a dataframe-like object."""

    records = dataframe.to_dict(orient="records")
    if not isinstance(records, list):
        raise _InvalidDataFrameRecordsError

    row_records: list[Row] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise _InvalidDataFrameRecordsError
        row_records.append(record)

    return collapse_measurements(row_records, measurement_fields=measurement_fields)


def _import_pandas() -> Any:
    try:
        return importlib.import_module("pandas")
    except ModuleNotFoundError as error:
        if error.name != "pandas":
            raise
        raise PandasNotInstalledError from error


def _ordered_unique(fields: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered_fields: list[str] = []
    for field in fields:
        if field in seen:
            continue
        seen.add(field)
        ordered_fields.append(field)
    return tuple(ordered_fields)


def _infer_measurement_fields(rows: Iterable[Row]) -> tuple[str, ...]:
    return _ordered_unique(
        field
        for row in rows
        for field, value in row.items()
        if isinstance(value, Measurement)
    )


def _check_generated_column_collisions(
    rows: Iterable[Row], measurement_fields: Iterable[str]
) -> None:
    for field in measurement_fields:
        generated_fields = _generated_field_names(field)
        for row in rows:
            for generated_field in generated_fields:
                if generated_field in row and generated_field != field:
                    raise MeasurementDataFrameColumnCollisionError(
                        field, generated_field
                    )


def _generated_field_names(field: str) -> tuple[str, str]:
    return (f"{field}_value", f"{field}_unit")


def _expand_row(
    row: Row,
    *,
    expanded_fields: tuple[str, ...],
    expanded_field_names: set[str],
    explicit_field_names: set[str],
) -> MutableRow:
    expanded_row: MutableRow = {}
    seen_measurement_fields: set[str] = set()

    for field, value in row.items():
        if field not in expanded_field_names:
            expanded_row[field] = value
            continue

        seen_measurement_fields.add(field)
        value_field, unit_field = _generated_field_names(field)
        measurement = _coerce_measurement_value(
            field,
            value,
            explicit_field_names=explicit_field_names,
        )
        expanded_row[value_field] = (
            measurement.magnitude if measurement is not None else None
        )
        expanded_row[unit_field] = measurement.unit if measurement is not None else None

    for field in expanded_fields:
        if field in seen_measurement_fields:
            continue
        value_field, unit_field = _generated_field_names(field)
        expanded_row[value_field] = None
        expanded_row[unit_field] = None

    return expanded_row


def _collapse_row(
    row: MutableRow,
    *,
    collapsed_fields: tuple[str, ...],
    value_columns: Mapping[str, str],
    unit_columns: set[str],
) -> MutableRow:
    for field in collapsed_fields:
        value_field, unit_field = _generated_field_names(field)
        if value_field not in row:
            raise MissingMeasurementDataFrameColumnError(value_field)
        if unit_field not in row:
            raise MissingMeasurementDataFrameColumnError(unit_field)
        if field in row:
            raise MeasurementDataFrameColumnCollisionError(
                field,
                field,
                operation="Collapsing",
            )

    collapsed_row: MutableRow = {}
    for column, value in row.items():
        if column in value_columns:
            field = value_columns[column]
            _value_field, unit_field = _generated_field_names(field)
            collapsed_row[field] = _build_collapsed_measurement(
                field,
                value,
                row[unit_field],
            )
            continue
        if column in unit_columns:
            continue
        collapsed_row[column] = value

    return collapsed_row


def _coerce_measurement_value(
    field: str,
    value: object,
    *,
    explicit_field_names: set[str],
) -> Measurement | None:
    if _is_null_measurement_value(value):
        return None
    if isinstance(value, Measurement):
        return value
    if isinstance(value, str) and field in explicit_field_names:
        try:
            return Measurement.from_string(value)
        except ValueError as error:
            raise InvalidDataFrameMeasurementValueError(
                field, allows_strings=True
            ) from error

    raise InvalidDataFrameMeasurementValueError(field, value_type=type(value).__name__)


def _build_collapsed_measurement(
    field: str,
    value: object,
    unit: object,
) -> Measurement | None:
    value_is_null = _is_null_measurement_value(value)
    unit_is_null = _is_null_measurement_value(unit)
    if value_is_null and unit_is_null:
        return None
    if value_is_null or unit_is_null:
        raise InvalidDataFrameMeasurementValueError(field)
    if not isinstance(unit, str):
        raise InvalidDataFrameMeasurementValueError(
            field,
            value_type=type(unit).__name__,
        )

    try:
        magnitude = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise InvalidDataFrameMeasurementValueError(
            field,
            value_type=type(value).__name__,
        ) from error
    try:
        return Measurement(magnitude, unit)
    except pint.errors.PintError as error:
        raise InvalidDataFrameMeasurementValueError(
            field,
            value_type=type(unit).__name__,
        ) from error
    except (TypeError, ValueError) as error:
        raise InvalidDataFrameMeasurementValueError(
            field,
            value_type=type(value).__name__,
        ) from error


def _is_null_measurement_value(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))
