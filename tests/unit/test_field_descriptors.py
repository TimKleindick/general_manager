from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast
from unittest.mock import Mock, patch
from uuid import UUID

from django.apps import apps
from django.contrib.postgres.search import SearchQueryField, SearchVectorField
from django.db import models
from django.db.models.fields.composite import CompositePrimaryKey
from django.db.models.fields.generated import GeneratedField
from django.db.models.fields.proxy import OrderWrt

from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    _FieldDescriptorBuilder,
    TRANSLATION,
    _general_manager_many_accessor,
    build_field_descriptors,
)
from general_manager.measurement.measurement import Measurement
from general_manager.measurement.measurement_field import MeasurementField


def _models_field(name: str) -> type[models.Field]:
    return cast(type[models.Field], getattr(models, name))


CommaSeparatedIntegerField = _models_field("CommaSeparatedIntegerField")
NullBooleanField = _models_field("NullBooleanField")


def test_general_manager_many_accessor_uses_explicit_relation_field_name() -> None:
    manager_class = Mock()
    filter_result = object()
    manager_class.filter.return_value = filter_result

    related_model = Mock()
    relation_field = Mock(spec=models.Field)
    relation_field.name = "reviewer"
    related_model._meta.get_field.return_value = relation_field

    accessor = _general_manager_many_accessor(
        accessor_name="reviewassignment_set",
        related_model=related_model,
        general_manager_class=manager_class,
        source_model=cast(type[models.Model], models.Model),
        relation_field_name="reviewer",
    )

    interface_instance = type("InterfaceInstance", (), {"pk": 42})()
    result = accessor(interface_instance)

    related_model._meta.get_field.assert_called_once_with("reviewer")
    manager_class.filter.assert_called_once_with(reviewer=42)
    assert result is filter_result


def test_build_field_descriptors_disambiguates_duplicate_reverse_relations() -> None:
    class SourceModel(models.Model):
        class Meta:
            app_label = "general_manager"

    class RelatedModel(models.Model):
        fk_a: Any = models.ForeignKey(SourceModel, on_delete=models.CASCADE)
        fk_b: Any = models.ForeignKey(SourceModel, on_delete=models.CASCADE)

        class Meta:
            app_label = "general_manager"

    for model in (SourceModel, RelatedModel):
        model_key = model._meta.model_name
        if model_key not in apps.all_models["general_manager"]:
            apps.register_model("general_manager", model)
    apps.clear_cache()

    interface_cls = type("InterfaceUnderTest", (), {"_model": SourceModel})

    register_calls: list[dict[str, Any]] = []
    resolve_calls: list[str | None] = []
    original_register = _FieldDescriptorBuilder._register_collection_field
    original_resolve = _FieldDescriptorBuilder._resolve_collection_base_name

    def record_register(self: _FieldDescriptorBuilder, **kwargs: Any) -> None:
        register_calls.append(dict(kwargs))
        return original_register(self, **kwargs)

    def record_resolve(self: _FieldDescriptorBuilder, **kwargs: Any) -> str:
        resolve_calls.append(kwargs.get("relation_field_name"))
        return original_resolve(self, **kwargs)

    with (
        patch.object(
            _FieldDescriptorBuilder,
            "_register_collection_field",
            autospec=True,
            side_effect=record_register,
        ),
        patch.object(
            _FieldDescriptorBuilder,
            "_resolve_collection_base_name",
            autospec=True,
            side_effect=record_resolve,
        ),
    ):
        descriptors = build_field_descriptors(
            interface_cls,
            resolve_many=lambda *_args: None,
        )

    assert "relatedmodel_list" in descriptors
    assert "relatedmodel_set_list" in descriptors

    relation_field_names = {
        call["relation_field_name"]
        for call in register_calls
        if call["relation_field_name"] is not None
    }
    assert relation_field_names == {"fk_a", "fk_b"}
    assert resolve_calls.count("fk_a") == 1
    assert resolve_calls.count("fk_b") == 1


def test_translation_covers_supported_django_field_types() -> None:
    expected_translations = {
        models.BigAutoField: int,
        models.AutoField: int,
        models.SmallAutoField: int,
        models.CharField: str,
        CommaSeparatedIntegerField: str,
        models.TextField: str,
        models.SlugField: str,
        models.BooleanField: bool,
        NullBooleanField: bool,
        models.IntegerField: int,
        models.BigIntegerField: int,
        models.SmallIntegerField: int,
        models.PositiveIntegerField: int,
        models.PositiveSmallIntegerField: int,
        models.PositiveBigIntegerField: int,
        OrderWrt: int,
        models.FloatField: float,
        models.DateField: date,
        models.DateTimeField: datetime,
        models.DurationField: timedelta,
        MeasurementField: Measurement,
        models.DecimalField: Decimal,
        models.EmailField: str,
        models.FilePathField: str,
        models.FileField: str,
        models.ImageField: str,
        models.GenericIPAddressField: str,
        models.IPAddressField: str,
        models.URLField: str,
        models.TimeField: time,
        models.BinaryField: bytes,
        models.UUIDField: UUID,
        models.JSONField: object,
        SearchQueryField: str,
        SearchVectorField: str,
        CompositePrimaryKey: tuple,
        GeneratedField: object,
    }

    assert TRANSLATION == expected_translations
