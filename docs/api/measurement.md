# Measurement API

::: general_manager.measurement.measurement.Measurement

::: general_manager.measurement.measurement_field.MeasurementField

`MeasurementField(base_unit: str, *args, null: bool = False, blank: bool = False,
editable: bool = True, unique: bool = False, **kwargs)` returns a Django field
that exposes one logical measurement while storing paired
`<field>_value`/`<field>_unit` columns. `default=` may be a `Measurement`, a
parseable measurement string, or a callable returning one. When Django
initializes a model instance without a supplied logical value or backing
attributes, the default is evaluated and materialized through the normal
descriptor conversion path. Explicit values, including `None`, suppress it.
Inherited concrete model classes receive the same behavior.

This is a model-initialization default, not a database-side default. The
default receivers are weakly connected and retained by their live model class,
so discarded migration-state model classes are not kept alive by global signal
registrations. This lifecycle behavior is available in GeneralManager 0.79.3
and later.

The constructor raises `InvalidMeasurementFieldBaseUnitError` (a `ValueError`
subclass) for offset base units such as `degC`, and Pint parsing errors for
unparseable or invalid base units. Assignment and preparation continue to
raise `ValidationError` for invalid values or incompatible units, while
`editable=False` assignments raise `MeasurementFieldNotEditableError`.

::: general_manager.measurement.measurement.ureg

::: general_manager.measurement.measurement.currency_units
