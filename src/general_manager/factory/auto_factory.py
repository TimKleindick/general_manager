"""Auto-generating factory utilities for GeneralManager models."""

from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Type,
    Callable,
    Union,
    Any,
    TypeVar,
    Literal,
)
from django.core.exceptions import FieldDoesNotExist
from django.db import models
from factory.django import DjangoModelFactory
from general_manager.factory.factories import (
    get_field_value,
    get_many_to_many_field_value,
    _ensure_model_instance,
)
from django.contrib.contenttypes.fields import GenericForeignKey

if TYPE_CHECKING:
    from general_manager.interface.database_based_interface import DBBasedInterface
    from general_manager.manager.general_manager import GeneralManager

modelsModel = TypeVar("modelsModel", bound=models.Model)


class InvalidGeneratedObjectError(TypeError):
    """Raised when factory generation produces non-model instances."""

    def __init__(self) -> None:
        """
        Initialize the exception indicating a generated object is not a Django model instance.

        Sets a default error message explaining that the generated object is not a Django model instance.
        """
        super().__init__("Generated object is not a Django model instance.")


class InvalidAutoFactoryModelError(TypeError):
    """Raised when the factory metadata does not reference a Django model class."""

    def __init__(self) -> None:
        """
        Raised when an AutoFactory target model is not a Django model class.

        The exception carries a default message explaining that `_meta.model` must be a Django model class.
        """
        super().__init__("AutoFactory requires _meta.model to be a Django model class.")


class UndefinedAdjustmentMethodError(ValueError):
    """Raised when an adjustment method is required but not configured."""

    def __init__(self) -> None:
        """
        Initialize the UndefinedAdjustmentMethodError with the default message indicating that a generate/adjustment function is not configured.
        """
        super().__init__("_adjustmentMethod is not defined.")


class MissingManagerClassError(ValueError):
    """Raised when attempting to wrap generated objects without a manager class."""

    def __init__(self) -> None:
        """
        Initialize the error indicating that wrapping requires a manager class on the interface.
        """
        super().__init__("Cannot wrap objects without a manager class.")


class MissingIdentificationFieldError(AttributeError):
    """Raised when a factory cannot resolve an identification field on a generated model instance."""

    def __init__(self, field_name: str, instance: models.Model) -> None:
        """
        Initialize an error indicating an identification field could not be resolved from a model instance.

        Parameters:
            field_name (str): Name of the identification field that could not be resolved.
            instance (models.Model): The Django model instance that lacks the expected attribute.
        """
        super().__init__(
            f"Unable to resolve identification field '{field_name}' from instance {instance!r}"
        )


