# Factories and Testing

Factories help you create realistic test data for managers. GeneralManager integrates with [factory_boy](https://factoryboy.readthedocs.io/) to generate Django model instances based on interface metadata. This section explains how to build fixtures that keep tests concise and reproducible.

- [Performance and caching](performance_caching.md)
- [Validation and clean hooks](validation_clean.md)

Use factories to populate development databases, seed GraphQL demos, or reproduce edge cases quickly.

## Lazy factory helpers

The `general_manager.factory` module exports helper functions that return factory declarations and lazy values for common generated defaults. They are intended for `AutoFactory` definitions and custom factory declarations; when a generated component must vary per object, make that variation explicit in the factory attribute instead of relying on unset helper arguments.

Use the helpers by value category:

- Dates and times: `lazy_date_today`, `lazy_date_between`, `lazy_date_time_between`, and `lazy_delta_date`.
- Numbers and choices: `lazy_integer`, `lazy_decimal`, `lazy_choice`, `lazy_sequence`, and `lazy_boolean`.
- Identifiers and text: `lazy_uuid`, `lazy_project_name`, `lazy_faker_name`, `lazy_faker_email`, `lazy_faker_sentence`, `lazy_faker_address`, and `lazy_faker_url`.
- Measurements: `lazy_measurement` creates `Measurement` values compatible with measurement-backed fields.

Each helper returns a `factory_boy` declaration object (`LazyFunction`, `LazyAttribute`, or `LazyAttributeSequence`). The generated value is produced when factory_boy evaluates the declaration, not when the helper is called. Date helpers use Python's local `date.today()` at evaluation time; Faker helpers use the module-level default-locale Faker instance and do not set a deterministic seed.

Random selections are uniform over the helper's stated candidate set: inclusive numeric bounds, inclusive date/day ranges, whole-second datetime offsets, choice snapshots, or the `[0, 1)` random value compared to `trues_ratio`.

Invalid inputs fail at the public boundary when GeneralManager can validate them:

- `lazy_delta_date()` rejects a negative `avg_delta_days`.
- `lazy_measurement()`, `lazy_integer()`, and `lazy_decimal()` reject reversed numeric bounds (`min_value > max_value`).
- `lazy_decimal()` rejects a negative `precision`.
- `lazy_choice()` rejects an empty sequence and snapshots the provided options when the declaration is created.
- `lazy_boolean()` rejects probabilities outside the inclusive `[0, 1]` interval.

`lazy_measurement()` delegates unit parsing and magnitude conversion to `general_manager.measurement.measurement.Measurement`. `lazy_delta_date()` expects the referenced base attribute to be missing, falsey, or compatible with adding `datetime.timedelta` values. `lazy_date_time_between()` follows Python's normal datetime subtraction rules, so mixed naive/aware datetime inputs raise `TypeError`.

Prefer explicit factory attributes when a test needs a specific value. Use lazy helpers for realistic defaults, demo data, and load-test data where exact values are not the assertion target.

## Automatic model-field defaults

`AutoFactory` fills missing model fields with lower-level helpers from
`general_manager.factory.factories`. For normal factory definitions you usually
do not call these directly; define explicit attributes with the lazy helpers
above when a value matters.

The automatic helpers inspect Django fields and produce values that factory_boy
can assign later. Scalar fields usually receive factory_boy `Faker`
declarations, short text and regex-constrained text receive lazy declarations,
measurement fields receive lazy `Measurement` values, and nullable fields may
return `None`. Foreign-key and one-to-one fields default to reusing data that is
already in the database: reusable existing related rows are preferred, and a
related GeneralManager factory is used only when no reusable row exists.
One-to-one reuse skips rows that are already linked through that one-to-one
field. If the factory interface uses a database alias, relation reuse and
one-to-one linked-row filtering query that alias. Nullable relations, including
relations with a default of `None`, preserve their nullable behavior in default
mode and may remain `None`.
Nullable foreign-key and one-to-one fields with no related factory and no
existing related rows resolve to `None`; required relations in the same
situation raise `MissingFactoryOrInstancesError`.

Factories can opt into different relation generation behavior on the nested
`Factory` class:

```python
class Factory:
    _related_factory_mode = "create"
    _related_factory_modes = {"owner": "create"}
```

`_related_factory_mode` applies to every generated relation unless a
field-specific `_related_factory_modes` entry overrides it. `"create"` forces a
new related object when the related model exposes a factory, and it bypasses the
nullable/default-`None` shortcut for that relation. `"random"` restores the
legacy foreign-key behavior that may create a new related row or reuse an
existing one. The default mode is `"reuse_existing"`.

Many-to-many fields are handled after the main object is saved. Explicit
many-to-many values passed to `Factory.create(...)` or
`Factory.create_batch(...)` are assigned to the saved relation. Omitted
`blank=True` many-to-many fields stay empty by default; use a field-specific
`_related_factory_modes = {"members": "create"}` entry when an omitted
many-to-many field should generate and assign related values. `Factory.build(...)`
returns unsaved model instances and skips many-to-many assignment, even when
explicit or declared many-to-many values are supplied. If a related factory
returns a GeneralManager instance, the helper resolves it back to the underlying
Django row before assignment. If that manager cannot be resolved to a row,
`UnableToResolveManagerInstanceError` is raised.

## Sequences and existing rows

`AutoFactory` initializes factory_boy sequence counters from the target model's
current row count. The count is read through the interface database alias when
one is configured. If a table already contains one row and a factory uses
`lazy_sequence()` or another factory_boy sequence declaration, the first
generated sequence index is `1` rather than `0`.

This is a row-count default, not a parser for existing values. Override
`_setup_next_sequence()` on a custom factory when uniqueness requires reading
and parsing existing names, codes, or other sequence-bearing fields.

See the [Factory API reference](../../api/factory.md) for signatures.
