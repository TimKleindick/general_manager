# Migration Gotchas

GeneralManager integrates tightly with Django migrations. Keep the following tips in mind to avoid unexpected issues.

## Import order

Always import manager modules in `apps.py` (inside `AppConfig.ready()`). If Django does not evaluate your interfaces, it cannot generate migrations for the derived models.

## Model renames

When you rename a manager attribute or interface field, update existing migrations accordingly. Django may consider it a field deletion plus addition otherwise.

## Measurement fields

`MeasurementField` stores value and unit columns. When you change the base unit, create a data migration that rescales stored values to the new unit to avoid inconsistent data.

## Read-only interfaces

Read-only data synchronises `_data` entries during startup. If you remove entries, they are deactivated rather than deleted. Write data migrations when you need permanent removal.

## Historical data

Database interfaces integrate with `django-simple-history`. When you backfill migrations, ensure history tables are populated before managers expect them.

## Rolling back

Because managers call `full_clean()` during operations, old migrations can fail if data violates new validation rules. Plan rollback steps by capturing export snapshots before applying schema changes.
