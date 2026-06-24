# Persisting Measurements

`MeasurementField` stores measurements in Django models. It persists the magnitude in a configurable base unit while tracking the input unit for display.

## Declaring fields

```python
from django.db import models
from general_manager.measurement import MeasurementField, Measurement

class Product(models.Model):
    weight = MeasurementField(base_unit="kg", null=True, blank=True)
    price = MeasurementField(base_unit="EUR", null=True, blank=True)
```

Assignments accept `Measurement` instances or human-readable strings:

```python
product.weight = Measurement(750, "gram")
product.price = "19.99 EUR"
```

Use `None` to clear a nullable measurement:

```python
product.weight = None
```

Assignment clears the backing columns immediately. On a non-nullable field,
Django validation rejects that cleared value later through the normal
`null=False` validation path. Saving without model validation relies on the
database schema for the non-null backing columns.

An empty string is parsed as measurement text and raises `ValidationError`; it is
not treated as a clear operation. `blank=True` only affects Django's validation
hook for already-normalized empty values; descriptor assignment and query
preparation still reject `""` as invalid measurement text.

Measurement strings start with a Python `Decimal` numeric token and may then add
a Pint unit expression. Signed decimals and exponent notation are accepted;
thousands separators are not. Leading and trailing whitespace is ignored, but a
whitespace-only string is invalid. Unitless numeric strings, such as `"123"`,
create dimensionless measurements. They are valid only for dimensionless fields,
not as shorthand for a field's base unit. Descriptor assignment accepts only
`Measurement`, string, or `None`; bare numeric values are not assignment inputs.

## Base units

Choose base units that match your reporting needs. All stored values convert to the base unit automatically. When reading from the database, the original unit is restored so you can display the same unit the user provided.

The backing columns are named `<field>_value` and `<field>_unit`. The value
column stores the magnitude in `base_unit` using decimal precision, and the unit
column stores Pint's canonical spelling for the assigned unit. For example,
assigning `"1000 g"` to a kilogram-backed field stores the value as `1.0000000000`
in the value column and `gram` in the unit column. If stored data is edited
outside GeneralManager and the unit is unknown, unparsable, or no longer matches
the base dimension, attribute access falls back to returning the stored
magnitude as already being in `base_unit`. That fallback only affects the
returned `Measurement`; it does not rewrite the backing columns. If the stored
value column itself cannot be converted with `Decimal(str(value))`, that decimal
conversion error propagates.

For currency fields, the base unit must be one of GeneralManager's fixed
currency units: `EUR`, `USD`, `GBP`, `JPY`, `CHF`, `AUD`, or `CAD`. Each
currency is its own Pint dimension, so assignments must use the exact base
currency for that field. Currency aliases such as `dollar` are not part of the
public field contract.
`MeasurementField` does not accept exchange rates, and cross-currency
assignments raise `ValidationError` as incompatible. Convert first with
`Measurement.to(..., exchange_rate=...)`.

If you use `unique=True`, `unique_together`, or a `UniqueConstraint` on a
measurement field, GeneralManager maps that uniqueness to the value column.
Equivalent values in different units, such as `1 kg` and `1000 gram`, conflict
because they store the same base magnitude. Incompatible units and different
currency dimensions are rejected before storage, so uniqueness is meaningful
within the field's configured base unit dimension.

## Validation and forms

`MeasurementField` plugs into Django forms and admin just like other fields. Invalid strings raise `ValidationError` with descriptive messages. To customise form widgets, subclass `MeasurementFormField` or provide your own input parsing logic.

Assignment handles string parsing, currency checks, and dimensionality checks.
Django's normal `clean()`/`validate()` lifecycle handles `null`, `blank`, and
custom validators after the value has been normalized. Validators receive the
`Measurement` object supplied to validation; they are not given a separate
base-unit copy. Direct `clean("100 g")` calls do not parse strings and raise
`ValidationError`; string parsing belongs to descriptor assignment and
`get_prep_value()`. Non-empty non-`Measurement` values passed directly to
`clean()`, `validate()`, or `run_validators()` raise the same generic field
`ValidationError`. `clean(None)` raises Django's standard null error when
`null=False` and returns `None` when `null=True`. Direct `run_validators()` calls
with `""`, `[]`, `()`, or `{}` skip validators regardless of `blank`; use
`clean()` when you need blank enforcement before validators. When `blank=True`,
`clean("")`, `clean([])`, `clean(())`, and `clean({})` return the original empty
object/value unchanged. `to_python()` is intentionally a passthrough for this
virtual field, not a parser; `to_python("1 meter")` returns `"1 meter"`.

Filters accept the same measurement strings and `Measurement` objects used for
assignment:

```python
Product.objects.filter(weight="1 kg")
Product.objects.filter(weight__gte=Measurement(500, "gram"))
```

Bare numeric filter values are interpreted as already being in the stored base
unit only on lookup paths delegated to Django's `DecimalField`; direct
`get_prep_value()` calls do not accept bare numbers. Use measurement strings or
`Measurement` objects for portable field filters:

```python
Product.objects.filter(weight="1 kg")
Product.objects.filter(weight__lt=Measurement(500, "gram"))
```

If you pass a bare number to a Django lookup that is already operating on the
backing value column, such as a translated `<field>_value` lookup, Django treats
that number as a base-unit decimal.

Invalid assignment text, direct `clean("100 g")`, and wrong assignment types
raise `ValidationError` with the field message
`Value must be a Measurement instance or None.` Incompatible units raise
`ValidationError` with `Unit must be compatible with '<base_unit>'.` A physical
unit assigned to a currency field raises
`Unit must be a currency (AUD, CAD, CHF, EUR, GBP, JPY, USD).`; a currency
assigned to a physical field raises `Unit cannot be a currency.` A wrong
currency, such as `EUR` for a `USD` field, raises the incompatible-unit message.
Null and blank validation use Django's standard `null` and `blank` error codes,
and custom validators control their own error details. Unknown or invalid
`base_unit` declarations can still raise Pint exceptions during field
construction; assignment and preparation wrap parser `ValueError` and
dimensionality failures as `ValidationError`. Empty strings, whitespace-only
strings, invalid unit syntax, and unknown unit names that surface as `ValueError`
use the generic invalid-value message. Other Pint exceptions may propagate, but
their exact subclasses are not a stable GeneralManager field API.

## Migrations

Because measurements store magnitudes as decimals, they migrate cleanly across databases. If you change the base unit, run a data migration to rescale stored values.
