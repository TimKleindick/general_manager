import json
from typing import Type, ClassVar, Any
from django.db import models, transaction
from abc import ABC, abstractmethod
from django.contrib.auth import get_user_model
from simple_history.utils import update_change_reason
from datetime import datetime, timedelta
from simple_history.models import HistoricalRecords
from generalManager.src.manager.bucket import DatabaseBucket


class GeneralManagerModel(models.Model):
    is_active = models.BooleanField(default=True)
    changed_by = models.ForeignKey(get_user_model(), on_delete=models.PROTECT)
    history = HistoricalRecords(inherit=True)

    @property
    def _history_user(self):
        return self.changed_by

    @_history_user.setter
    def _history_user(self, value):
        self.changed_by = value

    class Meta:
        abstract = True


class InterfaceBase(ABC):
    _parent_class: ClassVar[Type]
    _interface_type: ClassVar[str]

    def __init__(self, pk: Any, *args, **kwargs):
        self.pk = pk

    @classmethod
    @abstractmethod
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

    @classmethod
    @abstractmethod
    def filter(cls, **kwargs):
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def exclude(cls, **kwargs):
        raise NotImplementedError


class DBBasedInterface(InterfaceBase):
    _model: ClassVar[Type[GeneralManagerModel]]

    def __init__(self, pk: Any, search_date: datetime | None = None):
        super().__init__(pk)
        self._instance = self.getData(search_date)

    def getData(self, search_date: datetime | None = None) -> GeneralManagerModel:
        model = self._model
        instance = model.objects.get(pk=self.pk)
        if search_date and not search_date > datetime.now() - timedelta(seconds=5):
            instance = self.getHistoricalRecord(instance, search_date)
        return instance

    @classmethod
    def filter(cls, **kwargs):
        return DatabaseBucket(cls._model.objects.filter(**kwargs), cls._parent_class)

    @classmethod
    def exclude(cls, **kwargs):
        return DatabaseBucket(cls._model.objects.exclude(**kwargs), cls._parent_class)

    @classmethod
    def getHistoricalRecord(
        cls, instance: GeneralManagerModel, search_date: datetime | None = None
    ) -> GeneralManagerModel:
        return instance.history.filter(history_date__lte=search_date).last()  # type: ignore

    def getAttributes(self):
        field_values = {}
        to_ignore_list = []
        for field in self.__getCustomFields():
            field_values[field] = getattr(self._instance, field)
            to_ignore_list.append(f"{field}_value")
            to_ignore_list.append(f"{field}_unit")

        for field in [*self.__getModelFields(), *self.__getForeignKeyFields()]:
            if field not in to_ignore_list:
                field_values[field] = getattr(self._instance, field)

        for field in [*self.__getManyToManyFields(), *self.__getReverseRelations()]:
            field_values[field] = lambda: getattr(self._instance, field).all()

        return field_values

    def __getCustomFields(self):
        return [
            field.name
            for field in self._model.__dict__.values()
            if isinstance(field, models.Field)
        ]

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
        instance.is_active = False
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
        cls, instance: GeneralManagerModel, creator_id: int, history_comment: str | None
    ) -> int:
        instance.changed_by_id = creator_id  # type: ignore
        instance.full_clean()
        if history_comment:
            update_change_reason(instance, history_comment)
        instance.save()

        return instance.pk
