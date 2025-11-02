"""Database-backed interface implementation for GeneralManager classes."""

from __future__ import annotations
import warnings
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
    SoftDeleteGeneralManagerModel,
    SoftDeleteMixin,
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
        Create an InvalidFieldTypeError that records the field name and the originating exception.

        Parameters:
            field_name (str): Name of the model field that received a value of an unexpected type.
            error (Exception): The original exception encountered for the field.
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
    _active_manager: ClassVar[models.Manager[models.Model] | None] = None

    @classmethod
    def _get_database_alias(cls) -> str | None:
        """
        Get the configured database alias for ORM operations.

        Returns:
            The database alias string, or `None` if no alias is configured.
        """
        return getattr(cls, "database", None)

    @classmethod
    def _get_manager(cls, *, only_active: bool = True) -> models.Manager[HistoryModelT]:
        """
        Get the model manager for the interface's model, bound to the configured database alias if one is set.

        Returns:
            manager (django.db.models.Manager[HistoryModelT]): The model manager for the interface's model, using the configured database alias when provided.
        """
        if getattr(cls, "_use_soft_delete", False):
            if not only_active and hasattr(cls._model, "all_objects"):
                manager = cast(models.Manager[HistoryModelT], cls._model.all_objects)  # type: ignore[attr-defined]
            elif only_active and not hasattr(cls._model, "all_objects"):
                cached_manager = cast(
                    models.Manager[HistoryModelT] | None,
                    getattr(cls, "_active_manager", None),
                )
                if cached_manager is None:
                    base_manager = cls._model._default_manager

                    class _FilteredManager(models.Manager[HistoryModelT]):  # type: ignore[misc]
                        def get_queryset(self_inner) -> models.QuerySet[HistoryModelT]:
                            queryset = base_manager.get_queryset()
                            if self_inner._db:  # type: ignore[attr-defined]
                                queryset = queryset.using(self_inner._db)  # type: ignore[attr-defined]
                            return queryset.filter(is_active=True)

                    filtered_manager: models.Manager[HistoryModelT] = _FilteredManager()
                    filtered_manager.model = cls._model  # type: ignore[attr-defined]
                    cls._active_manager = filtered_manager  # type: ignore[attr-defined]
                    manager = filtered_manager
                else:
                    manager = cached_manager
            else:
                manager = cls._model._default_manager
        else:
            manager = cls._model._default_manager
        database_alias = cls._get_database_alias()
        if database_alias:
            manager = manager.db_manager(database_alias)
        return cast(models.Manager[HistoryModelT], manager)

    @classmethod
    def _get_queryset(cls) -> models.QuerySet[HistoryModelT]:
        """
        Get a queryset for the interface's model using the configured database alias.

        Returns:
            A Django QuerySet of the interface's model (models.QuerySet[HistoryModelT]) bound to the configured database alias.
        """
        manager = cls._get_manager(only_active=True)
        queryset: models.QuerySet[HistoryModelT] = manager.all()  # type: ignore[assignment]
        if getattr(cls, "_use_soft_delete", False) and not hasattr(
            cls._model, "all_objects"
        ):
            queryset = queryset.filter(is_active=True)
        return cast(models.QuerySet[HistoryModelT], queryset)

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
        Return the model instance backing this interface; if `search_date` is provided, return the most recent historical record at or before that timestamp.

        Parameters:
            search_date (datetime | None): Timestamp for retrieving historical state. Naive datetimes will be converted to timezone-aware before lookup.

        Returns:
            HistoryModelT: The current model instance, or the historical instance at or before `search_date` if one is found.
        """
        manager = self.__class__._get_manager(only_active=True)
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
        Create a DatabaseBucket containing model instances that match the given Django-style filter expressions.

        Parameters:
            kwargs (Any): Django-style filter lookups to apply to the queryset.

        Returns:
            DatabaseBucket: Bucket wrapping the filtered queryset.
        """

        include_inactive = kwargs.pop("include_inactive", False)
        kwargs = cls.__parse_kwargs(**kwargs)
        queryset_base = cls._get_queryset()
        if include_inactive:
            queryset_base = cls._get_manager(only_active=False).all()
        queryset = queryset_base.filter(**kwargs)

        return DatabaseBucket(
            cast(models.QuerySet[models.Model], queryset),
            cls._parent_class,
            cls.__create_filter_definitions(**kwargs),
        )

    @classmethod
    def exclude(cls, **kwargs: Any) -> DatabaseBucket:
        """
        Exclude model instances matching the provided Django-style lookup expressions and wrap the resulting queryset in a DatabaseBucket.

        Parameters:
            **kwargs (Any): Django-style exclusion lookup expressions (field lookups and query expressions).

        Returns:
            DatabaseBucket: Bucket wrapping the queryset after applying the exclusions.
        """
        include_inactive = kwargs.pop("include_inactive", False)
        kwargs = cls.__parse_kwargs(**kwargs)
        queryset_base = cls._get_queryset()
        if include_inactive:
            queryset_base = cls._get_manager(only_active=False).all()
        queryset = queryset_base.exclude(**kwargs)

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
        if not isinstance(instance, SupportsHistory):
            return None

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
        Build a mapping of the model's exposed attribute names to their type metadata for this interface.

        Includes direct model fields, custom fields, foreign-key relations, many-to-many relations, and reverse one-to-many relations. Related models that expose a `_general_manager_class` use that class as the attribute type. Many-to-many and reverse relation attributes are exposed with a "_list" suffix. GenericForeignKey fields are omitted.

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
        Generate a concrete Django model class, a corresponding Interface subclass, and an AutoFactory from an Interface definition.

        Parameters:
                name (str): Name for the generated Django model class.
                attrs (dict): Attribute dictionary to be updated with the generated Interface and Factory entries.
                interface (type): Interface base class used to derive the model and Interface subclass.
                base_model_class (type): Base Django model class to inherit from for the generated model (defaults to GeneralManagerModel).

        Returns:
                tuple: (updated_attrs, interface_cls, model) where `updated_attrs` is the input attrs dict updated with "Interface" and "Factory" keys, `interface_cls` is the created Interface subclass, and `model` is the generated Django model class.
        """
        model_fields: dict[str, Any] = {}
        meta_class = None
        use_soft_delete = False
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
            if hasattr(meta_class, "use_soft_delete"):
                use_soft_delete = bool(meta_class.use_soft_delete)
                delattr(meta_class, "use_soft_delete")
            model_fields["Meta"] = meta_class
            if hasattr(meta_class, "rules"):
                rules = meta_class.rules
                delattr(meta_class, "rules")

        # Create the concrete Django model dynamically
        if use_soft_delete:
            if (
                base_model_class is GeneralManagerModel
                or base_model_class is GeneralManagerBasisModel
            ) and issubclass(SoftDeleteGeneralManagerModel, base_model_class):
                base_classes: tuple[type[GeneralManagerBasisModel], ...] = (
                    SoftDeleteGeneralManagerModel,
                )
            elif issubclass(base_model_class, SoftDeleteMixin):
                base_classes = (base_model_class,)
            else:
                base_classes = (SoftDeleteMixin, base_model_class)  # type: ignore
        else:
            base_classes = (base_model_class,)

        model = cast(
            type[GeneralManagerBasisModel],
            type(name, base_classes, model_fields),
        )
        if meta_class and rules:
            model._meta.rules = rules  # type: ignore[attr-defined]
            # add full_clean method
            model.full_clean = get_full_clean_methode(model)  # type: ignore[assignment]
        if meta_class and use_soft_delete:
            model._meta.use_soft_delete = use_soft_delete  # type: ignore[attr-defined]
        # Determine interface type
        attrs["_interface_type"] = interface._interface_type
        interface_cls = type(interface.__name__, (interface,), {})
        interface_cls._model = model  # type: ignore[attr-defined]
        interface_cls._use_soft_delete = use_soft_delete  # type: ignore[attr-defined]
        attrs["Interface"] = interface_cls

        # Build the associated factory class
        manager_factory = cast(type | None, attrs.pop("Factory", None))
        factory_definition = manager_factory or getattr(interface, "Factory", None)
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
        try:
            new_class.objects = interface_class._get_manager()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        if getattr(interface_class, "_use_soft_delete", False) and hasattr(
            model, "all_objects"
        ):
            new_class.all_objects = interface_class._get_manager(  # type: ignore[attr-defined]
                only_active=False
            )

    @classmethod
    def handle_interface(
        cls,
    ) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Return the pre- and post-creation hooks used for dynamic interface class creation.

        Returns:
            tuple: A pair (pre_create, post_create) where `pre_create` is called before the manager class is created to prepare attributes and configuration, and `post_create` is called after creation to finalize wiring and attach managers.
        """
        return cls._pre_create, cls._post_create

    @classmethod
    def get_field_type(cls, field_name: str) -> type:
        """
        Determine the class used to represent a model field for this interface.

        If the field is a relation and the related model exposes a `_general_manager_class`,
        that manager class is returned; otherwise the Django field class is returned.

        Parameters:
            field_name (str): Name of the model field to inspect.

        Returns:
            type: The related model's GeneralManager class when present, otherwise the Django field class.
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
        """
        Initialize the exception indicating a model lacks activation support.

        Parameters:
            model_name (str): The name of the model missing an `is_active` attribute. The exception message will include this name.
        """
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
        manager = self.__class__._get_manager(only_active=False)
        instance = self.__set_attr_for_write(manager.get(pk=self.pk), kwargs)
        pk = self._save_with_history(instance, creator_id, history_comment)
        self.__set_many_to_many_attributes(instance, many_to_many_kwargs)
        return {"id": pk}

    def delete(
        self, creator_id: int | None, history_comment: str | None = None
    ) -> dict[str, Any]:
        """
        Delete the current model instance, performing either a soft or hard delete.

        Parameters:
            creator_id (int | None): Identifier of the user performing the action, or None.
            history_comment (str | None): Optional comment to attach to the history entry.

        Returns:
            dict[str, Any]: Dictionary containing the key `"id"` with the primary key of the deleted instance.
        """
        manager = self.__class__._get_manager(only_active=False)
        instance = manager.get(pk=self.pk)
        if getattr(self.__class__, "_use_soft_delete", False):
            if not isinstance(instance, SupportsActivation):
                raise MissingActivationSupportError(instance.__class__.__name__)
            instance.is_active = False
            history_comment = (
                f"{history_comment} (deactivated)" if history_comment else "Deactivated"
            )
            return {
                "id": self._save_with_history(instance, creator_id, history_comment)
            }

        history_comment = f"{history_comment} (deleted)" or "Deleted"
        try:
            instance.changed_by_id = creator_id  # type: ignore[attr-defined]
        except AttributeError:
            pass
        update_change_reason(instance, history_comment)
        database_alias = self.__class__._get_database_alias()
        atomic_context = (
            transaction.atomic(using=database_alias)
            if database_alias
            else transaction.atomic()
        )
        with atomic_context:
            if database_alias:
                instance.delete(using=database_alias)
            else:
                instance.delete()
        return {"id": self.pk}

    def deactivate(
        self, creator_id: int | None, history_comment: str | None = None
    ) -> dict[str, Any]:
        """
        Deprecated compatibility wrapper for `delete`.
        """
        warnings.warn(
            "deactivate() is deprecated; use delete() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.delete(creator_id=creator_id, history_comment=history_comment)

    @staticmethod
    def __set_many_to_many_attributes(
        instance: WritableModelT, many_to_many_kwargs: dict[str, list[Any]]
    ) -> WritableModelT:
        """
        Apply many-to-many relation values to a model instance.

        Keys in many_to_many_kwargs are expected to end with the suffix "_id_list"; the suffix is removed to obtain the relation attribute name. Values that are lists of GeneralManager instances are converted to their underlying ids (uses identification["id"] when present). Entries with value None or NOT_PROVIDED are ignored. Each relation is applied via the relation manager's set() method.

        Parameters:
            instance (WritableModelT): The model instance to update.
            many_to_many_kwargs (dict[str, list[Any]]): Mapping from relation keys (with "_id_list" suffix) to lists of related ids or GeneralManager instances.

        Returns:
            WritableModelT: The same instance with updated many-to-many relations.
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
        Set non-relational writable fields on an instance, converting manager references to primary keys.

        Converts any GeneralManager value to its underlying `id` and uses `<field>_id` as the attribute name, skips values equal to `NOT_PROVIDED`, assigns each remaining value on the given instance, and translates assignment `ValueError`/`TypeError` into `InvalidFieldValueError` and `InvalidFieldTypeError`.

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
    def _save_with_history(
        cls,
        instance: WritableModelT,
        creator_id: int | None,
        history_comment: str | None,
    ) -> int:
        """
        Save a model instance with validation, optional changed_by assignment, and an optional history comment.

        Parameters:
            instance (WritableModelT): The model instance to validate and persist.
            creator_id (int | None): ID to assign to the instance's `changed_by_id` attribute if present.
            history_comment (str | None): Optional change reason to attach to the instance's history.

        Returns:
            int: The primary key of the saved instance.
        """
        database_alias = cls._get_database_alias()
        if database_alias:
            instance._state.db = database_alias  # type: ignore[attr-defined]
        atomic_context = (
            transaction.atomic(using=database_alias)
            if database_alias
            else transaction.atomic()
        )
        with atomic_context:
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
