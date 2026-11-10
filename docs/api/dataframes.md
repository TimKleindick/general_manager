# Dataframes API

Use these helpers to convert row mappings containing `Measurement` values to
value/unit columns and back. `expand_measurements` and `collapse_measurements`
work on rows; `to_dataframe` creates a pandas DataFrame and `from_dataframe`
reads its records. Install pandas when creating DataFrames.

Follow the [dataframe walkthrough](../howto/dataframe_measurements.md) for a
complete round trip and the [measurement concepts](../concepts/measurement/index.md)
for units and storage.

`general_manager.dataframes` re-exports `expand_measurements`,
`collapse_measurements`, `to_dataframe`, `from_dataframe`,
`DataFrameMeasurementError`, `InvalidDataFrameMeasurementValueError`,
`MeasurementDataFrameColumnCollisionError`,
`MissingMeasurementDataFrameColumnError`, and `PandasNotInstalledError`.

::: general_manager.dataframes.measurements
