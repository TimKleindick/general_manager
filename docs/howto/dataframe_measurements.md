# Import and Export Measurements with Dataframes

Use `general_manager.dataframes` when tabular data includes `Measurement`
values. The helpers expand measurements into explicit value and unit columns so
CSV, spreadsheet, and dataframe workflows do not need object-valued measurement
columns.

## Expand Measurements

```python
from general_manager.dataframes import expand_measurements
from general_manager.measurement import Measurement

rows = [
    {"name": "Alice", "height": Measurement(180, "cm")},
    {"name": "Bob", "height": "170 cm"},
]

expanded = expand_measurements(rows, measurement_fields={"height"})
```

`expanded` contains:

```python
from decimal import Decimal

[
    {"name": "Alice", "height_value": Decimal("180"), "height_unit": "centimeter"},
    {"name": "Bob", "height_value": Decimal("170"), "height_unit": "centimeter"},
]
```

Units use Pint's canonical unit names through `Measurement.unit`.

## Parse Strings Intentionally

Strings are parsed with `Measurement.from_string()` only when their field is
listed in `measurement_fields`.

```python
expand_measurements(
    [{"height": "180 cm", "age": "31"}],
    measurement_fields={"height"},
)
```

This expands `height` and leaves `age` unchanged. This avoids treating ordinary
numeric strings as dimensionless measurements.

## Collapse Measurement Columns

```python
from decimal import Decimal

from general_manager.dataframes import collapse_measurements

rows = collapse_measurements(
    [{"height_value": Decimal("180"), "height_unit": "centimeter"}],
    measurement_fields={"height"},
)
```

The result is:

```python
from general_manager.measurement import Measurement

[{"height": Measurement(180, "centimeter")}]
```

`collapse_measurements()` requires both `<field>_value` and `<field>_unit`
columns for every listed measurement field. If both generated columns are null,
the collapsed measurement is `None`.

## Use Pandas When Available

`to_dataframe()` imports pandas lazily. Pandas is not a GeneralManager runtime
dependency, so projects that do not install pandas can keep using
`expand_measurements()` and `collapse_measurements()`.

```python
from general_manager.dataframes import from_dataframe, to_dataframe

df = to_dataframe(rows, measurement_fields={"height"})
rows = from_dataframe(df, measurement_fields={"height"})
```

If pandas is not installed, `to_dataframe()` raises `PandasNotInstalledError`.
