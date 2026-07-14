"""Database-aware django-simple-history integration helpers."""

from __future__ import annotations

from typing import Any, cast

from django.db import DEFAULT_DB_ALIAS, models
from django.db.models.fields.related import ForeignKey
from simple_history import utils as simple_history_utils
from simple_history.models import HistoricalRecords
from simple_history.signals import (
    post_create_historical_m2m_records,
    pre_create_historical_m2m_records,
)
from simple_history.utils import get_history_manager_for_model


DATABASE_AWARE_HISTORY_MARKER = "_general_manager_database_aware_history"


class DatabaseAwareHistoricalRecords(HistoricalRecords):  # type: ignore[misc]
    """Store generated history records on the base model's database alias."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["use_base_model_db"] = True
        super().__init__(*args, **kwargs)

    def create_history_model(
        self,
        model: type[models.Model],
        inherited: bool,
    ) -> type[models.Model]:
        """Mark the generated model as safe for non-default database aliases."""
        history_model = cast(
            type[models.Model],
            super().create_history_model(model, inherited),
        )
        setattr(history_model, DATABASE_AWARE_HISTORY_MARKER, True)
        return history_model

    def create_historical_record(
        self,
        instance: models.Model,
        history_type: str,
        using: str | None = None,
    ) -> None:
        """Route base history even when an M2M signal omits ``using``."""
        database_alias = using or instance._state.db or DEFAULT_DB_ALIAS
        super().create_historical_record(
            instance,
            history_type,
            using=database_alias,
        )

    def create_historical_record_m2ms(
        self,
        history_instance: models.Model,
        instance: models.Model,
    ) -> None:
        """Snapshot tracked many-to-many rows on the base instance alias."""
        database_alias = instance._state.db or history_instance._state.db
        database_alias = database_alias or DEFAULT_DB_ALIAS
        history_fields = cast(
            list[Any],
            history_instance._history_m2m_fields,  # type: ignore[attr-defined]
        )
        for field in history_fields:
            m2m_history_model = self.m2m_models[field]
            original_instance = history_instance.instance  # type: ignore[attr-defined]
            through_model = getattr(original_instance, field.name).through
            through_model_field_names = [
                model_field.name for model_field in through_model._meta.fields
            ]
            through_model_fk_field_names = [
                model_field.name
                for model_field in through_model._meta.fields
                if isinstance(model_field, ForeignKey)
            ]

            through_field_name = simple_history_utils.get_m2m_field_name(field)
            rows = through_model.objects.using(database_alias).filter(
                **{through_field_name: instance}
            )
            rows = rows.select_related(*through_model_fk_field_names)
            insert_rows = []
            for row in rows:
                insert_row = m2m_history_model(
                    history_id=history_instance.pk,
                    **{
                        field_name: getattr(row, field_name)
                        for field_name in through_model_field_names
                    },
                )
                insert_row._state.db = database_alias
                insert_rows.append(insert_row)

            pre_create_historical_m2m_records.send(
                sender=m2m_history_model,
                rows=insert_rows,
                history_instance=history_instance,
                instance=instance,
                field=field,
            )
            created_rows = m2m_history_model.objects.using(database_alias).bulk_create(
                insert_rows
            )
            post_create_historical_m2m_records.send(
                sender=m2m_history_model,
                created_rows=created_rows,
                history_instance=history_instance,
                instance=instance,
                field=field,
            )


def update_change_reason(instance: models.Model, reason: str) -> None:
    """Update the latest history row on the model instance's database alias."""
    manager_owner: models.Model | type[models.Model]
    manager_owner = instance if instance.pk is not None else type(instance)
    history = get_history_manager_for_model(manager_owner)
    database_alias = instance._state.db
    if database_alias:
        history = history.using(database_alias)

    history_fields = {field.attname for field in history.model._meta.fields}
    attrs: dict[str, object] = {}
    for field in instance._meta.fields:
        if field.attname not in history_fields:
            continue
        value = getattr(instance, field.attname)
        if not field.primary_key or value is not None:
            attrs[field.attname] = value

    record = history.filter(**attrs).order_by("-history_date").first()
    record.history_change_reason = reason
    if database_alias:
        record.save(using=database_alias)
    else:
        record.save()
