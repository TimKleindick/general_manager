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
    Construct field descriptors for the given DB-based interface class.

    Parameters:
        interface_cls (type[DBBasedInterface]): A subclass of DBBasedInterface whose associated model will be inspected.

    Returns:
        dict[str, FieldDescriptor]: Mapping from attribute name to its FieldDescriptor containing metadata and an accessor.
    """
    builder = _FieldDescriptorBuilder(interface_cls)
    return builder.build()


class _FieldDescriptorBuilder:
    def __init__(self, interface_cls: type["DBBasedInterface"]) -> None:
        """
        Initialize a builder for constructing field descriptors for a DBBasedInterface subclass.

        Parameters:
                interface_cls (type[DBBasedInterface]): The interface class whose associated Django model will be inspected to build field descriptors. The builder initializes its internal state (model reference, empty descriptor mapping) and collects custom model fields and helper names to ignore.
        """
        self.interface_cls = interface_cls
        self.model = interface_cls._model  # type: ignore[attr-defined]
        self._descriptors: dict[str, FieldDescriptor] = {}
        self._custom_fields, self._ignored_helpers = _collect_custom_fields(self.model)

    def build(self) -> dict[str, FieldDescriptor]:
        """
        Builds and returns field descriptors for the builder's associated interface model.

        Constructs descriptors covering custom model attributes, regular model fields, foreign-key relations, and collection relations, and returns a mapping from attribute name to its FieldDescriptor.

        Returns:
            dict[str, FieldDescriptor]: Mapping of attribute names to their corresponding FieldDescriptor objects.
        """
        self._add_custom_fields()
        self._add_model_fields()
        self._add_foreign_key_fields()
        self._add_collection_relations()
        return self._descriptors

    def _add_custom_fields(self) -> None:
        """
        Register field descriptors for model attributes defined directly on the model.

        For each custom field declared on the model, add a FieldDescriptor to the builder's descriptor map with metadata including the field's type, whether it is required, whether it is editable, its default value, and `is_derived=False`. The descriptor uses an accessor that reads the field value from the interface instance.
        """
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
        """
        Register non-relational fields from the builder's model into the descriptor map.

        Scans the model's concrete (non-relational) fields, skipping any names marked as ignored, and creates a FieldDescriptor for each remaining field using the field's name, type, required/editable/default properties, and an instance-attribute accessor. Descriptors are marked as not derived.
        """
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
        """
        Register collection relation field descriptors for the builder's model.

        Iterates the model's many-to-many and reverse (one-to-many) relations and registers a collection descriptor for each, deriving descriptor names from each relation's base name and accessor name.
        """
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
        """
        Register a collection-based field descriptor (many-to-many or reverse one-to-many) under a generated "<base>_list" attribute.

        Adds a FieldDescriptor to the builder's descriptor map for the collection relation, choosing an appropriate accessor and metadata (relation type, editable flag, derived flag). If the related model cannot be resolved or the field is a GenericForeignKey, the registration is skipped.

        Parameters:
            field (models.Field | models.ManyToManyRel | models.ManyToOneRel): The model field or relation object representing the collection relation.
            base_name (str): Candidate base name for the attribute; used to derive the final attribute name before appending "_list".
            accessor_name (str): Name of the attribute or relation used by accessors to resolve related objects.
        """
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
        """
        Selects a non-conflicting base name for a collection field.

        Parameters:
                candidate (str): Proposed base name for the collection field.
                fallback (str): Alternative base name to use if `candidate` is already registered.

        Returns:
                base_name (str): `candidate` if it is not already registered, otherwise `fallback`.

        Raises:
                DuplicateFieldNameError: If both `candidate` and `fallback` are already registered.
        """
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
        """
        Register a new FieldDescriptor for an attribute on the builder.

        Parameters:
                attribute_name (str): Name of the interface attribute to register; must be unique.
                raw_type (type): The model field type used to determine the metadata `type` (mapped via TRANSLATION when available).
                is_required (bool): Whether the attribute is required.
                is_editable (bool): Whether the attribute is editable.
                default (Any): The attribute's default value to record in metadata.
                is_derived (bool): Whether the attribute value is derived rather than stored directly.
                accessor (DescriptorAccessor): Callable that resolves the attribute value from a DBBasedInterface instance.

        Raises:
                DuplicateFieldNameError: If `attribute_name` is already registered.
        """
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
        """
        Resolve a related-model reference that may use the string "self" to refer to the builder's model.

        Parameters:
            related_model (Any): Either the string "self" to indicate the builder's model, a Django model class, or None.

        Returns:
            Optional[type[models.Model]]: The resolved Django model class, or `None` if `related_model` is `None`.
        """
        if related_model == "self":
            return cast(type[models.Model], self.model)
        return cast(Optional[type[models.Model]], related_model)


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


def _iter_model_fields(model: type[models.Model]) -> Iterable[models.Field]:
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
        yield cast(models.Field, field)


def _iter_foreign_key_fields(
    model: type[models.Model],
) -> Iterable[models.Field]:
    """
    Yield the model's concrete foreign-key fields (many-to-one and one-to-one), excluding generic foreign keys.

    Parameters:
        model: A Django model class to inspect.

    Returns:
        An iterable of Django `Field` objects for each many-to-one or one-to-one relation on the model, excluding `GenericForeignKey` fields.
    """
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
            yield cast(models.Field, field)


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
    Create an accessor that reads a named attribute from a DBBasedInterface's underlying model instance.

    Parameters:
        field_name (str): The model attribute name to read from the interface's `_instance`.

    Returns:
        accessor (Callable[["DBBasedInterface"], Any]): A function that, given a DBBasedInterface, returns the value of the specified attribute on its `_instance`.
    """

    def getter(self: "DBBasedInterface") -> Any:  # type: ignore[name-defined]
        return getattr(self._instance, field_name)

    return getter


