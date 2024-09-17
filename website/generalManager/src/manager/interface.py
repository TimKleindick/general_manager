import json
from typing import Type, ClassVar, Any
from django.db import models, transaction
from abc import ABC, abstractmethod
from generalManager.src.manager.meta import GeneralManagerModel
from django.contrib.auth import get_user_model
from simple_history.utils import update_change_reason
from datetime import datetime, timedelta


class InterfaceBase(ABC):
    _parent_class: ClassVar[Type]
    _interface_type: ClassVar[str]

    def __init__(self, pk: Any, *args, **kwargs):
        self.pk = pk

    @abstractmethod
    @classmethod
    def create(cls, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def update(self, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def deactivate(self, **kwargs):
        raise NotImplementedError

    @abstractmethod
    def getData(self, search_date: datetime | None = None):
        raise NotImplementedError

    @abstractmethod
    def getAttributes(self):
        raise NotImplementedError


class DBBasedInterface(InterfaceBase):
    _model: ClassVar[Type[GeneralManagerModel]]

    def __init__(self, pk: Any, search_date: datetime | None = None):
        super().__init__(pk)
        self._instance = self.getData()

    def getData(self, search_date: datetime | None = None) -> GeneralManagerModel:
        model = self._model
        instance = model.objects.get(pk=self.pk)
        if search_date and not search_date > datetime.now() - timedelta(seconds=5):
            instance = self.getHistoricalRecord(instance, search_date)
        return instance

    @classmethod
    def getHistoricalRecord(
        cls, instance: GeneralManagerModel, search_date: datetime | None = None
    ) -> GeneralManagerModel:
        return instance.history.filter(history_date__lte=search_date).last()  # type: ignore

    def getAttributes(self):
        field_values = {}

        for field in [*self.__getModelFields(), *self.__getForeignKeyFields()]:
            field_values[field] = getattr(self._instance, field)

        for field in [*self.__getManyToManyFields(), *self.__getReverseRelations()]:
            field_values[field] = lambda: getattr(self._instance, field).all()

        return field_values

    def __getModelFields(self):
        return [
            field.name
            for field in self._model._meta.get_fields()
            if not field.many_to_many and not field.related_model
        ]

    def __getForeignKeyFields(self):
        return [
            field.name
            for field in self._model._meta.get_fields()
            if field.is_relation and (field.many_to_one or field.one_to_one)
        ]

    def __getManyToManyFields(self):
        return [
            field.name
            for field in self._model._meta.get_fields()
            if field.is_relation and field.many_to_many
        ]

    def __getReverseRelations(self):
        return [
            field.name
            for field in self._model._meta.get_fields()
            if field.is_relation and field.one_to_many
        ]


class ReadOnlyInterface(DBBasedInterface):
    _interface_type = "readonly"

    @classmethod
    def create(cls, **kwargs):
        raise NotImplementedError(
            "Create operation is not allowed in ReadOnlyInterface."
        )

    def update(self, **kwargs):
        raise NotImplementedError(
            "Update operation is not allowed in ReadOnlyInterface."
        )

    def deactivate(self, **kwargs):
        raise NotImplementedError(
            "Deactivate operation is not allowed in ReadOnlyInterface."
        )

    @classmethod
    def sync_data(cls) -> None:
        model: Type[models.Model] | None = getattr(cls, "_model", None)
        parent_class = getattr(cls, "_parent_class", None)
        if model is None or parent_class is None:
            raise ValueError("Attribute '_model' and '_parent_class' must be set.")
        json_data = getattr(parent_class, "_json_data", None)
        if not json_data:
            raise ValueError(
                f"For ReadOnlyInterface '{parent_class.__name__}' must be set '_json_data'"
            )

        # JSON-Daten parsen
        if isinstance(json_data, str):
            data_list = json.loads(json_data)
        elif isinstance(json_data, list):
            data_list = json_data
        else:
            raise ValueError(
                "_json_data must be a JSON string or a list of dictionaries"
            )

        unique_fields = getattr(parent_class, "_unique_fields", [])
        if not unique_fields:
            raise ValueError(
                f"For ReadOnlyInterface '{parent_class.__name__}' must be defined '_unique_fields'"
            )

        with transaction.atomic():
            json_unique_values = set()

            # Daten synchronisieren
            for data in data_list:
                lookup = {field: data[field] for field in unique_fields}
                unique_identifier = tuple(lookup[field] for field in unique_fields)
                json_unique_values.add(unique_identifier)

                instance, created = model.objects.get_or_create(**lookup)
                updated = False
                for field_name, value in data.items():
                    if getattr(instance, field_name, None) != value:
                        setattr(instance, field_name, value)
                        updated = True
                if updated:
                    instance.save()

            # Existierende Einträge abrufen und löschen, wenn nicht im JSON vorhanden
            existing_instances = model.objects.all()
            for instance in existing_instances:
                lookup = {field: getattr(instance, field) for field in unique_fields}
                unique_identifier = tuple(lookup[field] for field in unique_fields)
                if unique_identifier not in json_unique_values:
                    instance.delete()


class DatabaseInterface(DBBasedInterface):
    _interface_type = "database"

    @classmethod
    def create(
        cls, creator_id: int, history_comment: str | None = None, **kwargs
    ) -> int:
        kwargs, many_to_many_kwargs = cls.__sortKwargs(cls._model, kwargs)
        instance = cls._model(**kwargs)
        for key, value in many_to_many_kwargs.items():
            getattr(instance, key).set(value)
        return cls.__save_with_history(instance, creator_id, history_comment)

    def update(
        self, creator_id: int, history_comment: str | None = None, **kwargs
    ) -> int:
        kwargs, many_to_many_kwargs = self.__sortKwargs(self._model, kwargs)
        instance = self._model.objects.get(pk=self.pk)
        for key, value in kwargs.items():
            setattr(instance, key, value)
        for key, value in many_to_many_kwargs.items():
            getattr(instance, key).set(value)
        return self.__save_with_history(instance, creator_id, history_comment)

    def deactivate(self, creator_id: int, history_comment: str | None = None) -> int:
        instance = self._model.objects.get(pk=self.pk)
        instance.active = False
        if history_comment:
            history_comment = f"{history_comment} (deactivated)"
        else:
            history_comment = "Deactivated"
        return self.__save_with_history(instance, creator_id, history_comment)

    @staticmethod
    def __sortKwargs(
        model: Type[models.Model], kwargs: dict
    ) -> tuple[dict, dict[str, list]]:
        many_to_many_fields = model._meta.many_to_many
        many_to_many_kwargs = {}
        for key, value in kwargs.items():
            many_to_many_key = key.split("_id_list")[0]
            if many_to_many_key in many_to_many_fields:
                many_to_many_kwargs[key] = value
                kwargs.pop(key)
        return kwargs, many_to_many_kwargs

    @classmethod
    @transaction.atomic
    def __save_with_history(
        cls, instance: models.Model, creator_id: int, history_comment: str | None
    ) -> int:
        user_model = get_user_model()
        if not user_model.objects.filter(pk=creator_id).exists():
            raise ValueError("User does not exist")
        instance.full_clean()
        instance.save()
        instance.history_user = user_model.objects.get(pk=creator_id)  # type: ignore
        if history_comment:
            update_change_reason(instance, history_comment)
        instance.save()

        return instance.pk
