"""Compare validated ORM updates without changing mutation override signatures."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any

from django.db import models, router
from django.db.models.fields.files import FieldFile

from general_manager.measurement.measurement_field import MeasurementField


@dataclass(frozen=True)
class _UpdateState:
    instance: models.Model
    values: dict[str, object]


_update_state: ContextVar[_UpdateState | None] = ContextVar(
    "orm_update_state", default=None
)


def _field_value(field: models.Field[Any, Any], value: object) -> object:
    """Compare storage values, preserving JSON types while ignoring key order."""
    if isinstance(field, models.JSONField):
        encoded = json.dumps(value, cls=field.encoder)
        return json.dumps(json.loads(encoded), sort_keys=True)
    if isinstance(field, models.BinaryField) and isinstance(
        value, (bytes, bytearray, memoryview)
    ):
        return bytes(value)
    return field.get_prep_value(value)


@contextmanager
def track_update(
    instance: models.Model,
    simple_values: dict[str, object],
    many_values: dict[str, list[object]],
    history_comment: str | None,
    *,
    database_alias: str | None = None,
) -> Iterator[None]:
    """Scope the original row to its save, including nested mutation calls.

    The save compares after full_clean(), so validation and changes made by
    clean() are preserved. A context keeps existing save_with_history overrides
    compatible; only the exact original model instance can skip its save.
    """
    state = None
    if isinstance(instance, models.Model) and not many_values and not history_comment:
        writer = database_alias or router.db_for_write(
            type(instance), instance=instance
        )
        fields = instance._meta.concrete_fields
        field_keys = {key for field in fields for key in (field.name, field.attname)}
        field_keys.update(
            field.name
            for field in instance._meta.get_fields()
            if isinstance(field, MeasurementField)
        )
        # Custom writable attributes can affect save() without altering fields.
        # A read replica is not authoritative for deciding to skip a writer save.
        if writer == instance._state.db and all(
            key in field_keys or value is models.NOT_PROVIDED
            for key, value in simple_values.items()
        ):
            values = {
                field.attname: deepcopy(
                    _field_value(field, getattr(instance, field.attname))
                )
                for field in fields
                # An implicit actor change is not a business-data change.
                if field.name != "changed_by"
                or "changed_by" in simple_values
                or "changed_by_id" in simple_values
            }
            state = _UpdateState(instance, values)
    token = _update_state.set(state)
    try:
        yield
    finally:
        _update_state.reset(token)


def is_unchanged_update(instance: models.Model) -> bool:
    """Compare prepared field values after validation, before save hooks run."""
    state = _update_state.get()
    if state is None or state.instance is not instance:
        return False
    for field in instance._meta.concrete_fields:
        if field.attname not in state.values:
            continue
        value = getattr(instance, field.attname)
        # A replacement file needs storage even if it has the same filename.
        if isinstance(value, FieldFile) and not getattr(value, "_committed", False):
            return False
        if _field_value(field, value) != state.values[field.attname]:
            return False
    return True


def changed_many_to_many(
    instance: models.Model,
    values: dict[str, list[object]],
) -> dict[str, list[object]]:
    """Remove unchanged relation sets, respecting target fields and aliases."""
    if not isinstance(instance, models.Model):
        return values
    changed = {}
    for key, items in values.items():
        relation: Any = getattr(instance, key.removesuffix("_id_list"))
        target_field = relation.target_field.target_field
        desired = set()
        for item in items:
            if isinstance(item, models.Model):
                if not isinstance(item, relation.model) or not router.allow_relation(
                    item, instance
                ):
                    # Let Django report invalid objects in the normal write path.
                    changed[key] = items
                    break
                item = target_field.value_from_object(item)
            desired.add(target_field.get_prep_value(item))
        else:
            current = set(
                relation.through._default_manager.using(
                    router.db_for_write(relation.through, instance=instance)
                )
                .filter(**{relation.source_field_name: instance})
                .values_list(relation.target_field.attname, flat=True)
            )
            if desired != current:
                changed[key] = items
    return changed
