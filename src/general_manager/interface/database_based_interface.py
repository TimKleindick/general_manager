"""Database-backed interface implementation for GeneralManager classes."""

from __future__ import annotations
from typing import Any, Callable, ClassVar, Generic, TYPE_CHECKING, Type, TypeVar, cast
from django.db import models, transaction
from django.db.models import NOT_PROVIDED

from datetime import datetime, date, time, timedelta
from django.utils import timezone
from general_manager.measurement.measurement import Measurement
from general_manager.measurement.measurement_field import MeasurementField
from decimal import Decimal
from general_manager.factory.auto_factory import AutoFactory
from general_manager.interface.base_interface import (
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
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.interface.database_interface_protocols import (
    SupportsActivation,
    SupportsHistory,
)
from general_manager.interface.models import (
    GeneralManagerBasisModel,
    GeneralManagerModel,
    get_full_clean_methode,
)
from django.contrib.contenttypes.fields import GenericForeignKey
from simple_history.utils import update_change_reason  # type: ignore

if TYPE_CHECKING:
    from general_manager.rule.rule import Rule

HistoryModelT = TypeVar("HistoryModelT", bound=models.Model)
WritableModelT = TypeVar("WritableModelT", bound=models.Model)


class InvalidFieldValueError(ValueError):
    """Raised when assigning a value incompatible with the model field."""

    def __init__(self, field_name: str, value: object) -> None:
        """
        Initialize an InvalidFieldValueError for a specific model field and value.

        Parameters:
            field_name (str): Name of the field that received an invalid value.
            value (object): The invalid value provided; included in the exception message.

        """
        super().__init__(f"Invalid value for {field_name}: {value}.")


class InvalidFieldTypeError(TypeError):
    """Raised when assigning a value with an unexpected type."""

    def __init__(self, field_name: str, error: Exception) -> None:
        """
        Initialize the InvalidFieldTypeError with the field name and the originating exception.

        Parameters:
            field_name (str): Name of the model field that received an unexpected type.
            error (Exception): The original exception or error encountered for the field.

        Notes:
            The exception's message is formatted as "Type error for {field_name}: {error}."
        """
        super().__init__(f"Type error for {field_name}: {error}.")


class UnknownFieldError(ValueError):
    """Raised when keyword arguments reference fields not present on the model."""

    def __init__(self, field_name: str, model_name: str) -> None:
        """
        Initialize an UnknownFieldError indicating a field name is not present on a model.

        Parameters:
            field_name (str): The field name that was not found on the model.
            model_name (str): The name of the model in which the field was expected.
        """
        super().__init__(f"{field_name} does not exist in {model_name}.")


class DuplicateFieldNameError(ValueError):
    """Raised when a dynamically generated field name conflicts with an existing one."""

    def __init__(self) -> None:
        """
        Initialize the DuplicateFieldNameError with a default descriptive message.

        This exception indicates a conflict where a dynamically generated field name duplicates an existing name; the default message is "Field name already exists."
        """
        super().__init__("Field name already exists.")


class DBBasedInterface(InterfaceBase, Generic[HistoryModelT]):
    """Interface implementation that persists data using Django ORM models."""

    _model: Type[HistoryModelT]
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    database: ClassVar[str | None] = None

    @classmethod
    def _get_database_alias(cls) -> str | None:
        """
        Return the configured database alias, if any, for ORM operations.
        """
        return getattr(cls, "database", None)

    @classmethod
    def _get_manager(cls) -> models.Manager[HistoryModelT]:
        """
        Return the model manager configured to operate on the selected database.
        """
        manager = cls._model._default_manager
        database_alias = cls._get_database_alias()
        if database_alias:
            manager = manager.db_manager(database_alias)
        return cast(models.Manager[HistoryModelT], manager)

    @classmethod
    def _get_queryset(cls) -> models.QuerySet[HistoryModelT]:
        """
        Return a queryset initialised against the configured database alias.
        """
        return cast(models.QuerySet[HistoryModelT], cls._get_manager().all())

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
        self._instance: HistoryModelT = self.get_data(search_date)

    def get_data(self, search_date: datetime | None = None) -> HistoryModelT:
        """
        Fetch the underlying model instance, optionally as of a historical date.

        Parameters:
            search_date (datetime | None): When provided, retrieve the state closest to this timestamp.

        Returns:
            MODEL_TYPE: Current or historical instance matching the primary key.
        """
        manager = self.__class__._get_manager()
        instance = cast(HistoryModelT, manager.get(pk=self.pk))
        if search_date is not None:
            # Normalize to aware datetime if needed
            if timezone.is_naive(search_date):
                search_date = timezone.make_aware(search_date)
            if search_date <= timezone.now() - timedelta(seconds=5):
                historical = self.get_historical_record(instance, search_date)
                if historical is not None:
                    instance = historical
        return instance

    @staticmethod
    def __parse_kwargs(**kwargs: Any) -> dict[str, Any]:
        """
        Convert keyword arguments into ORM-friendly values.

        Parameters:
            **kwargs (Any): Filter or update arguments potentially containing manager instances.

        Returns:
            dict[str, Any]: Arguments ready to be passed to Django ORM methods.
        """
        from general_manager.manager.general_manager import GeneralManager

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

        kwargs = cls.__parse_kwargs(**kwargs)
        queryset = cls._get_queryset().filter(**kwargs)

        return DatabaseBucket(
            cast(models.QuerySet[models.Model], queryset),
            cls._parent_class,
            cls.__create_filter_definitions(**kwargs),
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
        kwargs = cls.__parse_kwargs(**kwargs)
        queryset = cls._get_queryset().exclude(**kwargs)

        return DatabaseBucket(
            cast(models.QuerySet[models.Model], queryset),
            cls._parent_class,
            cls.__create_filter_definitions(**kwargs),
        )

    @staticmethod
    def __create_filter_definitions(**kwargs: Any) -> dict[str, Any]:
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
    def get_historical_record(
        cls, instance: HistoryModelT, search_date: datetime | None = None
    ) -> HistoryModelT | None:
        """
        Retrieves the most recent historical record of a model instance at or before a specified date.

        Parameters:
            instance (HistoryModelT): Model instance whose history is queried.
            search_date (datetime | None): Cutoff datetime used to select the historical record.

        Returns:
            HistoryModelT | None: Historical instance as of the specified date, if available.
        """
        history_source = cast(SupportsHistory, instance)
        history_manager = history_source.history
        database_alias = cls._get_database_alias()
        if database_alias:
            history_manager = history_manager.using(database_alias)
        historical = history_manager.filter(history_date__lte=search_date).last()
        return cast(HistoryModelT | None, historical)

    @classmethod
    def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
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
        field_name_list, to_ignore_list = cls.handle_custom_fields(cls._model)
        for field_name in field_name_list:
            field = cast(models.Field, getattr(cls._model, field_name))
            fields[field_name] = {
                "type": type(field),
                "is_derived": False,
                "is_required": not field.null,
                "is_editable": field.editable,
                "default": field.default,
            }

        for field_name in cls.__get_model_fields():
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

        for field_name in cls.__get_foreign_key_fields():
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
            *cls.__get_many_to_many_fields(),
            *cls.__get_reverse_relations(),
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
    def get_attributes(cls) -> dict[str, Callable[[DBBasedInterface], Any]]:
        """
        Builds a mapping of attribute names to accessor callables for a DBBasedInterface instance.

        Includes accessors for custom fields, standard model fields, foreign-key relations, many-to-many relations, and reverse relations. For relations whose related model exposes a _general_manager_class, the accessor yields the corresponding GeneralManager instance (for single relations) or a filtered manager/queryset (for multi-relations); otherwise the accessor yields the related model instance or a queryset directly.

        Returns:
            dict[str, Callable[[DBBasedInterface], Any]]: Mapping from attribute name to a callable that accepts a DBBasedInterface and returns that attribute's value.

        Raises:
            DuplicateFieldNameError: If a generated attribute name conflicts with an existing attribute name.
        """
        from general_manager.manager.general_manager import GeneralManager

        field_values: dict[str, Any] = {}

        field_name_list, to_ignore_list = cls.handle_custom_fields(cls._model)
        for field_name in field_name_list:
            field_values[field_name] = lambda self, field_name=field_name: getattr(
                self._instance, field_name
            )

        for field_name in cls.__get_model_fields():
            if field_name not in to_ignore_list:
                field_values[field_name] = lambda self, field_name=field_name: getattr(
                    self._instance, field_name
                )

        for field_name in cls.__get_foreign_key_fields():
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
            *cls.__get_many_to_many_fields(),
            *cls.__get_reverse_relations(),
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
    def handle_custom_fields(
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
        for field_name in DBBasedInterface._get_custom_fields(model):
            to_ignore_list.append(f"{field_name}_value")
            to_ignore_list.append(f"{field_name}_unit")
            field_name_list.append(field_name)

        return field_name_list, to_ignore_list

    @staticmethod
    def _get_custom_fields(model: Type[models.Model] | models.Model) -> list[str]:
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
    def __get_model_fields(cls) -> list[str]:
        """Return names of non-relational fields defined on the model."""
        return [
            field.name
            for field in cls._model._meta.get_fields()
            if not field.many_to_many and not field.related_model
        ]

    @classmethod
    def __get_foreign_key_fields(cls) -> list[str]:
        """Return names of foreign-key and one-to-one relations on the model."""
        return [
            field.name
            for field in cls._model._meta.get_fields()
            if field.is_relation and (field.many_to_one or field.one_to_one)
        ]

    @classmethod
    def __get_many_to_many_fields(cls) -> list[tuple[str, str]]:
        """Return (field_name, accessor_name) tuples for many-to-many fields."""
        return [
            (field.name, field.name)
            for field in cls._model._meta.get_fields()
            if field.is_relation and field.many_to_many
        ]

    @classmethod
    def __get_reverse_relations(cls) -> list[tuple[str, str]]:
        """Return (field_name, accessor_name) tuples for reverse one-to-many relations."""
        return [
            (field.name, f"{field.name}_set")
            for field in cls._model._meta.get_fields()
            if field.is_relation and field.one_to_many
        ]

    @staticmethod
    def _pre_create(
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
            model.full_clean = get_full_clean_methode(model)  # type: ignore[assignment]
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
    def _post_create(
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
    def handle_interface(
        cls,
    ) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Provide hooks invoked before and after dynamic interface class creation.

        Returns:
            tuple[classPreCreationMethod, classPostCreationMethod]: A pair (pre_create, post_create) where `pre_create` is invoked before the manager class is created to allow customization, and `post_create` is invoked after creation to finalize setup.
        """
        return cls._pre_create, cls._post_create

    @classmethod
    def get_field_type(cls, field_name: str) -> type:
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


class MissingActivationSupportError(TypeError):
    """Raised when a model does not provide activation support."""

    def __init__(self, model_name: str) -> None:
        super().__init__(f"{model_name} must define an 'is_active' attribute.")


class WritableDBBasedInterface(DBBasedInterface[WritableModelT]):
    """DB-based interface with write capabilities for models supporting persistence."""

    _model: Type[WritableModelT]
    _interface_type = "database"

    @classmethod
    def create(
        cls, creator_id: int | None, history_comment: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """
        Create a new model instance using the provided field values.

        Parameters:
            creator_id (int | None): ID of the user to record as the change author, or None to leave unset.
            history_comment (str | None): Optional comment to attach to the instance history.
            **kwargs: Field values used to populate the model; many-to-many relations may be provided as `<field>_id_list`.

        Returns:
            dict[str, Any]: Primary key of the newly created instance wrapped in a dict.

        Raises:
            UnknownFieldError: If kwargs contain names that do not correspond to model fields.
        """
        model_cls = cls._model
        cls._check_for_invalid_kwargs(
            cast(Type[models.Model], model_cls), kwargs=kwargs
        )
        kwargs, many_to_many_kwargs = cls._sort_kwargs(
            cast(Type[models.Model], model_cls), kwargs
        )
        instance = cls.__set_attr_for_write(model_cls(), kwargs)
        pk = cls._save_with_history(instance, creator_id, history_comment)
        cls.__set_many_to_many_attributes(instance, many_to_many_kwargs)
        return {"id": pk}

    def update(
        self, creator_id: int | None, history_comment: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """
        Update this instance with the provided field values.

        Parameters:
            creator_id (int | None): ID of the user recording the change; used to set `changed_by_id`.
            history_comment (str | None): Optional comment to attach to the instance's change history.
            **kwargs (Any): Field names and values to apply to the instance; many-to-many updates may be supplied using the `<relation>_id_list` convention.

        Returns:
            dict[str, Any]: Primary key of the updated instance wrapped in a dict.

        Raises:
            UnknownFieldError: If any provided kwarg does not correspond to a model field.
        """
        model_cls = self._model
        self._check_for_invalid_kwargs(
            cast(Type[models.Model], model_cls), kwargs=kwargs
        )
        kwargs, many_to_many_kwargs = self._sort_kwargs(
            cast(Type[models.Model], model_cls), kwargs
        )
        manager = self.__class__._get_manager()
        instance = self.__set_attr_for_write(manager.get(pk=self.pk), kwargs)
        pk = self._save_with_history(instance, creator_id, history_comment)
        self.__set_many_to_many_attributes(instance, many_to_many_kwargs)
        return {"id": pk}

    def deactivate(
        self, creator_id: int | None, history_comment: str | None = None
    ) -> dict[str, Any]:
        """
        Mark the current model instance as inactive and record the change.

        Parameters:
            creator_id (int | None): Identifier of the user performing the action.
            history_comment (str | None): Optional comment stored in the history log.

        Returns:
            dict[str, Any]: Primary key of the deactivated instance wrapped in a dict.
        """
        manager = self.__class__._get_manager()
        instance = manager.get(pk=self.pk)
        if not isinstance(instance, SupportsActivation):
            raise MissingActivationSupportError(instance.__class__.__name__)
        instance.is_active = False
        if history_comment:
            history_comment = f"{history_comment} (deactivated)"
        else:
            history_comment = "Deactivated"
        return {"id": self._save_with_history(instance, creator_id, history_comment)}

    @staticmethod
    def __set_many_to_many_attributes(
        instance: WritableModelT, many_to_many_kwargs: dict[str, list[Any]]
    ) -> WritableModelT:
        """
        Set many-to-many relationship values on the provided instance.

        Parameters:
            instance (WritableModelT): Model instance whose relations are updated.
            many_to_many_kwargs (dict[str, list[Any]]): Mapping of relation names to values.

        Returns:
            WritableModelT: Updated instance.
        """
        from general_manager.manager.general_manager import GeneralManager

        for key, value in many_to_many_kwargs.items():
            if value is None or value is NOT_PROVIDED:
                continue
            field_name = key.removesuffix("_id_list")
            if isinstance(value, list) and all(
                isinstance(v, GeneralManager) for v in value
            ):
                value = [
                    v.identification["id"] if hasattr(v, "identification") else v
                    for v in value
                ]
            getattr(instance, field_name).set(value)

        return instance

    @staticmethod
    def __set_attr_for_write(
        instance: WritableModelT,
        kwargs: dict[str, Any],
    ) -> WritableModelT:
        """
        Populate non-relational fields on an instance and prepare values for writing.

        Converts any GeneralManager value to its `id` and appends `_id` to the attribute name, skips values equal to `NOT_PROVIDED`, sets each attribute on the instance, and translates underlying `ValueError`/`TypeError` from attribute assignment into `InvalidFieldValueError` and `InvalidFieldTypeError` respectively.

        Parameters:
            instance (WritableModelT): The model instance to modify.
            kwargs (dict[str, Any]): Mapping of attribute names to values to apply.

        Returns:
            WritableModelT: The same instance with attributes updated.

        Raises:
            InvalidFieldValueError: If setting an attribute raises a `ValueError`.
            InvalidFieldTypeError: If setting an attribute raises a `TypeError`.
        """
        from general_manager.manager.general_manager import GeneralManager

        for key, value in kwargs.items():
            if isinstance(value, GeneralManager):
                value = value.identification["id"]
                key = f"{key}_id"
            if value is NOT_PROVIDED:
                continue
            try:
                setattr(instance, key, value)
            except ValueError as error:
                raise InvalidFieldValueError(key, value) from error
            except TypeError as error:
                raise InvalidFieldTypeError(key, error) from error
        return instance

    @staticmethod
    def _check_for_invalid_kwargs(
        model: Type[models.Model], kwargs: dict[str, Any]
    ) -> None:
        """
        Validate that each key in `kwargs` corresponds to an attribute or field on `model`.

        Parameters:
            model (type[models.Model]): The Django model class to validate against.
            kwargs (dict[str, Any]): Mapping of keyword names to values; keys ending with `_id_list` are validated after stripping that suffix.

        Raises:
            UnknownFieldError: If any provided key (after removing a trailing `_id_list`) does not match a model attribute or field name.
        """
        attributes = vars(model)
        field_names = {f.name for f in model._meta.get_fields()}
        for key in kwargs:
            temp_key = key.split("_id_list")[0]  # Remove '_id_list' suffix
            if temp_key not in attributes and temp_key not in field_names:
                raise UnknownFieldError(key, model.__name__)

    @staticmethod
    def _sort_kwargs(
        model: Type[models.Model], kwargs: dict[Any, Any]
    ) -> tuple[dict[str, Any], dict[str, list[Any]]]:
        """
        Separate provided kwargs into simple model-field arguments and many-to-many relation arguments.

        This function removes keys targeting many-to-many relations from the input kwargs and returns them separately. A many-to-many key is identified by the suffix "_id_list" whose base name matches a many-to-many field on the given model.

        Parameters:
            model (Type[models.Model]): Django model whose many-to-many field names are inspected.
            kwargs (dict[Any, Any]): Mapping of keyword arguments to partition; keys matching many-to-many relations are removed in-place.

        Returns:
            tuple[dict[str, Any], dict[str, list[Any]]]: A tuple where the first element is the original kwargs dict with many-to-many keys removed, and the second element maps the removed many-to-many keys to their values.
        """
        many_to_many_fields = [field.name for field in model._meta.many_to_many]
        many_to_many_kwargs: dict[Any, Any] = {}
        for key, _value in list(kwargs.items()):
            many_to_many_key = key.split("_id_list")[0]
            if many_to_many_key in many_to_many_fields:
                many_to_many_kwargs[key] = kwargs.pop(key)
        return kwargs, many_to_many_kwargs

    @classmethod
    @transaction.atomic
    def _save_with_history(
        cls,
        instance: WritableModelT,
        creator_id: int | None,
        history_comment: str | None,
    ) -> int:
        """
        Atomically saves a model instance with validation and optional history comment.

        Sets the `changed_by_id` field, validates the instance, applies a history comment if provided, and saves the instance within a database transaction.

        Returns:
            The primary key of the saved instance.
        """
        database_alias = cls._get_database_alias()
        if database_alias:
            instance._state.db = database_alias  # type: ignore[attr-defined]
        try:
            instance.changed_by_id = creator_id  # type: ignore[attr-defined]
        except AttributeError:
            pass
        instance.full_clean()
        if database_alias:
            instance.save(using=database_alias)
        else:
            instance.save()
        if history_comment:
            update_change_reason(instance, history_comment)

        return instance.pk
