"""Concrete interface providing CRUD operations via Django ORM."""

from __future__ import annotations
from typing import (
    Any,
    Type,
    cast,
)
from django.db import models, transaction
from simple_history.utils import update_change_reason  # type: ignore
from general_manager.interface.databaseBasedInterface import (
    DBBasedInterface,
    GeneralManagerModel,
)
from django.db.models import NOT_PROVIDED


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


class DatabaseInterface(DBBasedInterface[GeneralManagerModel]):
    """CRUD-capable interface backed by a concrete Django model."""

    _interface_type = "database"

    @classmethod
    def create(
        cls, creator_id: int | None, history_comment: str | None = None, **kwargs: Any
    ) -> int:
        """
        Create a new model instance using the provided field values.

        Parameters:
            creator_id (int | None): ID of the user to record as the change author, or None to leave unset.
            history_comment (str | None): Optional comment to attach to the instance history.
            **kwargs: Field values used to populate the model; many-to-many relations may be provided as `<field>_id_list`.

        Returns:
            int: Primary key of the newly created instance.

        Raises:
            UnknownFieldError: If kwargs contain names that do not correspond to model fields.
            ValidationError: If model validation fails during save.
        """
        model_cls = cast(type[GeneralManagerModel], cls._model)
        cls._checkForInvalidKwargs(model_cls, kwargs=kwargs)
        kwargs, many_to_many_kwargs = cls._sortKwargs(model_cls, kwargs)
        instance = cls.__setAttrForWrite(model_cls(), kwargs)
        pk = cls._save_with_history(instance, creator_id, history_comment)
        cls.__setManyToManyAttributes(instance, many_to_many_kwargs)
        return pk

    def update(
        self, creator_id: int | None, history_comment: str | None = None, **kwargs: Any
    ) -> int:
        """
        Update this instance with the provided field values.

        Parameters:
            creator_id (int | None): ID of the user recording the change; used to set `changed_by_id`.
            history_comment (str | None): Optional comment to attach to the instance's change history.
            **kwargs (Any): Field names and values to apply to the instance; many-to-many updates may be supplied using the `<relation>_id_list` convention.

        Returns:
            int: Primary key of the updated instance.

        Raises:
            UnknownFieldError: If any provided kwarg does not correspond to a model field.
            ValidationError: If model validation fails during save.
        """
        model_cls = cast(type[GeneralManagerModel], self._model)
        self._checkForInvalidKwargs(model_cls, kwargs=kwargs)
        kwargs, many_to_many_kwargs = self._sortKwargs(model_cls, kwargs)
        instance = self.__setAttrForWrite(model_cls.objects.get(pk=self.pk), kwargs)
        pk = self._save_with_history(instance, creator_id, history_comment)
        self.__setManyToManyAttributes(instance, many_to_many_kwargs)
        return pk

    def deactivate(
        self, creator_id: int | None, history_comment: str | None = None
    ) -> int:
        """
        Mark the current model instance as inactive and record the change.

        Parameters:
            creator_id (int | None): Identifier of the user performing the action.
            history_comment (str | None): Optional comment stored in the history log.

        Returns:
            int: Primary key of the deactivated instance.
        """
        model_cls = cast(type[GeneralManagerModel], self._model)
        instance = model_cls.objects.get(pk=self.pk)
        instance.is_active = False
        if history_comment:
            history_comment = f"{history_comment} (deactivated)"
        else:
            history_comment = "Deactivated"
        return self._save_with_history(instance, creator_id, history_comment)

    @staticmethod
    def __setManyToManyAttributes(
        instance: GeneralManagerModel, many_to_many_kwargs: dict[str, list[Any]]
    ) -> GeneralManagerModel:
        """
        Set many-to-many relationship values on the provided instance.

        Parameters:
            instance (GeneralManagerModel): Model instance whose relations are updated.
            many_to_many_kwargs (dict[str, list[Any]]): Mapping of relation names to values.

        Returns:
            GeneralManagerModel: Updated instance.
        """
        from general_manager.manager.generalManager import GeneralManager

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
    def __setAttrForWrite(
        instance: GeneralManagerModel,
        kwargs: dict[str, Any],
    ) -> GeneralManagerModel:
        """
        Populate non-relational fields on an instance and prepare values for writing.

        Converts any GeneralManager value to its `id` and appends `_id` to the attribute name, skips values equal to `NOT_PROVIDED`, sets each attribute on the instance, and translates underlying `ValueError`/`TypeError` from attribute assignment into `InvalidFieldValueError` and `InvalidFieldTypeError` respectively.

        Parameters:
            instance (GeneralManagerModel): The model instance to modify.
            kwargs (dict[str, Any]): Mapping of attribute names to values to apply.

        Returns:
            GeneralManagerModel: The same instance with attributes updated.

        Raises:
            InvalidFieldValueError: If setting an attribute raises a `ValueError`.
            InvalidFieldTypeError: If setting an attribute raises a `TypeError`.
        """
        from general_manager.manager.generalManager import GeneralManager

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
    def _checkForInvalidKwargs(
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
    def _sortKwargs(
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
        instance: GeneralManagerModel,
        creator_id: int | None,
        history_comment: str | None,
    ) -> int:
        """
        Atomically saves a model instance with validation and optional history comment.

        Sets the `changed_by_id` field, validates the instance, applies a history comment if provided, and saves the instance within a database transaction.

        Returns:
            The primary key of the saved instance.
        """
        instance.changed_by_id = creator_id
        instance.full_clean()
        instance.save()
        if history_comment:
            update_change_reason(instance, history_comment)

        return instance.pk
