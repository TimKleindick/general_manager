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


class DatabaseInterface(DBBasedInterface):
    _interface_type = "database"

    @classmethod
    def create(
        cls, creator_id: int, history_comment: str | None = None, **kwargs: Any
    ) -> int:
        from general_manager.manager.generalManager import GeneralManager

        cls.__checkForInvalidKwargs(cls._model, kwargs=kwargs)
        kwargs, many_to_many_kwargs = cls.__sortKwargs(cls._model, kwargs)
        instance = cls._model()
        for key, value in kwargs.items():
            if isinstance(value, GeneralManager):
                value = value.identification["id"]
                key = f"{key}_id"
            setattr(instance, key, value)
        for key, value in many_to_many_kwargs.items():
            getattr(instance, key).set(value)
        return cls.__save_with_history(instance, creator_id, history_comment)

    def update(
        self, creator_id: int, history_comment: str | None = None, **kwargs: Any
    ) -> int:
        from general_manager.manager.generalManager import GeneralManager

        self.__checkForInvalidKwargs(self._model, kwargs=kwargs)
        kwargs, many_to_many_kwargs = self.__sortKwargs(self._model, kwargs)
        instance = self._model.objects.get(pk=self.pk)
        for key, value in kwargs.items():
            if isinstance(value, GeneralManager):
                value = value.identification["id"]
                key = f"{key}_id"
            setattr(instance, key, value)
        for key, value in many_to_many_kwargs.items():
            getattr(instance, key).set(value)
        return self.__save_with_history(instance, creator_id, history_comment)

    def deactivate(self, creator_id: int, history_comment: str | None = None) -> int:
        instance = self._model.objects.get(pk=self.pk)
        instance.is_active = False
        if history_comment:
            history_comment = f"{history_comment} (deactivated)"
        else:
            history_comment = "Deactivated"
        return self.__save_with_history(instance, creator_id, history_comment)

    @staticmethod
    def __checkForInvalidKwargs(model: Type[models.Model], kwargs: dict[Any, Any]):
        attributes = vars(model)
        fields = model._meta.get_fields()
        for key in kwargs:
            if key not in attributes and key not in fields:
                raise ValueError(f"{key} does not exsist in {model.__name__}")

    @staticmethod
    def __sortKwargs(
        model: Type[models.Model], kwargs: dict[Any, Any]
    ) -> tuple[dict[str, Any], dict[str, list[Any]]]:
        many_to_many_fields = model._meta.many_to_many
        many_to_many_kwargs: dict[Any, Any] = {}
        for key, value in kwargs.items():
            many_to_many_key = key.split("_id_list")[0]
            if many_to_many_key in many_to_many_fields:
                many_to_many_kwargs[key] = value
                kwargs.pop(key)
        return kwargs, many_to_many_kwargs

    @classmethod
    @transaction.atomic
    def __save_with_history(
        cls, instance: GeneralManagerModel, creator_id: int, history_comment: str | None
    ) -> int:
        """
        Saves a model instance with validation and optional history tracking.

        Sets the `changed_by_id` field, validates the instance, applies a history comment if provided, and saves the instance within an atomic transaction.

        Args:
            instance: The model instance to save.
            creator_id: The ID of the user making the change.
            history_comment: Optional comment describing the reason for the change.

        Returns:
            The primary key of the saved instance.
        """
        instance.changed_by_id = creator_id
        instance.full_clean()
        if history_comment:
            update_change_reason(instance, history_comment)
        instance.save()

        return instance.pk
