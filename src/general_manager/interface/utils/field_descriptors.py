"""Field descriptor helpers shared by database-based interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional, cast

from django.contrib.contenttypes.fields import GenericForeignKey
from django.db import models

from general_manager.interface.base_interface import AttributeTypedDict
from general_manager.interface.utils.errors import DuplicateFieldNameError
from general_manager.measurement.measurement import Measurement
from general_manager.measurement.measurement_field import MeasurementField

if TYPE_CHECKING:
    from general_manager.interface.database_based_interface import DBBasedInterface

DescriptorAccessor = Callable[["DBBasedInterface"], Any]


@dataclass(frozen=True)
class FieldDescriptor:
    """Describe an interface attribute and the callable that resolves its value."""

    name: str
    metadata: AttributeTypedDict
    accessor: DescriptorAccessor


TRANSLATION: dict[type[models.Field], type] = {
    models.fields.BigAutoField: int,
    models.AutoField: int,
    models.CharField: str,
    models.TextField: str,
    models.BooleanField: bool,
    models.IntegerField: int,
    models.FloatField: float,
    models.DateField: date,
    models.DateTimeField: datetime,
    MeasurementField: Measurement,
    models.DecimalField: Decimal,
    models.EmailField: str,
    models.FileField: str,
    models.ImageField: str,
    models.URLField: str,
    models.TimeField: time,
}


def build_field_descriptors(
    interface_cls: type["DBBasedInterface"],
) -> dict[str, FieldDescriptor]:
    """
    Inspect the interface model to build descriptors used for attribute metadata and accessors.
    """
    builder = _FieldDescriptorBuilder(interface_cls)
    return builder.build()


class _FieldDescriptorBuilder:
    def __init__(self, interface_cls: type["DBBasedInterface"]) -> None:
        self.interface_cls = interface_cls
        self.model = interface_cls._model  # type: ignore[attr-defined]
        self._descriptors: dict[str, FieldDescriptor] = {}
        self._custom_fields, self._ignored_helpers = _collect_custom_fields(self.model)

    def build(self) -> dict[str, FieldDescriptor]:
        self._add_custom_fields()
        self._add_model_fields()
        self._add_foreign_key_fields()
        self._add_collection_relations()
        return self._descriptors

    def _add_custom_fields(self) -> None:
        for field_name in self._custom_fields:
            field = cast(models.Field, getattr(self.model, field_name))
            self._register(
                attribute_name=field_name,
                raw_type=type(field),
                is_required=not field.null,
                is_editable=field.editable,
                default=field.default,
                is_derived=False,
                accessor=_instance_attribute_accessor(field_name),
            )

    def _add_model_fields(self) -> None:
        for field in _iter_model_fields(self.model):
            if field.name in self._ignored_helpers:
                continue
            self._register(
                attribute_name=field.name,
                raw_type=type(field),
                is_required=not field.null and field.default is models.NOT_PROVIDED,
                is_editable=field.editable,
                default=field.default,
                is_derived=False,
                accessor=_instance_attribute_accessor(field.name),
            )

    def _add_foreign_key_fields(self) -> None:
        for field in _iter_foreign_key_fields(self.model):
            if isinstance(field, GenericForeignKey):
                continue
            related_model = self._resolve_related_model(field.related_model)
            if related_model is None:
                continue
            general_manager_class = getattr(
                related_model, "_general_manager_class", None
            )
            if general_manager_class:
                accessor = _general_manager_accessor(field.name, general_manager_class)
                relation_type = cast(type, general_manager_class)
            else:
                accessor = _instance_attribute_accessor(field.name)
                relation_type = cast(type, related_model)
            default = getattr(field, "default", None)
            self._register(
                attribute_name=field.name,
                raw_type=relation_type,
                is_required=not field.null,
                is_editable=field.editable,
                default=default,
                is_derived=False,
                accessor=accessor,
            )

    def _add_collection_relations(self) -> None:
        for m2m_field in _iter_many_to_many_fields(self.model):
            self._register_collection_field(
                field=m2m_field,
                base_name=m2m_field.name,
                accessor_name=m2m_field.name,
            )
        for reverse_relation in _iter_reverse_relations(self.model):
            accessor_name = reverse_relation.get_accessor_name()
            self._register_collection_field(
                field=reverse_relation,
                base_name=reverse_relation.name,
                accessor_name=accessor_name,
            )

    def _register_collection_field(
        self,
        *,
        field: models.Field | models.ManyToManyRel | models.ManyToOneRel,
        base_name: str,
        accessor_name: str,
    ) -> None:
        field_base = self._resolve_collection_base_name(base_name, accessor_name)
        attribute_name = f"{field_base}_list"
        related_model = self._resolve_related_model(
            getattr(field, "related_model", None)
        )
        if related_model is None or isinstance(field, GenericForeignKey):
            return

        general_manager_class = getattr(related_model, "_general_manager_class", None)
        is_many_to_many = bool(getattr(field, "many_to_many", False))
        is_editable = bool(getattr(field, "editable", False) and is_many_to_many)
        is_derived = not is_many_to_many

        if general_manager_class:
            accessor = _general_manager_many_accessor(
                accessor_name=accessor_name,
                related_model=related_model,
                general_manager_class=general_manager_class,
                source_model=self.model,
            )
            relation_type = cast(type, general_manager_class)
        else:
            accessor = _direct_many_accessor(accessor_name, field_base)
            relation_type = cast(type, related_model)

        self._register(
            attribute_name=attribute_name,
            raw_type=relation_type,
            is_required=False,
            is_editable=is_editable,
            default=None,
            is_derived=is_derived,
            accessor=accessor,
        )

    def _resolve_collection_base_name(self, candidate: str, fallback: str) -> str:
        if candidate in self._descriptors:
            if fallback not in self._descriptors:
                return fallback
            raise DuplicateFieldNameError()
        return candidate

    def _register(
        self,
        *,
        attribute_name: str,
        raw_type: type,
        is_required: bool,
        is_editable: bool,
        default: Any,
        is_derived: bool,
        accessor: DescriptorAccessor,
    ) -> None:
        if attribute_name in self._descriptors:
            raise DuplicateFieldNameError()
        metadata: AttributeTypedDict = {
            "type": TRANSLATION.get(raw_type, raw_type),
            "is_required": is_required,
            "is_editable": is_editable,
            "default": default,
            "is_derived": is_derived,
        }
        self._descriptors[attribute_name] = FieldDescriptor(
            name=attribute_name,
            metadata=metadata,
            accessor=accessor,
        )

    def _resolve_related_model(
        self,
        related_model: Any,
    ) -> Optional[type[models.Model]]:
        if related_model == "self":
            return cast(type[models.Model], self.model)
        return cast(Optional[type[models.Model]], related_model)


def _collect_custom_fields(
    model: type[models.Model] | models.Model,
) -> tuple[list[str], set[str]]:
    field_names: list[str] = []
    ignored_helpers: set[str] = set()
    for attr_name, value in model.__dict__.items():
        if isinstance(value, models.Field):
            field_names.append(attr_name)
            ignored_helpers.add(attr_name)
            ignored_helpers.add(f"{attr_name}_value")
            ignored_helpers.add(f"{attr_name}_unit")
    return field_names, ignored_helpers


def _iter_model_fields(model: type[models.Model]) -> Iterable[models.Field]:
    for field in model._meta.get_fields():
        if field.is_relation:
            continue
        if isinstance(field, GenericForeignKey):
            continue
        yield cast(models.Field, field)


def _iter_foreign_key_fields(
    model: type[models.Model],
) -> Iterable[models.Field]:
    for field in model._meta.get_fields():
        if not field.is_relation:
            continue
        if isinstance(field, GenericForeignKey):
            continue
        if getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False):
            yield cast(models.Field, field)


def _iter_many_to_many_fields(
    model: type[models.Model],
) -> Iterable[models.Field]:
    for field in model._meta.get_fields():
        if getattr(field, "is_relation", False) and getattr(
            field, "many_to_many", False
        ):
            yield cast(models.Field, field)


def _iter_reverse_relations(
    model: type[models.Model],
) -> Iterable[models.ManyToOneRel]:
    for field in model._meta.get_fields():
        if getattr(field, "is_relation", False) and getattr(
            field, "one_to_many", False
        ):
            yield cast(models.ManyToOneRel, field)


def _instance_attribute_accessor(field_name: str) -> DescriptorAccessor:
    def getter(self: "DBBasedInterface") -> Any:  # type: ignore[name-defined]
        return getattr(self._instance, field_name)

    return getter


def _general_manager_accessor(
    field_name: str, manager_class: type
) -> DescriptorAccessor:
    def getter(self: "DBBasedInterface") -> Any:  # type: ignore[name-defined]
        related = getattr(self._instance, field_name)
        if related is None:
            return None
        return manager_class(related.pk)

    return getter


def _general_manager_many_accessor(
    *,
    accessor_name: str,
    related_model: type[models.Model],
    general_manager_class: type,
    source_model: type[models.Model],
) -> DescriptorAccessor:
    related_fields = [
        rel
        for rel in related_model._meta.get_fields()
        if getattr(rel, "related_model", None) == source_model
    ]

    def getter(self: "DBBasedInterface") -> Any:  # type: ignore[name-defined]
        filter_kwargs = {field.name: self.pk for field in related_fields}
        manager_cls = cast(Any, general_manager_class)
        return manager_cls.filter(**filter_kwargs)

    return getter


def _direct_many_accessor(field_call: str, field_name: str) -> DescriptorAccessor:
    def getter(self: "DBBasedInterface") -> Any:  # type: ignore[name-defined]
        return self._resolve_many_to_many(field_call=field_call, field_name=field_name)

    return getter
