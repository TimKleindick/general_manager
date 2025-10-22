"""Database-backed interface implementation for GeneralManager classes."""

from __future__ import annotations
from typing import Any, Callable, ClassVar, Generic, TYPE_CHECKING, TypeVar, Type, cast
from django.db import models

from datetime import datetime, date, time, timedelta
from django.utils import timezone
from general_manager.measurement.measurement import Measurement
from general_manager.measurement.measurementField import MeasurementField
from decimal import Decimal
from general_manager.factory.autoFactory import AutoFactory
from general_manager.interface.baseInterface import (
    InterfaceBase,
    classPostCreationMethod,
    classPreCreationMethod,
    generalManagerClassName,
    attributes,
    interfaceBaseClass,
    newlyCreatedGeneralManagerClass,
    newlyCreatedInterfaceClass,
    relatedClass,
    AttributeTypedDict,
)
from general_manager.manager.input import Input
from general_manager.bucket.databaseBucket import DatabaseBucket
from general_manager.interface.models import (
    GeneralManagerBasisModel,
    GeneralManagerModel,
    getFullCleanMethode,
)
from django.contrib.contenttypes.fields import GenericForeignKey

if TYPE_CHECKING:
    from general_manager.rule.rule import Rule

modelsModel = TypeVar("modelsModel", bound=models.Model)

MODEL_TYPE = TypeVar("MODEL_TYPE", bound=GeneralManagerBasisModel)


class DuplicateFieldNameError(ValueError):
    """Raised when a dynamically generated field name conflicts with an existing one."""

    def __init__(self) -> None:
        """
        Initialize the DuplicateFieldNameError with a default descriptive message.

        This exception indicates a conflict where a dynamically generated field name duplicates an existing name; the default message is "Field name already exists."
        """
        super().__init__("Field name already exists.")


