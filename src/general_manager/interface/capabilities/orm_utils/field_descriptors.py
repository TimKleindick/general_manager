"""Field descriptor helpers shared by database-based interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import re
from typing import TYPE_CHECKING, Callable, Hashable, Iterable, Literal, Protocol, cast
from uuid import UUID

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models.query import prefetch_related_objects

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.interface.base_interface import AttributeTypedDict
from general_manager.interface.utils.errors import DuplicateFieldNameError
from general_manager.measurement.measurement import Measurement
from general_manager.measurement.measurement_field import MeasurementField

if TYPE_CHECKING:
    from general_manager.interface.orm_interface import (
        OrmInterfaceBase,
    )
    from general_manager.manager.general_manager import GeneralManager

type OrmInterfaceInstance = "OrmInterfaceBase[models.Model]"
type DescriptorAccessor = Callable[[OrmInterfaceInstance], object]
type ResolveMany = Callable[[OrmInterfaceInstance, str, str], object]
type DjangoField = models.Field[object, object]
type OrmFileFieldKind = Literal["file", "image"]


class ManyRelationResolver(Protocol):
    """
    Protocol for fallback many-to-many resolution on ORM interface instances.

    Implementations receive the relation accessor name as `field_call` and the
    generated descriptor base name as `field_name`. Exceptions from the
    implementation are propagated to the descriptor accessor caller.
    """

    def _resolve_many_to_many(self, *, field_call: str, field_name: str) -> object: ...


@dataclass(frozen=True)
class FieldDescriptor:
    """
    Describe one generated interface attribute and its value accessor.

    `metadata` always contains `type`, `default`, `is_required`, `is_editable`,
    and `is_derived`. Auto-integer-like fields may add `graphql_scalar`.
    File fields add `orm_field_kind` and `file_clearable`. Relation descriptors
    may add `relation_kind` (`"direct"` or `"collection"`) and `filter_lookup`.
    The `accessor` accepts an ORM interface instance and returns the stored
    value, related manager object, queryset, iterable, or `None` when an
    optional relation is missing.
    """

    name: str
    metadata: AttributeTypedDict
    accessor: DescriptorAccessor


class MissingRelatedFieldsError(RuntimeError):
    """
    Raised when a GeneralManager collection relation cannot be scoped.

    The descriptor builder raises this from the collection accessor only when
    no explicit `relation_filter_name` was supplied and no related field was
    found for the `source_model`. Multiple discovered fields are all used as
    filter constraints; explicit relation hints bypass discovery.
    """

    def __init__(
        self,
        *,
        accessor_name: str,
        related_model: type[models.Model],
        source_model: type[models.Model],
    ) -> None:
        super().__init__(
            "Unable to resolve related fields for collection relation "
            f"'{accessor_name}' from {source_model.__name__} to "
            f"{related_model.__name__}."
        )


TRANSLATION: dict[type[object], type[object]] = {
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
    models.DurationField: timedelta,
    models.UUIDField: UUID,
    models.GenericIPAddressField: str,
    models.FilePathField: str,
    models.BinaryField: bytes,
}


def _translate_field_type(raw_type: type[object]) -> type[object]:
    """Resolve a Django field class to the exposed Python metadata type."""
    for field_type, translated_type in TRANSLATION.items():
        if issubclass(raw_type, field_type):
            return translated_type
    return raw_type


def _graphql_scalar_hint(raw_type: type[object]) -> str | None:
    """Return an optional GraphQL scalar hint for field families needing special handling."""
    if issubclass(raw_type, models.AutoField):
        return None
    if issubclass(raw_type, models.BigIntegerField):
        return "bigint"
    return None


def _orm_file_field_kind(
    field: models.Field[object, object],
) -> OrmFileFieldKind | None:
    """Return the concrete ORM file family, checking images before files."""
    if isinstance(field, models.ImageField):
        return "image"
    if isinstance(field, models.FileField):
        return "file"
    return None


def _to_snake_case(name: str) -> str:
    """Convert a CamelCase class name into snake_case."""
    snake = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    snake = re.sub("([a-z0-9])([A-Z])", r"\1_\2", snake)
    return snake.lower()


def build_field_descriptors(
    interface_cls: type["OrmInterfaceBase[models.Model]"],
    resolve_many: ResolveMany | None = None,
) -> dict[str, FieldDescriptor]:
    """
    Construct field descriptors for an ORM-backed interface class.

    Parameters:
        interface_cls: ORM interface class whose associated Django model is
            inspected to derive descriptors. The class must expose `_model` as
            a Django model class, as generated ORM interface classes do.
        resolve_many: Optional resolver used for many-to-many and reverse
            collection relations. It is called as
            three positional arguments
            `(interface_instance, field_call, field_name)` and must return the
            resolved relation object, queryset, manager, or iterable. If
            omitted, the interface instance's own `_resolve_many_to_many()`
            method is used.

    Returns:
        Mapping from interface attribute name to its descriptor metadata and
        value accessor.

    Raises:
        DuplicateFieldNameError: If generated descriptor names collide.
        AttributeError: If `interface_cls` does not expose the required `_model`
            attribute or the fallback resolver is used on an instance without
            `_resolve_many_to_many()`.
        Exception: Exceptions raised by the optional `resolve_many` callable are
            not wrapped.
    """
    builder = _FieldDescriptorBuilder(
        interface_cls,
        resolve_many=resolve_many or _fallback_resolve_many,
    )
    return builder.build()


class _FieldDescriptorBuilder:
    def __init__(
        self,
        interface_cls: type["OrmInterfaceBase[models.Model]"],
        *,
        resolve_many: ResolveMany,
    ) -> None:
        """
        Create a builder for constructing FieldDescriptor objects for an OrmInterfaceBase subclass.

        Parameters:
            interface_cls: The interface class whose associated ORM model is
                inspected to build descriptors.
            resolve_many: Callable used to resolve collection relations
                (many-to-many and reverse one-to-many). It receives the
                interface instance, relation accessor name, and descriptor base
                name, and returns the resolved related manager, queryset, or
                iterable.
        """
        self.interface_cls = interface_cls
        self.model = interface_cls._model
        self._descriptors: dict[str, FieldDescriptor] = {}
        self._custom_fields, self._ignored_helpers = _collect_custom_fields(self.model)
        self._resolve_many = resolve_many

    def build(self) -> dict[str, FieldDescriptor]:
        """
        Builds field descriptors for the builder's associated interface model.

        Returns:
            dict[str, FieldDescriptor]: Mapping of attribute names to their corresponding FieldDescriptor objects.
        """
        self._add_custom_fields()
        self._add_model_fields()
        self._add_foreign_key_fields()
        self._add_reverse_one_to_one_relations()
        self._add_collection_relations()
        return self._descriptors

    def _add_custom_fields(self) -> None:
        """
        Register field descriptors for model attributes defined directly on the model.

        For each custom field declared on the model, add a FieldDescriptor to the builder's descriptor map with metadata including the field's type, whether it is required, whether it is editable, its default value, and `is_derived=False`. The descriptor uses an accessor that reads the field value from the interface instance.
        """
        for field_name in self._custom_fields:
            field = cast("DjangoField", getattr(self.model, field_name))
            orm_field_kind = _orm_file_field_kind(field)
            self._register(
                attribute_name=field_name,
                raw_type=type(field),
                is_required=(
                    not field.blank and field.default is models.NOT_PROVIDED
                    if orm_field_kind is not None
                    else not field.null
                ),
                is_editable=field.editable,
                default=field.default,
                is_derived=False,
                accessor=_instance_attribute_accessor(field_name),
                orm_field_kind=orm_field_kind,
                file_clearable=field.blank if orm_field_kind is not None else None,
            )

    def _add_model_fields(self) -> None:
        """
        Register non-relational fields from the builder's model into the descriptor map.

        Scans the model's concrete (non-relational) fields, skipping any names marked as ignored, and creates a FieldDescriptor for each remaining field using the field's name, type, required/editable/default properties, and an instance-attribute accessor. Descriptors are marked as not derived.
        """
        for field in _iter_model_fields(self.model):
            if field.name in self._ignored_helpers:
                continue
            orm_field_kind = _orm_file_field_kind(field)
            self._register(
                attribute_name=field.name,
                raw_type=type(field),
                is_required=(
                    not field.blank and field.default is models.NOT_PROVIDED
                    if orm_field_kind is not None
                    else not field.null and field.default is models.NOT_PROVIDED
                ),
                is_editable=field.editable,
                default=field.default,
                is_derived=False,
                accessor=_instance_attribute_accessor(field.name),
                orm_field_kind=orm_field_kind,
                file_clearable=field.blank if orm_field_kind is not None else None,
            )

    def _add_foreign_key_fields(self) -> None:
        """
        Register FieldDescriptor entries for the model's foreign-key fields.

        Iterates the model's foreign-key fields and for each non-generic relation with a resolvable related model, registers a FieldDescriptor using either a general-manager accessor (when the related model exposes `_general_manager_class`) or a direct instance attribute accessor. The registered metadata includes the relation type, whether the field is required or editable, the default value (if any), and that the field is not derived.
        """
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
                raw_id_name = getattr(field, "attname", f"{field.name}_id")
                accessor = _general_manager_accessor(
                    field.name,
                    general_manager_class,
                    raw_id_name=raw_id_name,
                    related_model=related_model,
                )
                relation_type = cast(type[object], general_manager_class)
            else:
                accessor = _instance_attribute_accessor(field.name)
                relation_type = cast(type[object], related_model)
            default = getattr(field, "default", None)
            self._register(
                attribute_name=field.name,
                raw_type=relation_type,
                is_required=not field.null,
                is_editable=field.editable,
                default=default,
                is_derived=False,
                accessor=accessor,
                relation_kind="direct",
                filter_lookup=field.name,
            )
            raw_id_name = getattr(field, "attname", f"{field.name}_id")
            if raw_id_name not in self._descriptors:
                target_field = getattr(field, "target_field", None)
                raw_id_type = type(target_field) if target_field is not None else int
                self._register(
                    attribute_name=raw_id_name,
                    raw_type=raw_id_type,
                    is_required=not field.null,
                    is_editable=False,
                    default=default,
                    is_derived=False,
                    accessor=_instance_attribute_accessor(raw_id_name),
                    filter_lookup=raw_id_name,
                )

    def _add_reverse_one_to_one_relations(self) -> None:
        """Register snake_case aliases for reverse one-to-one relation accessors."""
        for relation in _iter_reverse_one_to_one_relations(self.model):
            related_model = self._resolve_related_model(
                getattr(relation, "related_model", None)
            )
            if related_model is None:
                continue
            accessor_name = relation.get_accessor_name() or relation.name
            attribute_name = _to_snake_case(related_model.__name__)
            if attribute_name in self._descriptors:
                continue
            general_manager_class = getattr(
                related_model, "_general_manager_class", None
            )
            if general_manager_class:
                accessor = _general_manager_accessor(
                    accessor_name,
                    general_manager_class,
                    related_model=related_model,
                )
                relation_type = cast(type[object], general_manager_class)
            else:
                accessor = _instance_attribute_accessor(accessor_name)
                relation_type = cast(type[object], related_model)
            self._register(
                attribute_name=attribute_name,
                raw_type=relation_type,
                is_required=False,
                is_editable=False,
                default=None,
                is_derived=True,
                accessor=accessor,
                relation_kind="direct",
                filter_lookup=relation.name,
            )

    def _add_collection_relations(self) -> None:
        """
        Register collection relation field descriptors for the builder's model.

        Iterates the model's many-to-many and reverse (one-to-many) relations and registers a collection descriptor for each, deriving descriptor names from each relation's base name and accessor name.
        """
        for m2m_field in _iter_many_to_many_fields(self.model):
            if getattr(m2m_field, "auto_created", False) and not getattr(
                m2m_field, "concrete", False
            ):
                relation_field_name = getattr(
                    getattr(m2m_field, "field", None),
                    "name",
                    None,
                )
                relation_filter_name = None
            else:
                relation_field_name = None
                relation_filter_name = _many_to_many_relation_filter_name(
                    m2m_field,
                    self.model,
                )
            self._register_collection_field(
                field=m2m_field,
                base_name=m2m_field.name,
                accessor_name=m2m_field.name,
                relation_field_name=relation_field_name,
                relation_filter_name=relation_filter_name,
            )
        for reverse_relation in _iter_reverse_relations(self.model):
            accessor_name = (
                reverse_relation.get_accessor_name() or reverse_relation.name
            )
            explicit_accessor = getattr(reverse_relation.field, "_related_name", None)
            related_model = getattr(reverse_relation, "related_model", None)
            if (
                isinstance(explicit_accessor, str)
                and explicit_accessor
                and explicit_accessor != "+"
            ):
                base_name = explicit_accessor
            elif related_model is not None:
                base_name = _to_snake_case(related_model.__name__)
            else:
                base_name = reverse_relation.name
            self._register_collection_field(
                field=reverse_relation,
                base_name=base_name,
                accessor_name=accessor_name,
                relation_field_name=getattr(reverse_relation.field, "name", None),
            )

    def _register_collection_field(
        self,
        *,
        field: DjangoField | models.ManyToManyRel | models.ManyToOneRel,
        base_name: str,
        accessor_name: str,
        relation_field_name: str | None = None,
        relation_filter_name: str | None = None,
    ) -> None:
        """
        Register a collection relation as a FieldDescriptor under the generated "<base>_list" attribute.

        If the relation's related model cannot be resolved or the field is a
        GenericForeignKey, registration is skipped. The descriptor's accessor
        and relation type are chosen from the related model's general-manager
        class when available; otherwise a direct-many accessor is used. The
        descriptor's editable flag is set only for many-to-many relations and
        the derived flag is set for reverse (non-many-to-many) relations.

        Parameters:
            field (models.Field | models.ManyToManyRel | models.ManyToOneRel): The model field or relation object representing the collection relation.
            base_name (str): Candidate base name used to derive the final attribute name (final name will be "<base>_list").
            accessor_name (str): Attribute or relation name used by accessors to resolve related objects.
            relation_field_name: Optional concrete relation field name on the
                related model. It contributes relation-field-derived fallback
                descriptor names and scopes GeneralManager-backed accessors.
            relation_filter_name: Optional explicit lookup name passed to the
                related GeneralManager filter. When set, related-field
                discovery is skipped.

        Raises:
            DuplicateFieldNameError: If every generated descriptor name
                candidate already exists.
        """
        related_model = self._resolve_related_model(
            getattr(field, "related_model", None)
        )
        if related_model is None or isinstance(field, GenericForeignKey):
            return
        field_base = self._resolve_collection_base_name(
            base_name=base_name,
            fallback=accessor_name,
            related_model=related_model,
            relation_field_name=relation_field_name,
        )
        attribute_name = f"{field_base}_list"

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
                relation_field_name=relation_field_name,
                relation_filter_name=relation_filter_name,
            )
            relation_type = cast(type[object], general_manager_class)
        else:
            accessor = _direct_many_accessor(
                self._resolve_many,
                accessor_name,
                field_base,
            )
            relation_type = cast(type[object], related_model)

        self._register(
            attribute_name=attribute_name,
            raw_type=relation_type,
            is_required=False,
            is_editable=is_editable,
            default=None,
            is_derived=is_derived,
            accessor=accessor,
            relation_kind="collection",
            filter_lookup=getattr(field, "name", accessor_name),
        )

    def _resolve_collection_base_name(
        self,
        *,
        base_name: str,
        fallback: str,
        related_model: type[models.Model],
        relation_field_name: str | None = None,
    ) -> str:
        """
        Selects a non-conflicting base name for a collection field.

        Parameters:
            base_name: Preferred base name for the collection field.
            fallback: Alternative base name used when `base_name` already has a
                registered `<name>_list` descriptor.
            related_model: Related model used to build the
                `<relation_field_name>_<related_model_name>` fallback.
            relation_field_name: Optional relation field name. When provided,
                candidates are tried in this order: `base_name`, `fallback`,
                `<relation_field_name>_<related_model_name>`, then
                `relation_field_name`.

        Returns:
            The first candidate whose `<candidate>_list` descriptor is not
            already registered.

        Raises:
            DuplicateFieldNameError: If every candidate already has a
                registered `<candidate>_list` descriptor.
        """
        candidates = [base_name, fallback]
        related_model_name = related_model._meta.model_name
        if relation_field_name:
            candidates.append(f"{relation_field_name}_{related_model_name}")
            candidates.append(relation_field_name)

        for candidate in candidates:
            if f"{candidate}_list" not in self._descriptors:
                return candidate
        raise DuplicateFieldNameError()

    def _register(
        self,
        *,
        attribute_name: str,
        raw_type: type[object],
        is_required: bool,
        is_editable: bool,
        default: object,
        is_derived: bool,
        accessor: DescriptorAccessor,
        relation_kind: str | None = None,
        filter_lookup: str | None = None,
        orm_field_kind: OrmFileFieldKind | None = None,
        file_clearable: bool | None = None,
    ) -> None:
        """
        Register a FieldDescriptor for a named interface attribute.

        Parameters:
            attribute_name (str): Unique attribute name to register on the interface.
            raw_type (type): Underlying model field type; translated via TRANSLATION when present to determine the descriptor `type`.
            is_required (bool): Whether the attribute is required.
            is_editable (bool): Whether the attribute is editable.
            default: Default value to record in the descriptor metadata.
            is_derived (bool): Whether the attribute value is derived rather than stored.
            accessor (DescriptorAccessor): Callable that resolves the attribute value from an OrmInterfaceBase instance.

        Raises:
            DuplicateFieldNameError: If `attribute_name` is already registered.
        """
        if attribute_name in self._descriptors:
            raise DuplicateFieldNameError()
        metadata: AttributeTypedDict = {
            "type": _translate_field_type(raw_type),
            "is_required": is_required,
            "is_editable": is_editable,
            "default": default,
            "is_derived": is_derived,
        }
        graphql_scalar = _graphql_scalar_hint(raw_type)
        if graphql_scalar is not None:
            metadata["graphql_scalar"] = graphql_scalar
        if relation_kind is not None:
            metadata["relation_kind"] = relation_kind
        if filter_lookup is not None:
            metadata["filter_lookup"] = filter_lookup
        if orm_field_kind is not None:
            metadata["orm_field_kind"] = orm_field_kind
            metadata["file_clearable"] = bool(file_clearable)
        self._descriptors[attribute_name] = FieldDescriptor(
            name=attribute_name,
            metadata=metadata,
            accessor=accessor,
        )

    def _resolve_related_model(
        self,
        related_model: object,
    ) -> type[models.Model] | None:
        """
        Resolve a related-model reference that may use the string "self" to refer to the builder's model.

        Parameters:
            related_model: Either the string "self", an app-qualified or
                current-app model name, a Django model class, or `None`.

        Returns:
            Optional[type[models.Model]]: The resolved Django model class, or `None` if `related_model` is `None`.
        """
        if related_model == "self":
            return self.model
        if isinstance(related_model, str):
            resolved_model = self._resolve_string_related_model(related_model)
            if resolved_model is not None:
                return resolved_model
        if isinstance(related_model, type) and issubclass(related_model, models.Model):
            return related_model
        if related_model is not None:
            return None
        return None

    def _resolve_string_related_model(
        self,
        related_model: str,
    ) -> type[models.Model] | None:
        app_label: str | None
        if "." in related_model:
            app_label, model_name = related_model.split(".", 1)
        else:
            meta = getattr(self.model, "_meta", None)
            app_label = getattr(meta, "app_label", None)
            model_name = related_model
        if not app_label:
            return None
        try:
            resolved_model = apps.get_model(app_label, model_name)
        except (LookupError, ValueError):
            return None
        if isinstance(resolved_model, type) and issubclass(
            resolved_model, models.Model
        ):
            return resolved_model
        return None


def _collect_custom_fields(
    model: type[models.Model] | models.Model,
) -> tuple[list[str], set[str]]:
    """
    Collects names of Field objects declared directly on a Django model and derives a set of helper attribute names to ignore.

    Parameters:
        model (type[models.Model] | models.Model): A Django model class or instance; the function inspects model.__dict__ so only attributes defined on the class (not inherited) are considered.

    Returns:
        tuple[list[str], set[str]]: A tuple where the first element is a list of attribute names whose values are instances of `models.Field`, and the second element is a set of ignored helper names which includes each field name plus `<field>_value` and `<field>_unit` for each discovered field.
    """
    field_names: list[str] = []
    ignored_helpers: set[str] = set()
    for attr_name, value in model.__dict__.items():
        if isinstance(value, models.Field):
            field_names.append(attr_name)
            ignored_helpers.add(attr_name)
            ignored_helpers.add(f"{attr_name}_value")
            ignored_helpers.add(f"{attr_name}_unit")
    return field_names, ignored_helpers


def _iter_model_fields(model: type[models.Model]) -> Iterable[DjangoField]:
    """
    Yield non-relational fields defined on the given Django model.

    Parameters:
        model (type[models.Model]): The Django model class to inspect.

    Returns:
        Iterable[models.Field]: An iterable of model Field objects excluding relational fields and GenericForeignKey.
    """
    for field in model._meta.get_fields():
        if field.is_relation:
            continue
        if isinstance(field, GenericForeignKey):
            continue
        yield cast("DjangoField", field)


def _iter_foreign_key_fields(
    model: type[models.Model],
) -> Iterable[DjangoField]:
    """
    Yield the model's concrete foreign-key fields (many-to-one and one-to-one), excluding generic foreign keys.

    Parameters:
        model: A Django model class to inspect.

    Returns:
        An iterable of Django `Field` objects for each many-to-one or one-to-one relation on the model, excluding `GenericForeignKey` fields.
    """
    for field in model._meta.get_fields():
        if getattr(field, "auto_created", False) and not getattr(
            field, "concrete", False
        ):
            continue
        if not field.is_relation:
            continue
        if isinstance(field, GenericForeignKey):
            continue
        if getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False):
            yield cast("DjangoField", field)


def _iter_many_to_many_fields(
    model: type[models.Model],
) -> Iterable[DjangoField]:
    """
    Iterate over the model's ManyToMany relational fields.

    Parameters:
        model (type[models.Model]): Django model class to scan for fields.

    Returns:
        Iterable[models.Field]: An iterable of ManyToMany relation fields defined on `model`.
    """
    for field in model._meta.get_fields():
        if getattr(field, "is_relation", False) and getattr(
            field, "many_to_many", False
        ):
            yield cast("DjangoField", field)


def _iter_reverse_one_to_one_relations(
    model: type[models.Model],
) -> Iterable[models.OneToOneRel]:
    """Yield reverse one-to-one relations declared on a Django model."""
    meta = getattr(model, "_meta", None)
    get_fields = getattr(meta, "get_fields", None)
    if not callable(get_fields):
        return
    for field in get_fields():
        if getattr(field, "is_relation", False) and getattr(field, "one_to_one", False):
            if getattr(field, "auto_created", False) and not getattr(
                field, "concrete", False
            ):
                yield cast(models.OneToOneRel, field)


def _iter_reverse_relations(
    model: type[models.Model],
) -> Iterable[models.ManyToOneRel]:
    """
    Yield reverse one-to-many relation fields declared on a Django model.

    Parameters:
        model (type[models.Model]): Django model class to inspect.

    Returns:
        Iterable[models.ManyToOneRel]: An iterable of `ManyToOneRel` objects representing reverse (one-to-many) relations for `model`.
    """
    for field in model._meta.get_fields():
        if getattr(field, "is_relation", False) and getattr(
            field, "one_to_many", False
        ):
            yield cast(models.ManyToOneRel, field)


def _instance_attribute_accessor(field_name: str) -> DescriptorAccessor:
    """
    Create an accessor that reads a named attribute from an interface's underlying model instance.

    Parameters:
        field_name (str): Name of the attribute on the model instance to read.

    Returns:
        A callable that, given an OrmInterfaceBase, returns the value of the specified attribute from its `_instance`.
    """

    def getter(self: OrmInterfaceInstance) -> object:
        """
        Return the value of the specified field from the interface's underlying model instance.

        Returns:
            The attribute value retrieved from the underlying model instance.
        """
        try:
            return getattr(self._instance, field_name)
        except ObjectDoesNotExist:
            return None

    return getter


_MISSING_RELATED = object()
_MANY_RELATION_PREFETCH_CHUNK_SIZE = 1000


def _direct_relation_prefetch_source_rows(
    context: object,
    source_instance: models.Model,
    database_alias: Hashable | None,
) -> (
    tuple[
        tuple[Hashable, Hashable | None],
        type[models.Model],
        list[models.Model],
    ]
    | None
):
    """Return the indexed source rows eligible for one direct relation batch."""
    source_identity = _orm_row_identity(source_instance)
    if source_identity is None:
        return None
    get_items = getattr(context, "get_orm_model_row_items", None)
    if not callable(get_items):
        return None
    row_items = get_items(source_instance.__class__)
    if not row_items:
        return None
    rows: list[models.Model] = []
    for row_key, row in row_items:
        if row_key[1] != database_alias or not isinstance(
            row, source_instance.__class__
        ):
            continue
        if getattr(row, "get_deferred_fields", lambda: set())():
            return None
        rows.append(row)
    if not rows:
        return None
    return source_identity, source_instance.__class__, rows


def _can_prefetch_direct_relation(
    interface_instance: OrmInterfaceInstance,
    manager_type: type["GeneralManager"],
    related_model: type[models.Model] | None,
    database_alias: Hashable | None,
) -> bool:
    """Return whether direct relation hydration can safely use trusted rows."""
    if (
        related_model is None
        or getattr(interface_instance, "_search_date", None) is not None
    ):
        return False
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.interface.orm_interface import OrmInterfaceBase

    interface_cls = getattr(manager_type, "Interface", None)
    interface_model = getattr(interface_cls, "_model", None)
    if interface_model is not related_model:
        return False
    configured_database = getattr(interface_cls, "database", None)
    if configured_database is not None and configured_database != database_alias:
        return False
    if bool(getattr(getattr(interface_model, "_meta", None), "use_soft_delete", False)):
        return False
    if bool(getattr(interface_cls, "_soft_delete_default", False)):
        return False
    if manager_type.__init__ is not GeneralManager.__init__:
        return False
    if getattr(interface_cls, "__init__", None) is not OrmInterfaceBase.__init__:
        return False
    return callable(getattr(manager_type, "_from_trusted_orm_instance", None))


def _prefetch_direct_relation_managers(
    interface_instance: OrmInterfaceInstance,
    *,
    accessor_name: str,
    manager_type: type["GeneralManager"],
    related_model: type[models.Model] | None,
    raw_id_name: str | None,
) -> bool:
    """Bulk-hydrate safe direct relations for all indexed source rows."""
    from general_manager.cache.run_context import current_calculation_run_context

    context = current_calculation_run_context()
    source_instance = getattr(interface_instance, "_instance", None)
    if context is None or not isinstance(source_instance, models.Model):
        return False
    state = getattr(source_instance, "_state", None)
    database_alias = getattr(state, "db", None)
    if not _can_prefetch_direct_relation(
        interface_instance,
        manager_type,
        related_model,
        database_alias,
    ):
        return False
    if related_model is None:
        return False
    source_data = _direct_relation_prefetch_source_rows(
        context,
        source_instance,
        database_alias,
    )
    if source_data is None:
        return False
    source_identity, source_model, rows = source_data
    prefetched = context.get_orm_direct_relation_prefetched_keys(
        source_model,
        database_alias,
        accessor_name,
    )
    if source_identity in prefetched:
        return True
    rows_to_process = [
        row for row in rows if (_orm_row_identity(row) not in prefetched)
    ]
    if not rows_to_process:
        return True
    trusted_hydrate = cast(
        Callable[..., object],
        manager_type._from_trusted_orm_instance,
    )
    relation_cache_keys = []
    if raw_id_name is not None:
        related_ids = {
            getattr(row, raw_id_name, None)
            for row in rows_to_process
            if getattr(row, raw_id_name, None) is not None
        }
        if related_ids:
            related_manager = related_model._default_manager
            if database_alias is not None:
                related_manager = related_manager.using(database_alias)
            related_rows = related_manager.in_bulk(related_ids)
            for related_row in related_rows.values():
                manager = trusted_hydrate(related_row)
                related_identity = _orm_row_identity(related_row)
                if related_identity is None:
                    continue
                relation_cache_keys.append(
                    (manager_type, related_identity[0], related_identity[1])
                )
                context.set_orm_relation_manager(
                    relation_cache_keys[-1],
                    manager,
                )
    else:
        _prefetch_relation_in_chunks(rows_to_process, accessor_name)
        for row in rows_to_process:
            try:
                related_row = getattr(row, accessor_name)
            except ObjectDoesNotExist:
                continue
            if related_row is None:
                continue
            manager = trusted_hydrate(related_row)
            related_identity = _orm_row_identity(related_row)
            if related_identity is None:
                continue
            relation_cache_keys.append(
                (manager_type, related_identity[0], related_identity[1])
            )
            context.set_orm_relation_manager(
                relation_cache_keys[-1],
                manager,
            )
    context.add_orm_direct_relation_prefetched_keys(
        source_model,
        database_alias,
        accessor_name,
        [
            identity
            for identity in (_orm_row_identity(row) for row in rows_to_process)
            if identity is not None
        ],
    )
    return True


def _general_manager_accessor(
    field_name: str,
    manager_class: type[object],
    *,
    raw_id_name: str | None = None,
    related_model: type[models.Model] | None = None,
) -> DescriptorAccessor:
    """
    Create an accessor that resolves a related object's manager from an interface.

    When `raw_id_name` is supplied, the accessor reads the raw foreign-key value
    first and consults Django's internal `_state.fields_cache` only to reuse an
    already-loaded related object. Loaded related rows are hydrated through
    `_from_trusted_orm_instance` when the manager supports it; otherwise the
    manager is constructed from the related object's primary key.

    Parameters:
        field_name (str): Name of the attribute on the underlying model that holds the related object.
        manager_class (type): Class used to hydrate or construct the related manager.
        raw_id_name (str | None): Optional raw foreign-key attribute name used to avoid loading the relation.
        related_model (type[models.Model] | None): Related ORM model used for
            conservative run-scoped bulk hydration.

    Returns:
        DescriptorAccessor: A callable that, given a OrmInterfaceBase, returns the manager instance for the related object, or `None` if the related attribute is `None`.
    """
    manager_type = cast("type[GeneralManager]", manager_class)

    def related_manager_from_instance(related: models.Model) -> object:
        from general_manager.cache.run_context import current_calculation_run_context

        context = current_calculation_run_context()
        identity = _orm_row_identity(related)
        if context is not None and identity is not None:
            cache_key = (manager_type, identity[0], identity[1])
            cached = context.get_orm_relation_manager(cast(Hashable, cache_key))
            if isinstance(cached, manager_type):
                track_own = getattr(
                    cached,
                    "_track_own_identification_dependency_active",
                    None,
                )
                if callable(track_own) and DependencyTracker.is_active():
                    track_own()
                return cached
        trusted_hydrate = getattr(manager_type, "_from_trusted_orm_instance", None)
        if callable(trusted_hydrate):
            manager = trusted_hydrate(related)
        else:
            manager = manager_type(related.pk)
        if context is not None and identity is not None:
            context.set_orm_relation_manager(
                cast(Hashable, (manager_type, identity[0], identity[1])),
                manager,
            )
        return manager

    def raw_id_manager(
        raw_id: object,
        database_alias: object,
        interface_instance: OrmInterfaceInstance,
    ) -> object:
        from general_manager.cache.run_context import current_calculation_run_context

        context = current_calculation_run_context()
        if context is None:
            return manager_type(raw_id)

        _prefetch_direct_relation_managers(
            interface_instance,
            accessor_name=field_name,
            manager_type=manager_type,
            related_model=related_model,
            raw_id_name=raw_id_name,
        )

        cache_key = (manager_type, raw_id, database_alias)
        try:
            cached = context.get_orm_relation_manager(cast(Hashable, cache_key))
        except TypeError:
            return manager_type(raw_id)
        if isinstance(cached, manager_type):
            track_own = getattr(
                cached,
                "_track_own_identification_dependency_active",
                None,
            )
            if callable(track_own) and DependencyTracker.is_active():
                track_own()
            return cached

        manager = manager_type(raw_id)
        context.set_orm_relation_manager(cast(Hashable, cache_key), manager)
        return manager

    def getter(self: OrmInterfaceInstance) -> object:
        """
        Return a manager for the related object without unnecessary ORM loads.

        The raw-id path avoids fetching the relation unless Django already has
        it in `_state.fields_cache`, which is an intentional internal-Django
        optimization point. Cached or directly loaded related objects use
        `_from_trusted_orm_instance` when available; otherwise the manager is
        built from the primary key.

        Returns:
            The related manager instance, or `None` if the related object is `None`.
        """
        if raw_id_name is not None:
            raw_id = getattr(self._instance, raw_id_name, None)
            if raw_id is None:
                return None
            state = getattr(self._instance, "_state", None)
            database_alias = getattr(state, "db", None)
            fields_cache = getattr(state, "fields_cache", {})
            related = fields_cache.get(field_name, _MISSING_RELATED)
            if related is _MISSING_RELATED:
                return raw_id_manager(raw_id, database_alias, self)
            if related is None:
                return None
            return related_manager_from_instance(related)

        _prefetch_direct_relation_managers(
            self,
            accessor_name=field_name,
            manager_type=manager_type,
            related_model=related_model,
            raw_id_name=None,
        )
        try:
            related = getattr(self._instance, field_name)
        except ObjectDoesNotExist:
            return None
        if related is None:
            return None
        return related_manager_from_instance(related)

    return getter


def _general_manager_many_accessor(
    *,
    accessor_name: str,
    related_model: type[models.Model],
    general_manager_class: type[object],
    source_model: type[models.Model],
    relation_field_name: str | None = None,
    relation_filter_name: str | None = None,
) -> DescriptorAccessor:
    """
    Create an accessor that returns a manager filtered to objects related to the given source model instance.

    Parameters:
        accessor_name (str): Logical name of the accessor (for naming/context).
        related_model (type[models.Model]): The model that contains foreign keys referencing the source model.
        general_manager_class (type): A manager-like class that provides a `filter(**kwargs)` method.
        source_model (type[models.Model]): The model class whose primary key is used to filter related objects.
        relation_field_name: Optional concrete field name on `related_model`
            used as the source-model lookup. When set, only that relation field
            is used.
        relation_filter_name: Optional lookup name passed directly to
            `general_manager_class.filter()`. When set, field discovery is
            skipped.

    Returns:
        DescriptorAccessor: A callable that accepts an OrmInterfaceBase instance and returns the manager/QuerySet of related_model instances whose foreign-key fields pointing to `source_model` match the instance's primary key.

    Raises:
        MissingRelatedFieldsError: If no explicit relation filter is supplied
            and no related fields pointing at `source_model` can be discovered.
        FieldDoesNotExist: If `relation_field_name` is supplied but is not a
            field on `related_model`.
    """
    if relation_filter_name is not None:
        related_fields: list[DjangoField] = []
    elif relation_field_name is not None:
        related_fields = [
            cast("DjangoField", related_model._meta.get_field(relation_field_name))
        ]
    else:
        related_fields = [
            cast("DjangoField", rel)
            for rel in related_model._meta.get_fields()
            if getattr(rel, "related_model", None) == source_model
        ]

    def getter(self: OrmInterfaceInstance) -> object:
        """
        Obtain related objects filtered by this interface instance's primary key.

        Returns:
            A manager or queryset containing related model instances whose foreign-key fields equal this interface instance's primary key.
        """
        manager_cls = cast("type[GeneralManager]", general_manager_class)
        if relation_filter_name is not None:
            filter_kwargs = {relation_filter_name: self.pk}
            prefetched_bucket = _prefetched_general_manager_many_bucket(
                self,
                accessor_name=accessor_name,
                relation_filter_name=relation_filter_name,
                manager_cls=manager_cls,
                source_model=source_model,
            )
            if prefetched_bucket is not None:
                return prefetched_bucket
        else:
            filter_kwargs = {field.name: self.pk for field in related_fields}
        if not filter_kwargs:
            raise MissingRelatedFieldsError(
                accessor_name=accessor_name,
                related_model=related_model,
                source_model=source_model,
            )
        return manager_cls.filter(**filter_kwargs)

    return getter


def _orm_row_identity(row: object) -> tuple[Hashable, Hashable | None] | None:
    pk = getattr(row, "pk", None)
    try:
        hash(pk)
    except TypeError:
        return None
    state = getattr(row, "_state", None)
    database_alias = getattr(state, "db", None)
    try:
        hash(database_alias)
    except TypeError:
        return None
    return cast(Hashable, pk), cast(Hashable | None, database_alias)


def _can_use_prefetched_general_manager_many_bucket(
    manager_cls: type["GeneralManager"],
    database_alias: Hashable | None,
) -> bool:
    interface_cls = manager_cls.Interface
    interface_model = getattr(interface_cls, "_model", None)
    configured_database = getattr(interface_cls, "database", None)
    if configured_database is not None and configured_database != database_alias:
        return False
    meta = getattr(interface_model, "_meta", None)
    if bool(getattr(meta, "use_soft_delete", False)):
        return False
    if bool(getattr(interface_cls, "_soft_delete_default", False)):
        return False
    return True


def _prefetch_relation_in_chunks(
    rows: list[models.Model],
    accessor_name: str,
) -> None:
    for index in range(0, len(rows), _MANY_RELATION_PREFETCH_CHUNK_SIZE):
        prefetch_related_objects(
            rows[index : index + _MANY_RELATION_PREFETCH_CHUNK_SIZE],
            accessor_name,
        )


def _prefetched_general_manager_many_bucket(
    interface_instance: OrmInterfaceInstance,
    *,
    accessor_name: str,
    relation_filter_name: str,
    manager_cls: type["GeneralManager"],
    source_model: type[models.Model],
) -> object | None:
    """Return a bucket backed by a run-scoped prefetched M2M relation."""
    source_instance = getattr(interface_instance, "_instance", None)
    if not isinstance(source_instance, source_model):
        return None
    source_identity = _orm_row_identity(source_instance)
    if source_identity is None:
        return None
    source_primary_key, database_alias = source_identity
    if not _can_use_prefetched_general_manager_many_bucket(
        manager_cls,
        database_alias,
    ):
        return None

    from general_manager.bucket.database_bucket import DatabaseBucket
    from general_manager.cache.run_context import current_calculation_run_context

    context = current_calculation_run_context()
    if context is None:
        return None
    indexed_source_row = context.get_orm_model_row(
        source_model,
        source_primary_key,
        database_alias,
    )
    if indexed_source_row is None:
        return None

    prefetched_keys = context.get_orm_model_relation_prefetched_keys(
        source_model,
        database_alias,
        accessor_name,
    )

    def build_bucket() -> DatabaseBucket["GeneralManager"]:
        queryset = getattr(indexed_source_row, accessor_name).all()
        bucket = DatabaseBucket(
            queryset,
            manager_cls,
            {relation_filter_name: [source_primary_key]},
        )
        bucket._set_trusted_query_signature(
            (
                "prefetched-direct-many-relation-v1",
                source_model,
                database_alias,
                accessor_name,
                relation_filter_name,
                source_primary_key,
            )
        )
        return bucket

    if source_identity in prefetched_keys:
        return build_bucket()

    row_items = context.get_orm_model_row_items(source_model)
    if not row_items:
        return None

    rows_to_prefetch: list[models.Model] = []
    row_keys_to_prefetch = []
    for row_key, row in row_items:
        if row_key[1] != database_alias or row_key in prefetched_keys:
            continue
        if isinstance(row, source_model):
            rows_to_prefetch.append(row)
            row_keys_to_prefetch.append(row_key)

    if rows_to_prefetch:
        _prefetch_relation_in_chunks(rows_to_prefetch, accessor_name)
        context.add_orm_model_relation_prefetched_keys(
            source_model,
            database_alias,
            accessor_name,
            row_keys_to_prefetch,
        )

    return build_bucket()


def _many_to_many_relation_filter_name(
    field: DjangoField,
    source_model: type[models.Model],
) -> str | None:
    """Return the target-side query lookup for a direct many-to-many field."""
    remote_field = getattr(field, "remote_field", None)
    related_query_name = getattr(remote_field, "related_query_name", None)
    if callable(related_query_name):
        related_query_name = related_query_name()
    if isinstance(related_query_name, str) and related_query_name:
        return related_query_name
    related_name = getattr(remote_field, "related_name", None)
    if related_name == "+":
        return None
    if isinstance(related_name, str) and related_name:
        return related_name
    source_meta = getattr(source_model, "_meta", None)
    source_model_name = getattr(source_meta, "model_name", None)
    if isinstance(source_model_name, str) and source_model_name:
        return source_model_name
    return None


def _direct_many_accessor(
    resolver: ResolveMany,
    field_call: str,
    field_name: str,
) -> DescriptorAccessor:
    """
    Create an accessor that resolves a direct many-to-many relation from an OrmInterfaceBase using the provided resolver.

    Parameters:
        resolver: Function that resolves the relation given
            `(interface_instance, field_call, field_name)` and returns the
            related manager, queryset, or iterable.
        field_call (str): Attribute or call expression used to access the related manager or relation on the underlying model.
        field_name (str): Base field name used to identify the relation when resolving many-to-many values.

    Returns:
        DescriptorAccessor: A callable that accepts an OrmInterfaceBase instance and returns the resolved collection for the specified many-to-many relation.
    """

    def getter(self: OrmInterfaceInstance) -> object:
        """
        Resolve the collection relation for the given interface instance.

        Returns:
            The resolved collection value for the field (typically a manager or queryset for the related objects).
        """
        return resolver(self, field_call, field_name)

    return getter


def _fallback_resolve_many(
    interface_instance: OrmInterfaceInstance,
    field_call: str,
    field_name: str,
) -> object:
    """
    Resolve a many-to-many relation for an ORM-backed interface using its default many-to-many resolver.

    Parameters:
        interface_instance (OrmInterfaceBase): The interface instance whose many-to-many relation is being resolved.
        field_call (str): The relation accessor or lookup string used to fetch the related objects.
        field_name (str): The logical field name for the relation on the interface.

    Returns:
        object: The value used to access the related objects (for example, a manager, queryset, or iterable) as produced by the interface's many-to-many resolver.
    """
    resolver = cast(ManyRelationResolver, interface_instance)
    return resolver._resolve_many_to_many(
        field_call=field_call,
        field_name=field_name,
    )
