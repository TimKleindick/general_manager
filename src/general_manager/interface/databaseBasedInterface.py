from __future__ import annotations
from typing import Type, Any, Callable, TYPE_CHECKING, TypeVar, Generic
from django.db import models
from datetime import datetime, timedelta
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

if TYPE_CHECKING:
    from general_manager.rule.rule import Rule

modelsModel = TypeVar("modelsModel", bound=models.Model)


MODEL_TYPE = TypeVar("MODEL_TYPE", bound=GeneralManagerBasisModel)


class DBBasedInterface(InterfaceBase, Generic[MODEL_TYPE]):
    _model: Type[MODEL_TYPE]
    input_fields: dict[str, Input] = {"id": Input(int)}

    def __init__(
        self,
        *args: list[Any],
        search_date: datetime | None = None,
        **kwargs: dict[str, Any],
    ):
        """
        Initialize the interface and load the associated model instance.

        If `search_date` is provided, loads the historical record as of that date; otherwise, loads the current record.
        """
        super().__init__(*args, **kwargs)
        self.pk = self.identification["id"]
        self._instance = self.getData(search_date)

    def getData(self, search_date: datetime | None = None) -> GeneralManagerBasisModel:
        """
        Retrieves the model instance by primary key, optionally as of a specified historical date.

        If a `search_date` is provided and is not within the last 5 seconds, returns the historical record of the instance as of that date; otherwise, returns the current instance.
        """
        model = self._model
        instance = model.objects.get(pk=self.pk)
        if search_date and not search_date > datetime.now() - timedelta(seconds=5):
            instance = self.getHistoricalRecord(instance, search_date)
        return instance

    @classmethod
    def filter(cls, **kwargs: Any) -> DatabaseBucket:
        """
        Returns a DatabaseBucket containing model instances filtered by the given criteria.

        Args:
            **kwargs: Field lookups to filter the queryset.

        Returns:
            A DatabaseBucket wrapping the filtered queryset and associated metadata.
        """
        return DatabaseBucket(
            cls._model.objects.filter(**kwargs),
            cls._parent_class,
            cls.__createFilterDefinitions(**kwargs),
        )

    @classmethod
    def exclude(cls, **kwargs: Any) -> DatabaseBucket:
        """
        Returns a DatabaseBucket containing model instances that do not match the given filter criteria.

        Args:
            **kwargs: Field lookups to exclude from the queryset.

        Returns:
            A DatabaseBucket wrapping the queryset of excluded model instances.
        """
        return DatabaseBucket(
            cls._model.objects.exclude(**kwargs),
            cls._parent_class,
            cls.__createFilterDefinitions(**kwargs),
        )

    @staticmethod
    def __createFilterDefinitions(**kwargs: Any) -> dict[str, Any]:
        """
        Creates a dictionary of filter definitions from the provided keyword arguments.

        Args:
            **kwargs: Key-value pairs representing filter criteria.

        Returns:
            A dictionary mapping filter keys to their corresponding values.
        """
        filter_definitions: dict[str, Any] = {}
        for key, value in kwargs.items():
            filter_definitions[key] = value
        return filter_definitions

    @classmethod
    def getHistoricalRecord(
        cls, instance: GeneralManagerBasisModel, search_date: datetime | None = None
    ) -> GeneralManagerBasisModel:
        """
        Retrieves the most recent historical record of a model instance at or before a specified date.

        Args:
            instance: The model instance whose history is queried.
            search_date: The cutoff datetime; returns the last record at or before this date.

        Returns:
            The historical model instance as of the specified date, or None if no such record exists.
        """
        return instance.history.filter(history_date__lte=search_date).last()  # type: ignore

    @classmethod
    def getAttributeTypes(cls) -> dict[str, AttributeTypedDict]:
        """
        Return a dictionary mapping attribute names to metadata describing their types and properties.

        The dictionary includes all model fields, custom fields, foreign keys, many-to-many, and reverse relation fields. For each attribute, the metadata specifies its Python type (translated from Django field types when possible), whether it is required, editable, derived, and its default value. For related models with a general manager class, the type is set to that class.

        Returns:
            dict[str, AttributeTypedDict]: Mapping of attribute names to their type information and metadata.
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
            field: models.Field = getattr(cls._model, field_name)
            fields[field_name] = {
                "type": type(field),
                "is_derived": False,
                "is_required": not field.null,
                "is_editable": field.editable,
                "default": field.default,
            }

        for field_name in cls.__getModelFields():
            if field_name not in to_ignore_list:
                field: models.Field = getattr(cls._model, field_name).field
                fields[field_name] = {
                    "type": type(field),
                    "is_derived": False,
                    "is_required": not field.null,
                    "is_editable": field.editable,
                    "default": field.default,
                }

        for field_name in cls.__getForeignKeyFields():
            field = cls._model._meta.get_field(field_name)
            related_model = field.related_model
            if related_model and hasattr(
                related_model,
                "_general_manager_class",
            ):
                related_model = related_model._general_manager_class

            elif related_model is not None:
                fields[field_name] = {
                    "type": related_model,
                    "is_derived": False,
                    "is_required": not field.null,
                    "is_editable": field.editable,
                    "default": field.default,
                }

        for field_name, field_call in [
            *cls.__getManyToManyFields(),
            *cls.__getReverseRelations(),
        ]:
            if field_name in fields:
                if field_call not in fields:
                    field_name = field_call
                else:
                    raise ValueError("Field name already exists.")
            field = cls._model._meta.get_field(field_name)
            related_model = cls._model._meta.get_field(field_name).related_model
            if related_model and hasattr(
                related_model,
                "_general_manager_class",
            ):
                related_model = related_model._general_manager_class
            if related_model is not None:
                fields[f"{field_name}_list"] = {
                    "type": related_model,
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
        Returns a mapping of attribute names to callables that retrieve their values from a DBBasedInterface instance.

        The returned dictionary includes accessors for custom fields, model fields, foreign keys (optionally returning related interface instances), many-to-many relations, and reverse relations. For related models that have a general manager class, the accessor returns an instance of that class; otherwise, it returns the related object or queryset directly. Raises a ValueError if a field name conflict occurs.
        """
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
                generalManagerClass = related_model._general_manager_class
                field_values[f"{field_name}"] = (
                    lambda self, field_name=field_name, manager_class=generalManagerClass: manager_class(
                        getattr(self._instance, field_name).pk
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
                    raise ValueError("Field name already exists.")
            if hasattr(
                cls._model._meta.get_field(field_name).related_model,
                "_general_manager_class",
            ):
                field_values[
                    f"{field_name}_list"
                ] = lambda self, field_name=field_name: self._instance._meta.get_field(
                    field_name
                ).related_model._general_manager_class.filter(
                    **{self._instance.__class__.__name__.lower(): self.pk}
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
        Identifies custom fields on a model and their associated utils fields to ignore.

        Returns:
            A tuple containing a list of custom field names and a list of related field names
            (typically suffixed with '_value' and '_unit') that should be ignored.
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
        Returns a list of custom field names defined directly on the model class.

        Args:
            model: The Django model class or instance to inspect.

        Returns:
            A list of field names that are defined as class attributes on the model, not via Django's meta system.
        """
        return [
            field.name
            for field in model.__dict__.values()
            if isinstance(field, models.Field)
        ]

    @classmethod
    def __getModelFields(cls):
        """
        Returns a list of model field names that are not many-to-many or related fields.

        Excludes fields representing many-to-many relationships and related models.
        """
        return [
            field.name
            for field in cls._model._meta.get_fields()
            if not field.many_to_many and not field.related_model
        ]

    @classmethod
    def __getForeignKeyFields(cls):
        """
        Returns a list of field names for all foreign key and one-to-one relations on the model.
        """
        return [
            field.name
            for field in cls._model._meta.get_fields()
            if field.is_relation and (field.many_to_one or field.one_to_one)
        ]

    @classmethod
    def __getManyToManyFields(cls):
        """
        Returns a list of tuples containing the names of all many-to-many fields on the model.

        Each tuple consists of the field name repeated twice.
        """
        return [
            (field.name, field.name)
            for field in cls._model._meta.get_fields()
            if field.is_relation and field.many_to_many
        ]

    @classmethod
    def __getReverseRelations(cls):
        """
        Returns a list of tuples representing reverse one-to-many relations for the model.

        Each tuple contains the related field's name and its default related accessor name.
        """
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
        base_model_class=GeneralManagerModel,
    ) -> tuple[attributes, interfaceBaseClass, relatedClass]:
        # Felder aus der Interface-Klasse sammeln
        """
        Dynamically generates a Django model class, its associated interface class, and a factory class from an interface definition.

        This method collects fields and metadata from the provided interface class, creates a new Django model inheriting from the specified base model class, attaches custom validation rules if present, and constructs corresponding interface and factory classes. The updated attributes dictionary, the new interface class, and the newly created model class are returned for integration into the general manager framework.

        Parameters:
            name: The name for the dynamically created model class.
            attrs: The attributes dictionary to be updated with the new interface and factory classes.
            interface: The interface base class defining the model structure and metadata.
            base_model_class: The base class to use for the new model (defaults to GeneralManagerModel).

        Returns:
            tuple: A tuple containing the updated attributes dictionary, the new interface class, and the newly created model class.
        """
        model_fields: dict[str, Any] = {}
        meta_class = None
        for attr_name, attr_value in interface.__dict__.items():
            if not attr_name.startswith("__"):
                if attr_name == "Meta" and isinstance(attr_value, type):
                    # Meta-Klasse speichern
                    meta_class = attr_value
                elif attr_name == "Factory":
                    # Factory nicht in model_fields speichern
                    pass
                else:
                    model_fields[attr_name] = attr_value
        model_fields["__module__"] = attrs.get("__module__")
        # Meta-Klasse hinzufügen oder erstellen
        rules: list[Rule] | None = None
        if meta_class:
            model_fields["Meta"] = meta_class

            if hasattr(meta_class, "rules"):
                rules = getattr(meta_class, "rules")
                delattr(meta_class, "rules")

        # Modell erstellen
        model = type(name, (base_model_class,), model_fields)
        if meta_class and rules:
            setattr(model._meta, "rules", rules)
            # full_clean Methode hinzufügen
            model.full_clean = getFullCleanMethode(model)
        # Interface-Typ bestimmen
        attrs["_interface_type"] = interface._interface_type
        interface_cls = type(interface.__name__, (interface,), {})
        setattr(interface_cls, "_model", model)
        attrs["Interface"] = interface_cls

        # add factory class
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
        """
        interface_class._parent_class = new_class
        setattr(model, "_general_manager_class", new_class)

    @classmethod
    def handleInterface(
        cls,
    ) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Returns the pre- and post-creation hooks for initializing the interface.

        The pre-creation method is called before the GeneralManager class is created to allow customization, while the post-creation method is called after creation to finalize setup.
        """
        return cls._preCreate, cls._postCreate

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        Returns the type associated with the specified field name.

        If the field is a relation and its related model defines a `_general_manager_class`, that class is returned; otherwise, returns the Django field type.
        """
        field = cls._model._meta.get_field(field_name)
        if field.is_relation and field.related_model:
            return field.related_model._general_manager_class
        return type(field)