def _general_manager_accessor(
    field_name: str, manager_class: type
) -> DescriptorAccessor:
    """
    Create an accessor that resolves a related object's manager instance from a DBBasedInterface.

    Parameters:
        field_name (str): Name of the attribute on the underlying model that holds the related object.
        manager_class (type): Class to instantiate with the related object's primary key to obtain its manager.

    Returns:
        DescriptorAccessor: A callable that, given a DBBasedInterface, returns the manager instance for the related object, or `None` if the related attribute is `None`.
    """

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
    """
    Create a descriptor accessor that returns the provided general manager filtered to objects related to the given source model instance.

    The returned callable accepts a DBBasedInterface instance and calls the provided general manager's `filter` with keyword arguments mapping each field (on the related model) that points to the source model to the interface instance's primary key.

    Parameters:
        accessor_name (str): Logical name of the accessor (used for naming only).
        related_model (type[models.Model]): Model that contains foreign keys referencing the source model.
        general_manager_class (type): Manager-like class providing a `filter(**kwargs)` method.
        source_model (type[models.Model]): Model class of the source instance whose primary key is used for filtering.

    Returns:
        DescriptorAccessor: A callable that, given a DBBasedInterface, returns the manager filtered to related objects whose foreign key to `source_model` equals the instance's primary key.
    """
    related_fields = [
        rel
        for rel in related_model._meta.get_fields()
        if getattr(rel, "related_model", None) == source_model
    ]

    def getter(self: "DBBasedInterface") -> Any:  # type: ignore[name-defined]
        """
        Retrieve related objects from the general manager filtered to this interface instance.

        Returns:
            A manager/QuerySet of related model instances whose foreign key field(s) equal this interface instance's primary key.
        """
        filter_kwargs = {field.name: self.pk for field in related_fields}
        manager_cls = cast(Any, general_manager_class)
        return manager_cls.filter(**filter_kwargs)

    return getter


def _direct_many_accessor(field_call: str, field_name: str) -> DescriptorAccessor:
    """
    Create an accessor that resolves a direct many-to-many relationship value from a DBBasedInterface instance.

    Parameters:
        field_call (str): The attribute or call expression used to access the related manager or relation on the underlying model.
        field_name (str): The base field name used to identify the relation when resolving many-to-many values.

    Returns:
        DescriptorAccessor: A callable that accepts a DBBasedInterface instance and returns the resolved collection for the specified many-to-many relation.
    """

    def getter(self: "DBBasedInterface") -> Any:  # type: ignore[name-defined]
        return self._resolve_many_to_many(field_call=field_call, field_name=field_name)

    return getter
