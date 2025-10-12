"""Concrete interface providing CRUD operations via Django ORM."""

from __future__ import annotations
from typing import (
    Type,
    Any,
)
from django.db import models, transaction
from simple_history.utils import update_change_reason  # type: ignore
from general_manager.interface.databaseBasedInterface import (
    DBBasedInterface,
    GeneralManagerModel,
)
from django.db.models import NOT_PROVIDED


class DatabaseInterface(DBBasedInterface[GeneralManagerModel]):
    """CRUD-capable interface backed by a concrete Django model."""

    _interface_type = "database"

    @classmethod
    def create(
        cls, creator_id: int | None, history_comment: str | None = None, **kwargs: Any
    ) -> int:
        """
        Create a new model instance and return its primary key.

        Parameters:
            creator_id (int | None): Identifier of the user creating the instance.
            history_comment (str | None): Optional comment stored in the history log.
            **kwargs (Any): Field values used to populate the model.

        Returns:
            int: Primary key of the newly created instance.

        Raises:
            ValueError: If unknown fields are supplied.
            ValidationError: Propagated when model validation fails.
        """
        cls._checkForInvalidKwargs(cls._model, kwargs=kwargs)
        kwargs, many_to_many_kwargs = cls._sortKwargs(cls._model, kwargs)
        instance = cls.__setAttrForWrite(cls._model(), kwargs)
        pk = cls._save_with_history(instance, creator_id, history_comment)
        cls.__setManyToManyAttributes(instance, many_to_many_kwargs)
        return pk

    def update(
        self, creator_id: int | None, history_comment: str | None = None, **kwargs: Any
    ) -> int:
        """
        Update the current model instance and return its primary key.

        Parameters:
            creator_id (int | None): Identifier of the user performing the update.
            history_comment (str | None): Optional comment stored in the history log.
            **kwargs (Any): Field updates applied to the model.

        Returns:
            int: Primary key of the updated instance.

        Raises:
            ValueError: If unknown fields are supplied.
            ValidationError: Propagated when model validation fails.
        """
        self._checkForInvalidKwargs(self._model, kwargs=kwargs)
        kwargs, many_to_many_kwargs = self._sortKwargs(self._model, kwargs)
        instance = self.__setAttrForWrite(self._model.objects.get(pk=self.pk), kwargs)
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
        instance = self._model.objects.get(pk=self.pk)
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
        Populate non-relational fields on the instance before saving.

        Parameters:
            instance (GeneralManagerModel): Model instance that will receive the values.
            kwargs (dict[str, Any]): Key-value pairs to assign to the instance.

        Returns:
            GeneralManagerModel: Instance with updated attributes.
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
            except ValueError as e:
                raise ValueError(f"Invalid value for {key}: {value}") from e
            except TypeError as e:
                raise TypeError(f"Type error for {key}: {e}") from e
        return instance

    @staticmethod
    def _checkForInvalidKwargs(
        model: Type[models.Model], kwargs: dict[str, Any]
    ) -> None:
        """
        Ensure provided keyword arguments map to known fields or attributes.

        Parameters:
            model (type[models.Model]): Django model being validated.
            kwargs (dict[str, Any]): Keyword arguments supplied by the caller.

        Raises:
            ValueError: If an unknown field name is encountered.
        """
        attributes = vars(model)
        field_names = {f.name for f in model._meta.get_fields()}
        for key in kwargs:
            temp_key = key.split("_id_list")[0]  # Remove '_id_list' suffix
            if temp_key not in attributes and temp_key not in field_names:
                raise ValueError(f"{key} does not exist in {model.__name__}")

    @staticmethod
    def _sortKwargs(
        model: Type[models.Model], kwargs: dict[Any, Any]
    ) -> tuple[dict[str, Any], dict[str, list[Any]]]:
        """
        Split keyword arguments into simple fields and many-to-many relations.

        Parameters:
            model (type[models.Model]): Model whose relation metadata is inspected.
            kwargs (dict[Any, Any]): Keyword arguments supplied by the caller.

        Returns:
            tuple[dict[str, Any], dict[str, list[Any]]]: Tuple containing simple-field kwargs and many-to-many kwargs.
        """
        many_to_many_fields = [field.name for field in model._meta.many_to_many]
        many_to_many_kwargs: dict[Any, Any] = {}
        for key, value in list(kwargs.items()):
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
