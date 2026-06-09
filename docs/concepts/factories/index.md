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

Prefer explicit factory attributes when a test needs a specific value. Use lazy helpers for realistic defaults, demo data, and load-test data where exact values are not the assertion target.

See the [Factory API reference](../../api/factory.md) for signatures.
