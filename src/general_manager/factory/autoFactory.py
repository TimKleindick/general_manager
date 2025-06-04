from __future__ import annotations
from typing import TYPE_CHECKING, Type, Callable, Union, Any, TypeVar, Literal
from django.db import models
from factory.django import DjangoModelFactory
from general_manager.factory.factories import getFieldValue, getManyToManyFieldValue


if TYPE_CHECKING:
    from general_manager.interface.databaseInterface import (
        DBBasedInterface,
    )

modelsModel = TypeVar("modelsModel", bound=models.Model)


class AutoFactory(DjangoModelFactory[modelsModel]):
    """
    A factory class that automatically generates values for model fields,
    including handling of unique fields and constraints.
    """

    interface: Type[DBBasedInterface]
    _adjustmentMethod: (
        Callable[..., Union[dict[str, Any], list[dict[str, Any]]]] | None
    ) = None

    @classmethod
    def _generate(
        cls, strategy: Literal["build", "create"], params: dict[str, Any]
    ) -> models.Model | list[models.Model]:
        """
        Generates and populates a Django model instance or list of instances with automatic field value assignment.
        
        Automatically assigns values to unset model fields, including handling custom and special fields, and processes many-to-many relationships after instance creation or building. Raises a ValueError if the model is not a subclass of Django's Model.
        
        Args:
            strategy: Specifies whether to build (unsaved) or create (saved) the instance(s).
            params: Field values to use for instance generation; missing fields are auto-filled.
        
        Returns:
            A single model instance or a list of model instances, depending on the input parameters and strategy.
        """
        cls._original_params = params
        model = cls._meta.model
        if not issubclass(model, models.Model):
            raise ValueError("Model must be a type")
        field_name_list, to_ignore_list = cls.interface.handleCustomFields(model)

        fields = [
            field
            for field in model._meta.get_fields()
            if field.name not in to_ignore_list
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
            for item in obj:  # type: ignore
                if not isinstance(item, models.Model):
                    raise ValueError("Model must be a type")
                cls._handleManyToManyFieldsAfterCreation(item, params)
        else:
            cls._handleManyToManyFieldsAfterCreation(obj, params)
        return obj

    @classmethod
    def _handleManyToManyFieldsAfterCreation(
        cls, obj: models.Model, attrs: dict[str, Any]
    ) -> None:
        """
        Sets many-to-many field values on a Django model instance after creation.
        
        For each many-to-many field, assigns related objects from the provided attributes if available; otherwise, generates values using `getManyToManyFieldValue`. The related objects are set using the Django ORM's `set` method.
        """
        for field in obj._meta.many_to_many:
            if field.name in attrs:
                m2m_values = attrs[field.name]
            else:
                m2m_values = getManyToManyFieldValue(field)
            if m2m_values:
                getattr(obj, field.name).set(m2m_values)

    @classmethod
    def _adjust_kwargs(cls, **kwargs: dict[str, Any]) -> dict[str, Any]:
        # Remove ManyToMany fields from kwargs before object creation
        """
        Removes many-to-many field entries from keyword arguments before model instance creation.
        
        Returns:
            The keyword arguments dictionary with many-to-many fields removed.
        """
        model: Type[models.Model] = cls._meta.model
        m2m_fields = {field.name for field in model._meta.many_to_many}
        for field_name in m2m_fields:
            kwargs.pop(field_name, None)
        return kwargs

    @classmethod
    def _create(
        cls, model_class: Type[models.Model], *args: list[Any], **kwargs: dict[str, Any]
    ) -> models.Model | list[models.Model]:
        """
        Creates and saves a Django model instance or instances, applying optional adjustment logic.
        
        If an adjustment method is defined, it is used to generate or modify field values before creation. Otherwise, the model is instantiated and saved with the provided attributes.
        
        Args:
            model_class: The Django model class to instantiate.
        
        Returns:
            A saved model instance or a list of instances.
        """
        kwargs = cls._adjust_kwargs(**kwargs)
        if cls._adjustmentMethod is not None:
            return cls.__createWithGenerateFunc(use_creation_method=True, params=kwargs)
        return cls._modelCreation(model_class, **kwargs)

    @classmethod
    def _build(
        cls, model_class: Type[models.Model], *args: list[Any], **kwargs: dict[str, Any]
    ) -> models.Model | list[models.Model]:
        """
        Constructs an unsaved instance or list of instances of the given Django model class.
        
        If an adjustment method is defined, it is used to generate or modify field values before building the instance(s). Many-to-many fields are removed from the keyword arguments prior to instantiation.
        """
        kwargs = cls._adjust_kwargs(**kwargs)
        if cls._adjustmentMethod is not None:
            return cls.__createWithGenerateFunc(
                use_creation_method=False, params=kwargs
            )
        return cls._modelBuilding(model_class, **kwargs)

    @classmethod
    def _modelCreation(
        cls, model_class: Type[models.Model], **kwargs: dict[str, Any]
    ) -> models.Model:
        """
        Creates and saves a Django model instance with the provided field values.
        
        Initializes the model, assigns attributes from keyword arguments, validates the instance using `full_clean()`, and saves it to the database.
        
        Returns:
            The saved Django model instance.
        """
        obj = model_class()
        for field, value in kwargs.items():
            setattr(obj, field, value)
        obj.full_clean()
        obj.save()
        return obj

    @classmethod
    def _modelBuilding(
        cls, model_class: Type[models.Model], **kwargs: dict[str, Any]
    ) -> models.Model:
        """
        Constructs an unsaved instance of the specified Django model with provided field values.
        
        Args:
            model_class: The Django model class to instantiate.
            **kwargs: Field values to set on the model instance.
        
        Returns:
            An unsaved Django model instance with attributes set from kwargs.
        """
        obj = model_class()
        for field, value in kwargs.items():
            setattr(obj, field, value)
        return obj

    @classmethod
    def __createWithGenerateFunc(
        cls, use_creation_method: bool, params: dict[str, Any]
    ) -> models.Model | list[models.Model]:
        """
        Generates one or more model instances using the adjustment method for field values.
        
        If the adjustment method returns a single dictionary, creates or builds a single model instance.
        If it returns a list of dictionaries, creates or builds multiple instances accordingly.
        
        Args:
            use_creation_method: If True, saves instances to the database; otherwise, builds unsaved instances.
            params: Parameters to pass to the adjustment method for generating field values.
        
        Returns:
            A single model instance or a list of model instances, depending on the adjustment method's output.
        
        Raises:
            ValueError: If the adjustment method is not defined.
        """
        model_cls = cls._meta.model
        if cls._adjustmentMethod is None:
            raise ValueError("generate_func is not defined")
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
