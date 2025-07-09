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
        Generates and populates one or more Django model instances with automatic field value assignment.
        
        Automatically fills unset model fields, excluding generic foreign keys and auto-created fields, and handles custom and special fields as defined by the interface. After instance creation or building, processes many-to-many relationships. Raises a ValueError if the model is not a subclass of Django's Model.
        
        Parameters:
            strategy (Literal["build", "create"]): Determines whether to build (unsaved) or create (saved) the instance(s).
            params (dict[str, Any]): Field values to use for instance generation; missing fields are auto-filled.
        
        Returns:
            models.Model or list[models.Model]: The generated model instance or list of instances.
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
        Assigns related objects to all many-to-many fields of a Django model instance after it has been created.
        
        For each many-to-many field, sets the related objects from the provided attributes if available; otherwise, generates related objects automatically. Uses the Django ORM's `set` method to establish the relationships.
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
        Removes many-to-many fields from the provided keyword arguments before creating or building a model instance.
        
        Returns:
            dict[str, Any]: The keyword arguments with any many-to-many fields excluded.
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
        Create and save a Django model instance or multiple instances, optionally applying custom adjustment logic to field values.
        
        If an adjustment method is defined, it is used to generate or modify field values before instance creation. Otherwise, the model is instantiated and saved with the provided attributes.
        
        Returns:
            A saved model instance or a list of saved instances.
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
        Builds an unsaved instance or list of instances of the specified Django model class.
        
        If an adjustment method is defined, it is used to generate or modify field values before building. Many-to-many fields are excluded from the keyword arguments prior to instantiation.
        
        Returns:
            models.Model or list[models.Model]: The unsaved model instance or list of instances.
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
        Create, validate, and save a Django model instance with the specified field values.
        
        Initializes the model, assigns attributes from keyword arguments, performs validation with `full_clean()`, and saves the instance to the database.
        
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
        Constructs an unsaved Django model instance with the specified field values.
        
        Parameters:
            model_class (Type[models.Model]): The Django model class to instantiate.
            **kwargs: Field values to assign to the model instance.
        
        Returns:
            models.Model: An unsaved instance of the specified model with attributes set from kwargs.
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
        Creates or builds one or more model instances using the adjustment method to generate field values.
        
        If the adjustment method returns a single dictionary, a single instance is created or built. If it returns a list of dictionaries, multiple instances are created or built accordingly.
        
        Parameters:
            use_creation_method (bool): If True, instances are saved to the database; if False, instances are built but not saved.
            params (dict[str, Any]): Arguments passed to the adjustment method for generating field values.
        
        Returns:
            models.Model or list[models.Model]: The created or built model instance(s).
        
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