class DBBasedInterface(InterfaceBase, Generic[MODEL_TYPE]):
    """Interface implementation that persists data using Django ORM models."""

    _model: Type[MODEL_TYPE]
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

    def __init__(
        self,
        *args: Any,
        search_date: datetime | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the interface and load its underlying model instance.

        Positional and keyword arguments are forwarded to the parent interface to establish identification.
        search_date, when provided, causes the instance to be resolved from historical records at or before that timestamp; if omitted, the current database record is loaded.

        Parameters:
            *args: Positional identification arguments forwarded to the parent interface.
            search_date (datetime | None): Timestamp to select a historical record; `None` to use the current record.
            **kwargs: Keyword identification arguments forwarded to the parent interface.
        """
        super().__init__(*args, **kwargs)
        self.pk = self.identification["id"]
        self._instance: MODEL_TYPE = self.getData(search_date)

    def getData(self, search_date: datetime | None = None) -> MODEL_TYPE:
        """
        Fetch the underlying model instance, optionally as of a historical date.

        Parameters:
            search_date (datetime | None): When provided, retrieve the state closest to this timestamp.

        Returns:
            MODEL_TYPE: Current or historical instance matching the primary key.
        """
        model = self._model
        instance = cast(MODEL_TYPE, model.objects.get(pk=self.pk))
        if search_date is not None:
            # Normalize to aware datetime if needed
            if timezone.is_naive(search_date):
                search_date = timezone.make_aware(search_date)
            if search_date <= timezone.now() - timedelta(seconds=5):
                historical = self.getHistoricalRecord(instance, search_date)
                if historical is not None:
                    instance = historical
        return instance

    @staticmethod
    def __parseKwargs(**kwargs: Any) -> dict[str, Any]:
        """
        Convert keyword arguments into ORM-friendly values.

        Parameters:
            **kwargs (Any): Filter or update arguments potentially containing manager instances.

        Returns:
            dict[str, Any]: Arguments ready to be passed to Django ORM methods.
        """
        from general_manager.manager.generalManager import GeneralManager

        parsed_kwargs: dict[str, Any] = {}
        for key, value in kwargs.items():
            if isinstance(value, GeneralManager):
                parsed_kwargs[key] = getattr(
                    value._interface, "_instance", value.identification["id"]
                )
            else:
                parsed_kwargs[key] = value
        return parsed_kwargs

    @classmethod
    def filter(cls, **kwargs: Any) -> DatabaseBucket:
        """
        Return a bucket of model instances filtered by the provided lookups.

        Parameters:
            **kwargs (Any): Django-style filter expressions.

        Returns:
            DatabaseBucket: Bucket wrapping the filtered queryset.
        """

        kwargs = cls.__parseKwargs(**kwargs)

        return DatabaseBucket(
            cls._model.objects.filter(**kwargs),
            cls._parent_class,
            cls.__createFilterDefinitions(**kwargs),
        )

    @classmethod
    def exclude(cls, **kwargs: Any) -> DatabaseBucket:
        """
        Return a bucket excluding model instances that match the provided lookups.

        Parameters:
            **kwargs (Any): Django-style exclusion expressions.

        Returns:
            DatabaseBucket: Bucket wrapping the excluded queryset.
        """
        kwargs = cls.__parseKwargs(**kwargs)

        return DatabaseBucket(
            cls._model.objects.exclude(**kwargs),
            cls._parent_class,
            cls.__createFilterDefinitions(**kwargs),
        )

    @staticmethod
    def __createFilterDefinitions(**kwargs: Any) -> dict[str, Any]:
        """
        Build a filter-definition mapping from Django-style kwargs.

        Parameters:
            **kwargs (Any): Filter expressions provided by the caller.

        Returns:
            dict[str, Any]: Mapping of filter names to their values.
        """
        filter_definitions: dict[str, Any] = {}
        for key, value in kwargs.items():
            filter_definitions[key] = value
        return filter_definitions

    @classmethod
    def getHistoricalRecord(
        cls, instance: MODEL_TYPE, search_date: datetime | None = None
    ) -> MODEL_TYPE | None:
        """
        Retrieves the most recent historical record of a model instance at or before a specified date.

        Parameters:
            instance (MODEL_TYPE): Model instance whose history is queried.
            search_date (datetime | None): Cutoff datetime used to select the historical record.

        Returns:
            MODEL_TYPE | None: Historical instance as of the specified date, if available.
        """
        historical = instance.history.filter(history_date__lte=search_date).last()  # type: ignore[attr-defined]
        return cast(MODEL_TYPE | None, historical)

    @classmethod
    def getAttributeTypes(cls) -> dict[str, AttributeTypedDict]:
        """
        Builds a mapping of model attribute names to their type metadata for the interface.

        Produces entries for model fields, custom measurement-like fields, foreign-key relations, many-to-many relations, and reverse one-to-many relations. For related models that expose a general manager class, the attribute type is that manager class; many-to-many and reverse relation attributes are exposed with a "_list" suffix. GenericForeignKey fields are omitted.

        Returns:
            dict[str, AttributeTypedDict]: Mapping from attribute name to metadata with keys:
                - `type`: the attribute's Python type or general-manager class for related models (common Django field classes are translated to built-in Python types),
                - `is_derived`: `True` for attributes computed from relations, `False` for direct model fields,
                - `is_required`: `True` if the attribute must be present (e.g., field null is False and no default),
                - `is_editable`: `True` if the field is editable on the model,
                - `default`: the field's default value or `None` when not applicable.

        Raises:
            DuplicateFieldNameError: if a generated attribute name collides with an existing attribute name.
        """
        TRANSLATION: dict[Type[models.Field[Any, Any]], type] = {
            models.fields.BigAutoField: int,
            models.AutoField: int,
            models.CharField: str,
            models.TextField: str,
            models.BooleanField: bool,
            models.IntegerField: int,
            models.FloatField: float,
            models.DateField: datetime,
            models.DateTimeField: datetime,
            MeasurementField: Measurement,
            models.DecimalField: Decimal,
            models.EmailField: str,
            models.FileField: str,
            models.ImageField: str,
            models.URLField: str,
            models.TimeField: datetime,
        }
        fields: dict[str, AttributeTypedDict] = {}
        field_name_list, to_ignore_list = cls.handleCustomFields(cls._model)
        for field_name in field_name_list:
            field = cast(models.Field, getattr(cls._model, field_name))
            fields[field_name] = {
                "type": type(field),
                "is_derived": False,
                "is_required": not field.null,
                "is_editable": field.editable,
                "default": field.default,
            }

        for field_name in cls.__getModelFields():
            if field_name not in to_ignore_list:
                field = cast(models.Field, getattr(cls._model, field_name).field)
                fields[field_name] = {
                    "type": type(field),
                    "is_derived": False,
                    "is_required": not field.null
                    and field.default is models.NOT_PROVIDED,
                    "is_editable": field.editable,
                    "default": field.default,
                }

        for field_name in cls.__getForeignKeyFields():
            field = cls._model._meta.get_field(field_name)
            if isinstance(field, GenericForeignKey):
                continue
            related_model = field.related_model
            if related_model == "self":
                related_model = cls._model
            if related_model and hasattr(
                related_model,
                "_general_manager_class",
            ):
                related_model = related_model._general_manager_class  # type: ignore

            if related_model is not None:
                default = None
                if hasattr(field, "default"):
                    default = field.default  # type: ignore
                fields[field_name] = {
                    "type": cast(type, related_model),
                    "is_derived": False,
                    "is_required": not field.null,
                    "is_editable": field.editable,
                    "default": default,
                }

        for field_name, field_call in [
            *cls.__getManyToManyFields(),
            *cls.__getReverseRelations(),
        ]:
            if field_name in fields:
                if field_call not in fields:
                    field_name = field_call
                else:
                    raise DuplicateFieldNameError()
            field = cls._model._meta.get_field(field_name)
            related_model = cls._model._meta.get_field(field_name).related_model
            if related_model == "self":
                related_model = cls._model
            if isinstance(field, GenericForeignKey):
                continue

            if related_model and hasattr(
                related_model,
                "_general_manager_class",
            ):
                related_model = related_model._general_manager_class  # type: ignore

            if related_model is not None:
                fields[f"{field_name}_list"] = {
                    "type": cast(type, related_model),
                    "is_required": False,
                    "is_derived": not bool(field.many_to_many),
                    "is_editable": bool(field.many_to_many and field.editable),
                    "default": None,
                }

        return {
            field_name: {**field, "type": TRANSLATION.get(field["type"], field["type"])}
            for field_name, field in fields.items()
        }

    @classmethod
    def getAttributes(cls) -> dict[str, Callable[[DBBasedInterface], Any]]:
        """
        Builds a mapping of attribute names to accessor callables for a DBBasedInterface instance.

        Includes accessors for custom fields, standard model fields, foreign-key relations, many-to-many relations, and reverse relations. For relations whose related model exposes a _general_manager_class, the accessor yields the corresponding GeneralManager instance (for single relations) or a filtered manager/queryset (for multi-relations); otherwise the accessor yields the related model instance or a queryset directly.

        Returns:
            dict[str, Callable[[DBBasedInterface], Any]]: Mapping from attribute name to a callable that accepts a DBBasedInterface and returns that attribute's value.

        Raises:
            DuplicateFieldNameError: If a generated attribute name conflicts with an existing attribute name.
        """
        from general_manager.manager.generalManager import GeneralManager

        field_values: dict[str, Any] = {}

        field_name_list, to_ignore_list = cls.handleCustomFields(cls._model)
        for field_name in field_name_list:
            field_values[field_name] = lambda self, field_name=field_name: getattr(
                self._instance, field_name
            )

        for field_name in cls.__getModelFields():
            if field_name not in to_ignore_list:
                field_values[field_name] = lambda self, field_name=field_name: getattr(
                    self._instance, field_name
                )

        for field_name in cls.__getForeignKeyFields():
            related_model = cls._model._meta.get_field(field_name).related_model
            if related_model and hasattr(
                related_model,
                "_general_manager_class",
            ):
                generalManagerClass = cast(
                    Type[GeneralManager], related_model._general_manager_class
                )
                field_values[f"{field_name}"] = (
                    lambda self,
                    field_name=field_name,
                    manager_class=generalManagerClass: (
                        manager_class(getattr(self._instance, field_name).pk)
                        if getattr(self._instance, field_name)
                        else None
                    )
                )
            else:
                field_values[f"{field_name}"] = (
                    lambda self, field_name=field_name: getattr(
                        self._instance, field_name
                    )
                )

        for field_name, field_call in [
            *cls.__getManyToManyFields(),
            *cls.__getReverseRelations(),
        ]:
            if field_name in field_values:
                if field_call not in field_values:
                    field_name = field_call
                else:
                    raise DuplicateFieldNameError()
            if hasattr(
                cls._model._meta.get_field(field_name).related_model,
                "_general_manager_class",
            ):
                related_model = cast(
                    Type[models.Model],
                    cls._model._meta.get_field(field_name).related_model,
                )
                related_fields = [
                    f
                    for f in related_model._meta.get_fields()
                    if f.related_model == cls._model
                ]

                field_values[f"{field_name}_list"] = (
                    lambda self,
                    field_name=field_name,
                    related_fields=related_fields: self._instance._meta.get_field(
                        field_name
                    ).related_model._general_manager_class.filter(
                        **{
                            related_field.name: self.pk
                            for related_field in related_fields
                        }
                    )
                )
            else:
                field_values[f"{field_name}_list"] = (
                    lambda self, field_call=field_call: getattr(
                        self._instance, field_call
                    ).all()
                )

        return field_values

    @staticmethod
    def handleCustomFields(
        model: Type[models.Model] | models.Model,
    ) -> tuple[list[str], list[str]]:
        """
        Identify custom fields on a model and related helper fields to ignore.

        Parameters:
            model (type[models.Model] | models.Model): Model class or instance to inspect.

        Returns:
            tuple[list[str], list[str]]: Names of custom fields and associated helper fields to ignore.
        """
        field_name_list: list[str] = []
        to_ignore_list: list[str] = []
        for field_name in DBBasedInterface._getCustomFields(model):
            to_ignore_list.append(f"{field_name}_value")
            to_ignore_list.append(f"{field_name}_unit")
            field_name_list.append(field_name)

        return field_name_list, to_ignore_list

    @staticmethod
    def _getCustomFields(model: Type[models.Model] | models.Model) -> list[str]:
        """
        Return names of fields declared directly on the model class.

        Parameters:
            model (type[models.Model] | models.Model): Model class or instance to inspect.

        Returns:
            list[str]: Field names declared as class attributes.
        """
        return [
            field.name
            for field in model.__dict__.values()
            if isinstance(field, models.Field)
        ]

    @classmethod
    def __getModelFields(cls) -> list[str]:
        """Return names of non-relational fields defined on the model."""
        return [
            field.name
            for field in cls._model._meta.get_fields()
            if not field.many_to_many and not field.related_model
        ]

    @classmethod
    def __getForeignKeyFields(cls) -> list[str]:
        """Return names of foreign-key and one-to-one relations on the model."""
        return [
            field.name
            for field in cls._model._meta.get_fields()
            if field.is_relation and (field.many_to_one or field.one_to_one)
        ]

    @classmethod
    def __getManyToManyFields(cls) -> list[tuple[str, str]]:
        """Return (field_name, accessor_name) tuples for many-to-many fields."""
        return [
            (field.name, field.name)
            for field in cls._model._meta.get_fields()
            if field.is_relation and field.many_to_many
        ]

    @classmethod
    def __getReverseRelations(cls) -> list[tuple[str, str]]:
        """Return (field_name, accessor_name) tuples for reverse one-to-many relations."""
        return [
            (field.name, f"{field.name}_set")
            for field in cls._model._meta.get_fields()
            if field.is_relation and field.one_to_many
        ]

    @staticmethod
    def _preCreate(
        name: generalManagerClassName,
        attrs: attributes,
        interface: interfaceBaseClass,
        base_model_class: type[GeneralManagerBasisModel] = GeneralManagerModel,
    ) -> tuple[attributes, interfaceBaseClass, relatedClass]:
        # Collect fields defined directly on the interface class
        """
        Create a Django model class, a corresponding interface subclass, and a Factory class from an interface definition.

        Parameters:
            name (generalManagerClassName): Name to assign to the generated Django model class.
            attrs (attributes): Attribute dictionary to be updated with the generated Interface and Factory entries.
            interface (interfaceBaseClass): Interface definition used to derive the model and interface subclass.
            base_model_class (type[GeneralManagerBasisModel]): Base class for the generated Django model (defaults to GeneralManagerModel).

        Returns:
            tuple[attributes, interfaceBaseClass, relatedClass]: A tuple containing the updated attributes dictionary, the newly created interface subclass, and the generated Django model class.
        """
        model_fields: dict[str, Any] = {}
        meta_class = None
        for attr_name, attr_value in interface.__dict__.items():
            if not attr_name.startswith("__"):
                if attr_name == "Meta" and isinstance(attr_value, type):
                    # Store the Meta class definition for later use
                    meta_class = attr_value
                elif attr_name == "Factory":
                    # Do not register the factory on the model
                    pass
                else:
                    model_fields[attr_name] = attr_value
        model_fields["__module__"] = attrs.get("__module__")
        # Attach the Meta class or create a default one
        rules: list[Rule] | None = None
        if meta_class:
            model_fields["Meta"] = meta_class

            if hasattr(meta_class, "rules"):
                rules = meta_class.rules
                delattr(meta_class, "rules")

        # Create the concrete Django model dynamically
        model = cast(
            type[GeneralManagerBasisModel],
            type(name, (base_model_class,), model_fields),
        )
        if meta_class and rules:
            model._meta.rules = rules  # type: ignore[attr-defined]
            # add full_clean method
            model.full_clean = getFullCleanMethode(model)  # type: ignore[assignment]
        # Determine interface type
        attrs["_interface_type"] = interface._interface_type
        interface_cls = type(interface.__name__, (interface,), {})
        interface_cls._model = model  # type: ignore[attr-defined]
        attrs["Interface"] = interface_cls

        # Build the associated factory class
        factory_definition = getattr(interface, "Factory", None)
        factory_attributes: dict[str, Any] = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        factory_class = type(f"{name}Factory", (AutoFactory,), factory_attributes)
        # factory_class._meta.model = model
        attrs["Factory"] = factory_class

        return attrs, interface_cls, model

    @staticmethod
    def _postCreate(
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        model: relatedClass,
    ) -> None:
        """
        Finalizes the setup of dynamically created classes by linking the interface and model to the new general manager class.

        This method sets the `_parent_class` attribute on the interface class and attaches the new general manager class to the model via the `_general_manager_class` attribute.

        Parameters:
            new_class (newlyCreatedGeneralManagerClass): Generated GeneralManager subclass.
            interface_class (newlyCreatedInterfaceClass): Concrete interface class created for the model.
            model (relatedClass): Django model linked to the manager.
        """
        interface_class._parent_class = new_class
        model._general_manager_class = new_class  # type: ignore

    @classmethod
    def handleInterface(
        cls,
    ) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Provide hooks invoked before and after dynamic interface class creation.

        Returns:
            tuple[classPreCreationMethod, classPostCreationMethod]: A pair (pre_create, post_create) where `pre_create` is invoked before the manager class is created to allow customization, and `post_create` is invoked after creation to finalize setup.
        """
        return cls._preCreate, cls._postCreate

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        Return the type associated with a given model field name.

        If the field is a relation and its related model has a `_general_manager_class` attribute, that class is returned; otherwise, returns the Django field type.

        Parameters:
            field_name (str): Name of the model field.

        Returns:
            type: Type or GeneralManager class representing the field.
        """
        field = cls._model._meta.get_field(field_name)
        if (
            field.is_relation
            and field.related_model
            and hasattr(field.related_model, "_general_manager_class")
        ):
            return field.related_model._general_manager_class  # type: ignore
        return type(field)