class AutoFactory(DjangoModelFactory[modelsModel]):
    """Factory that auto-populates model fields based on interface metadata."""

    interface: Type[DBBasedInterface]
    _adjustmentMethod: (
        Callable[..., Union[dict[str, Any], list[dict[str, Any]]]] | None
    ) = None

    @classmethod
    def _generate(
        cls, strategy: Literal["build", "create"], params: dict[str, Any]
    ) -> models.Model | list[models.Model] | "GeneralManager" | list["GeneralManager"]:
        """
        Generate and populate model instances using interface-derived values and declared defaults.

        Parameters:
            strategy (Literal["build", "create"]): "build" returns unsaved model instance(s); "create" returns saved model instance(s).
            params (dict[str, Any]): Field values supplied by the caller; missing non-auto fields will be populated from declared defaults or generated values.

        Returns:
            A Django model instance or a list of Django model instances. If `strategy` is "create", returns a `GeneralManager` instance or a list of `GeneralManager` instances wrapping the created model(s).

        Raises:
            InvalidAutoFactoryModelError: If the factory target `_meta.model` is not a Django model class.
            InvalidGeneratedObjectError: If an element of a generated list is not a Django model instance.
        """
        model = cls._meta.model
        try:
            is_model = isinstance(model, type) and issubclass(model, models.Model)
        except TypeError:
            is_model = False
        if not is_model:
            raise InvalidAutoFactoryModelError
        field_name_list, to_ignore_list = cls.interface.handle_custom_fields(model)

        fields = [
            field
            for field in model._meta.get_fields()
            if field.name not in to_ignore_list
            and not isinstance(field, GenericForeignKey)
        ]
        special_fields: list[models.Field[Any, Any]] = [
            getattr(model, field_name) for field_name in field_name_list
        ]
        pre_declarations = getattr(cls._meta, "pre_declarations", ())
        post_declarations = getattr(cls._meta, "post_declarations", ())
        declared_fields: set[str] = set(pre_declarations) | set(post_declarations)

        field_list: list[models.Field[Any, Any] | models.ForeignObjectRel] = [
            *fields,
            *special_fields,
        ]

        for field in field_list:
            if field.name in [*params, *declared_fields]:
                continue  # Skip fields that are already set
            if isinstance(field, models.AutoField) or field.auto_created:
                continue  # Skip auto fields
            declared_default = cls._get_declared_default(field.name)
            if declared_default is not None:
                params[field.name] = declared_default
                continue
            params[field.name] = get_field_value(field)

        obj: list[models.Model] | models.Model = super()._generate(strategy, params)
        if isinstance(obj, list):
            for item in obj:
                if not isinstance(item, models.Model):
                    raise InvalidGeneratedObjectError()
                cls._handle_many_to_many_fields_after_creation(item, params)
        else:
            cls._handle_many_to_many_fields_after_creation(obj, params)
        if strategy == "create":
            return cls._wrap_generated_objects(obj)
        return obj

    @classmethod
    def _handle_many_to_many_fields_after_creation(
        cls, obj: models.Model, attrs: dict[str, Any]
    ) -> None:
        """
        Assign related objects to many-to-many fields after creation/building.

        Parameters:
            obj (models.Model): Instance whose many-to-many relations should be populated.
            attrs (dict[str, Any]): Original attributes passed to the factory.
        """
        for field in obj._meta.many_to_many:
            if field.name in attrs:
                m2m_values = attrs[field.name]
            else:
                m2m_values = get_many_to_many_field_value(field)
            if m2m_values:
                normalized_values = cls._coerce_many_to_many_values(m2m_values)
                getattr(obj, field.name).set(normalized_values)

    @classmethod
    def _adjust_kwargs(cls, **kwargs: Any) -> dict[str, Any]:
        """
        Strip many-to-many entries from kwargs and coerce single-related values for foreign/one-to-one relation fields.

        Returns:
            dict[str, Any]: Keyword arguments with many-to-many fields removed and relation field values normalized.
        """
        model: Type[models.Model] = cls._meta.model
        m2m_fields = {field.name for field in model._meta.many_to_many}
        for field_name in list(kwargs.keys()):
            if field_name in m2m_fields:
                kwargs.pop(field_name, None)
                continue
            try:
                field = model._meta.get_field(field_name)
            except FieldDoesNotExist:
                continue
            if field.is_relation and (field.many_to_one or field.one_to_one):
                kwargs[field_name] = cls._coerce_single_related_value(
                    kwargs[field_name]
                )
        return kwargs

    @classmethod
    def _create(
        cls, model_class: Type[models.Model], *args: Any, **kwargs: Any
    ) -> models.Model | list[models.Model]:
        """
        Create and save model instance(s), applying adjustment hooks when defined.

        Parameters:
            model_class (type[models.Model]): Django model class to instantiate.
            *args: Unused positional arguments (required by factory_boy).
            **kwargs (dict[str, Any]): Field values supplied by the caller.

        Returns:
            models.Model | list[models.Model]: Saved instance(s).
        """
        kwargs = cls._adjust_kwargs(**kwargs)
        if cls._adjustmentMethod is not None:
            return cls.__create_with_generate_func(
                use_creation_method=True, params=kwargs
            )
        return cls._model_creation(model_class, **kwargs)

    @classmethod
    def _build(
        cls, model_class: Type[models.Model], *args: Any, **kwargs: Any
    ) -> models.Model | list[models.Model]:
        """
        Build (without saving) model instance(s), applying adjustment hooks when defined.

        Parameters:
            model_class (type[models.Model]): Django model class to instantiate.
            *args: Unused positional arguments (required by factory_boy).
            **kwargs (dict[str, Any]): Field values supplied by the caller.

        Returns:
            models.Model | list[models.Model]: Unsaved instance(s).
        """
        kwargs = cls._adjust_kwargs(**kwargs)
        if cls._adjustmentMethod is not None:
            return cls.__create_with_generate_func(
                use_creation_method=False, params=kwargs
            )
        return cls._model_building(model_class, **kwargs)

    @classmethod
    def _model_creation(
        cls, model_class: Type[models.Model], **kwargs: Any
    ) -> models.Model:
        """
        Instantiate, validate, and save a model instance.

        Parameters:
            model_class (type[models.Model]): Model class to instantiate.
            **kwargs (dict[str, Any]): Field assignments applied prior to saving.

        Returns:
            models.Model: Saved instance.
        """
        obj = model_class()
        for field, value in kwargs.items():
            setattr(obj, field, value)
        obj.full_clean()
        database_alias: str | None = None
        if hasattr(cls.interface, "_get_database_alias"):
            database_alias = cls.interface._get_database_alias()

        if database_alias:
            obj.save(using=database_alias)
        else:
            obj.save()
        return obj

    @classmethod
    def _model_building(
        cls, model_class: Type[models.Model], **kwargs: Any
    ) -> models.Model:
        """Construct an unsaved model instance with the provided field values."""
        obj = model_class()
        for field, value in kwargs.items():
            setattr(obj, field, value)
        return obj

    @classmethod
    def __create_with_generate_func(
        cls, use_creation_method: bool, params: dict[str, Any]
    ) -> models.Model | list[models.Model]:
        """
        Create or build model instance(s) using the configured adjustment method.

        Parameters:
            use_creation_method (bool): If True, created records are validated and saved; if False, unsaved instances are returned.
            params (dict[str, Any]): Keyword arguments forwarded to the adjustment method to produce record dict(s).

        Returns:
            models.Model | list[models.Model]: A single model instance or a list of instances — saved instances when `use_creation_method` is True, unsaved otherwise.

        Raises:
            UndefinedAdjustmentMethodError: If no adjustment method has been configured on the factory.
        """
        model_cls = cls._meta.model
        if cls._adjustmentMethod is None:
            raise UndefinedAdjustmentMethodError()
        records = cls._adjustmentMethod(**params)
        if isinstance(records, dict):
            if use_creation_method:
                return cls._model_creation(model_cls, **records)
            return cls._model_building(model_cls, **records)

        created_objects: list[models.Model] = []
        for record in records:
            if use_creation_method:
                created_objects.append(cls._model_creation(model_cls, **record))
            else:
                created_objects.append(cls._model_building(model_cls, **record))
        return created_objects

    @classmethod
    def _wrap_generated_objects(
        cls, generated: models.Model | list[models.Model]
    ) -> "GeneralManager" | list["GeneralManager"]:
        """
        Wrap one or more Django model instances into GeneralManager instances using the interface's parent manager class.

        Returns:
            A `GeneralManager` instance or a list of `GeneralManager` instances corresponding to the provided model instance(s).

        Raises:
            MissingManagerClassError: If the interface does not define a `_parent_class` manager to wrap instances.
        """
        manager_cls = getattr(cls.interface, "_parent_class", None)
        if manager_cls is None:
            raise MissingManagerClassError()

        def _to_manager(instance: models.Model) -> "GeneralManager":
            """
            Wrap a Django model instance into a GeneralManager using the instance's identification.

            Parameters:
                instance (models.Model): The Django model instance whose identification will be extracted.

            Returns:
                GeneralManager: A manager instance constructed with the identification extracted from `instance`.

            Raises:
                MissingIdentificationFieldError: If a required identification field cannot be resolved from `instance`.
            """
            identification = cls._extract_identification(instance)
            return manager_cls(**identification)

        if isinstance(generated, list):
            return [_to_manager(instance) for instance in generated]
        return _to_manager(generated)

    @classmethod
    def _extract_identification(cls, instance: models.Model) -> dict[str, Any]:
        """
        Builds the identification dictionary used to instantiate a manager by reading the interface's input fields from the given model instance.

        Returns:
            dict[str, Any]: A mapping of identification field names to their resolved values, formatted via the interface's `format_identification` method.
        """
        identification: dict[str, Any] = {}
        for name in cls.interface.input_fields.keys():
            value = cls._resolve_identification_value(instance, name)
            identification[name] = value
        return cls.interface.format_identification(dict(identification))

    @staticmethod
    def _resolve_identification_value(instance: models.Model, field_name: str) -> Any:
        """
        Resolve an identification value from a model instance for a given identification field.

        Parameters:
            instance (models.Model): The model instance to extract the value from.
            field_name (str): The identification field name to resolve; if the attribute does not exist,
                the function will attempt to use the corresponding `<field_name>_id` attribute.

        Returns:
            Any: The resolved value. If the resolved value is a Django model, its primary key (`pk`) is returned.

        Raises:
            MissingIdentificationFieldError: If neither `field_name` nor `field_name_id` exist on the instance.
        """
        if hasattr(instance, field_name):
            value = getattr(instance, field_name)
        elif hasattr(instance, f"{field_name}_id"):
            value = getattr(instance, f"{field_name}_id")
        else:
            raise MissingIdentificationFieldError(field_name, instance)

        if isinstance(value, models.Model):
            return getattr(value, "pk", value)
        return value

    @classmethod
    def _get_declared_default(cls, field_name: str) -> Any | None:
        """
        Return the constant default value for field_name declared on the factory class, if present.

        Parameters:
            field_name (str): Field name to look up on the factory class.

        Returns:
            Any | None: The declared value if the class attribute exists and is not callable or a method descriptor (classmethod/staticmethod); otherwise `None`.
        """
        if field_name in cls.__dict__:
            value = cls.__dict__[field_name]
            if not callable(value) and not isinstance(
                value, (classmethod, staticmethod)
            ):
                return value
        return None

    @staticmethod
    def _coerce_single_related_value(value: Any) -> Any:
        """
        Resolve a related value to a Django model instance.

        Parameters:
            value (Any): A related value such as a Django model instance or a GeneralManager wrapper.

        Returns:
            Any: The Django model instance corresponding to the input, or the original value if it cannot be resolved to a model.
        """
        return _ensure_model_instance(value)

    @classmethod
    def _coerce_many_to_many_values(cls, values: Any) -> list[models.Model] | Any:
        """
        Normalize various many-to-many assignment forms into a flat list of related model instances or values.

        Parameters:
            values (Any): A related-value or collection accepted for a many-to-many field — can be a Django Manager, QuerySet, any iterable (list/tuple/set), or a single model/identifier.

        Returns:
            list[models.Model] | list[Any]: A list where each element has been normalized; wrapper/manager-like inputs are converted to their underlying model instances or preserved values when conversion is not applicable.
        """
        if isinstance(values, models.Manager):
            iterable = list(values.all())
        elif isinstance(values, models.QuerySet):
            iterable = list(values)
        elif isinstance(values, (list, tuple, set)):
            iterable = list(values)
        else:
            iterable = [values]
        return [cls._coerce_single_related_value(item) for item in iterable]
