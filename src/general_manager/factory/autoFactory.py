"""Auto-generating factory utilities for GeneralManager models."""

from __future__ import annotations
from typing import TYPE_CHECKING, Type, Callable, Union, Any, TypeVar, Literal
from django.db import models
from factory.django import DjangoModelFactory
from general_manager.factory.factories import getFieldValue, getManyToManyFieldValue
from django.contrib.contenttypes.fields import GenericForeignKey

if TYPE_CHECKING:
    from general_manager.interface.databaseInterface import (
        DBBasedInterface,
    )

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


class AutoFactory(DjangoModelFactory[modelsModel]):
    """Factory that auto-populates model fields based on interface metadata."""

    interface: Type[DBBasedInterface]
    _adjustmentMethod: (
        Callable[..., Union[dict[str, Any], list[dict[str, Any]]]] | None
    ) = None

    @classmethod
    def _generate(
        cls, strategy: Literal["build", "create"], params: dict[str, Any]
    ) -> models.Model | list[models.Model]:
        """
        Generate and populate model instances using automatically derived field values.

        Parameters:
            strategy (Literal["build", "create"]): Either "build" (unsaved instance) or "create" (saved instance).
            params (dict[str, Any]): Field values supplied by the caller; any missing non-auto fields will be populated automatically.

        Returns:
            models.Model | list[models.Model]: A generated model instance or a list of generated model instances.

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
        field_name_list, to_ignore_list = cls.interface.handleCustomFields(model)

        fields = [
            field
            for field in model._meta.get_fields()
            if field.name not in to_ignore_list
            and not isinstance(field, GenericForeignKey)
        ]
        special_fields: list[models.Field[Any, Any]] = [
            getattr(model, field_name) for field_name in field_name_list
        ]
        pre_declarations = getattr(cls._meta, "pre_declarations", [])
        post_declarations = getattr(cls._meta, "post_declarations", [])
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
            params[field.name] = getFieldValue(field)

        obj: list[models.Model] | models.Model = super()._generate(strategy, params)
        if isinstance(obj, list):
            for item in obj:
                if not isinstance(item, models.Model):
                    raise InvalidGeneratedObjectError()
                cls._handleManyToManyFieldsAfterCreation(item, params)
        else:
            cls._handleManyToManyFieldsAfterCreation(obj, params)
        return obj

    @classmethod
    def _handleManyToManyFieldsAfterCreation(
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
                m2m_values = getManyToManyFieldValue(field)
            if m2m_values:
                getattr(obj, field.name).set(m2m_values)

    @classmethod
    def _adjust_kwargs(cls, **kwargs: Any) -> dict[str, Any]:
        """
        Remove many-to-many keys from kwargs prior to model instantiation.

        Parameters:
            **kwargs (dict[str, Any]): Field values supplied by the caller.

        Returns:
            dict[str, Any]: Keyword arguments with many-to-many entries stripped.
        """
        model: Type[models.Model] = cls._meta.model
        m2m_fields = {field.name for field in model._meta.many_to_many}
        for field_name in m2m_fields:
            kwargs.pop(field_name, None)
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
            return cls.__createWithGenerateFunc(use_creation_method=True, params=kwargs)
        return cls._modelCreation(model_class, **kwargs)

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
            return cls.__createWithGenerateFunc(
                use_creation_method=False, params=kwargs
            )
        return cls._modelBuilding(model_class, **kwargs)

    @classmethod
    def _modelCreation(
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
        obj.save()
        return obj

    @classmethod
    def _modelBuilding(
        cls, model_class: Type[models.Model], **kwargs: Any
    ) -> models.Model:
        """Construct an unsaved model instance with the provided field values."""
        obj = model_class()
        for field, value in kwargs.items():
            setattr(obj, field, value)
        return obj

    @classmethod
    def __createWithGenerateFunc(
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
                return cls._modelCreation(model_cls, **records)
            return cls._modelBuilding(model_cls, **records)

        created_objects: list[models.Model] = []
        for record in records:
            if use_creation_method:
                created_objects.append(cls._modelCreation(model_cls, **record))
            else:
                created_objects.append(cls._modelBuilding(model_cls, **record))
        return created_objects
