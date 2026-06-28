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
measurement fields receive lazy `Measurement` values, nullable fields may return
`None`, and relation fields return either a model instance or a lazy declaration
that selects an existing related row. Many-to-many fields are routed through the
dedicated many-to-many helper after the main object is built; the scalar helper
returns `None` if called with a many-to-many field directly. Unsupported scalar
or custom Django field classes also fall back to `None`. Nullable foreign-key
and one-to-one fields with no related factory and no existing related rows
resolve to `None`; required relations in the same situation raise
`MissingFactoryOrInstancesError`.

Many-to-many defaults are generated after the main object is created. The helper
creates or selects related model instances, then `AutoFactory` applies them to
the saved relation for `Factory.create(...)` and `Factory.create_batch(...)`.
`Factory.build(...)` returns unsaved model instances and skips many-to-many
assignment, even when explicit or declared many-to-many values are supplied.
`blank=True` fields may generate an empty list, while `blank=False` fields
request at least one related instance. If a related factory returns a
GeneralManager instance, the helper resolves it back to the underlying Django
row before assignment. If that manager cannot be resolved to a row,
`UnableToResolveManagerInstanceError` is raised.

See the [Factory API reference](../../api/factory.md) for signatures.
