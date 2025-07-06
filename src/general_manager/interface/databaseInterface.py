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
    _interface_type = "database"

    @classmethod
    def create(
        cls, creator_id: int | None, history_comment: str | None = None, **kwargs: Any
    ) -> int:
        """
        Create a new model instance with the provided attributes and optional history tracking.

        Validates input attributes, separates and sets many-to-many relationships, saves the instance with optional creator and history comment, and returns the primary key of the created instance.

        Parameters:
            creator_id (int | None): The ID of the user creating the instance, or None if not applicable.
            history_comment (str | None): Optional comment to record in the instance's history.

        Returns:
            int: The primary key of the newly created instance.
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
        Update the current model instance with new attribute values and many-to-many relationships, saving changes with optional history tracking.

        Parameters:
            creator_id (int | None): The ID of the user making the update, or None if not specified.
            history_comment (str | None): An optional comment describing the reason for the update.

        Returns:
            int: The primary key of the updated instance.
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
        Deactivate the current model instance by setting its `is_active` flag to `False` and recording the change with an optional history comment.

        Parameters:
            creator_id (int | None): The ID of the user performing the deactivation, or None if not specified.
            history_comment (str | None): An optional comment to include in the instance's history log.

        Returns:
            int: The primary key of the deactivated instance.
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
        Sets many-to-many attributes for the given instance based on the provided kwargs.

        Args:
            instance: The model instance to update.
            many_to_many_kwargs: A dictionary containing many-to-many field names and their corresponding values.

        Returns:
            The updated model instance.
        """
        for key, value in many_to_many_kwargs.items():
            if not value:
                continue
            field_name = key.split("_id_list")[0]
            getattr(instance, field_name).set(value)

        return instance

    @staticmethod
    def __setAttrForWrite(
        instance: GeneralManagerModel,
        kwargs: dict[str, Any],
    ) -> GeneralManagerModel:
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
    def _checkForInvalidKwargs(model: Type[models.Model], kwargs: dict[str, Any]):
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
