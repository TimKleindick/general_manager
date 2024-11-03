from __future__ import annotations
import json
from typing import Type, ClassVar, Any, Callable, TYPE_CHECKING
from django.db import models, transaction
from abc import ABC, abstractmethod
from django.contrib.auth import get_user_model
from simple_history.utils import update_change_reason
from datetime import datetime, timedelta
from simple_history.models import HistoricalRecords
from generalManager.src.manager.bucket import DatabaseBucket, Bucket
from generalManager.src.measurement.measurement import Measurement
from generalManager.src.measurement.measurementField import MeasurementField
from decimal import Decimal
from generalManager.src.factory.factories import AutoFactory
from django.core.exceptions import ValidationError

if TYPE_CHECKING:
    from generalManager.src.manager.generalManager import GeneralManager
    from generalManager.src.manager.meta import GeneralManagerMeta


def getFullCleanMethode(model):
    def full_clean(self, *args, **kwargs):
        errors = {}

        try:
            super(model, self).full_clean(*args, **kwargs)
        except ValidationError as e:
            errors.update(e.message_dict)

        for rule in self._meta.rules:
            if not rule.evaluate(self):
                errors.update(rule.getErrorMessage())

        if errors:
            raise ValidationError(errors)

    return full_clean


class GeneralManagerModel(models.Model):
    _general_manager_class: ClassVar[Type[GeneralManager]]
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
    def getAttributeTypes(self) -> dict[str, type]:
        raise NotImplementedError

    @abstractmethod
    def getAttributes(self) -> dict[str, Any]:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def filter(cls, **kwargs) -> Bucket:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def exclude(cls, **kwargs) -> Bucket:
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def handleInterface(cls) -> tuple[Callable, Callable]:
        """
        This method returns a pre and a post GeneralManager creation method
        and is called inside the GeneralManagerMeta class to initialize the
        Interface.
        The pre creation method is called before the GeneralManager instance
        is created to modify the kwargs.
        The post creation method is called after the GeneralManager instance
        is created to modify the instance and add additional data.
        """
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
        return DatabaseBucket(
            cls._model.objects.filter(**kwargs),
            cls._parent_class,
            cls.__createFilterDefinitions(**kwargs),
        )

    @classmethod
    def exclude(cls, **kwargs):
        return DatabaseBucket(
            cls._model.objects.exclude(**kwargs),
            cls._parent_class,
            cls.__createFilterDefinitions(**kwargs),
        )

    @staticmethod
    def __createFilterDefinitions(**kwargs):
        filter_definitions = {}
        for key, value in kwargs.items():
            filter_definitions[key] = [value]
        return filter_definitions

    @classmethod
    def getHistoricalRecord(
        cls, instance: GeneralManagerModel, search_date: datetime | None = None
    ) -> GeneralManagerModel:
        return instance.history.filter(history_date__lte=search_date).last()  # type: ignore

    @classmethod
    def getAttributeTypes(cls) -> dict[str, type]:
        translation = {
            models.CharField: str,
            models.TextField: str,
            models.BooleanField: bool,
            models.IntegerField: int,
            models.FloatField: float,
            models.DateField: datetime,
            models.DateTimeField: datetime,
            MeasurementField: Measurement,
            models.DecimalField: Decimal,
        }
        fields = {}
        field_name_list, to_ignore_list = cls._handleCustomFields(cls._model)
        for field_name in field_name_list:
            fields[field_name] = type(getattr(cls, field_name))

        for field_name in cls.__getModelFields():
            if field_name not in to_ignore_list:
                fields[field_name] = type(getattr(cls._model, field_name).field)

        for field_name in cls.__getForeignKeyFields():
            if hasattr(
                cls._model._meta.get_field(field_name).related_model,
                "_general_manager_class",
            ):
                fields[field_name] = cls._model._meta.get_field(
                    field_name
                ).related_model._general_manager_class  # type: ignore

        for field_name, field_call in [
            *cls.__getManyToManyFields(),
            *cls.__getReverseRelations(),
        ]:
            if field_name in fields.keys():
                if field_call not in fields.keys():
                    field_name = field_call
                else:
                    raise ValueError("Field name already exists.")
            if hasattr(
                cls._model._meta.get_field(field_name).related_model,
                "_general_manager_class",
            ):
                fields[f"{field_name}_list"] = cls._model._meta.get_field(
                    field_name
                ).related_model._general_manager_class  # type: ignore

        return {
            field_name: translation.get(field, field)
            for field_name, field in fields.items()
        }

    def getAttributes(self):
        field_values = {}

        field_name_list, to_ignore_list = self._handleCustomFields(self._model)
        for field_name in field_name_list:
            field_values[field_name] = getattr(self._instance, field_name)

        for field_name in self.__getModelFields():
            if field_name not in to_ignore_list:
                field_values[field_name] = getattr(self._instance, field_name)

        for field_name in self.__getForeignKeyFields():
            if hasattr(
                self._instance._meta.get_field(field_name).related_model,
                "_general_manager_class",
            ):
                generalManagerClass = self._instance._meta.get_field(
                    field_name
                ).related_model._general_manager_class  # type: ignore
                field_values[f"{field_name}"] = (
                    lambda field_name=field_name: generalManagerClass(
                        getattr(self._instance, field_name).pk
                    )
                )
            else:
                field_values[f"{field_name}"] = lambda field_name=field_name: getattr(
                    self._instance, field_name
                )

        for field_name, field_call in [
            *self.__getManyToManyFields(),
            *self.__getReverseRelations(),
        ]:
            if field_name in field_values.keys():
                if field_call not in field_values.keys():
                    field_name = field_call
                else:
                    raise ValueError("Field name already exists.")
            if hasattr(
                self._instance._meta.get_field(field_name).related_model,
                "_general_manager_class",
            ):
                field_values[f"{field_name}_list"] = (
                    lambda field_name=field_name, field_call=field_call: DatabaseBucket(
                        getattr(self._instance, field_call).all(),
                        self._instance._meta.get_field(
                            field_name
                        ).related_model._general_manager_class,  # type: ignore
                    )
                )
            else:
                field_values[f"{field_name}_list"] = (
                    lambda field_call=field_call: getattr(
                        self._instance, field_call
                    ).all()
                )
        return field_values

    @staticmethod
    def _handleCustomFields(
        model: Type[models.Model] | models.Model,
    ) -> tuple[list, list]:
        field_name_list: list[str] = []
        to_ignore_list: list[str] = []
        for field_name in DBBasedInterface._getCustomFields(model):
            to_ignore_list.append(f"{field_name}_value")
            to_ignore_list.append(f"{field_name}_unit")
            field_name_list.append(field_name)

        return field_name_list, to_ignore_list

    @staticmethod
    def _getCustomFields(model):
        return [
            field.name
            for field in model.__dict__.values()
            if isinstance(field, models.Field)
        ]

    @classmethod
    def __getModelFields(cls):
        return [
            field.name
            for field in cls._model._meta.get_fields()
            if not field.many_to_many and not field.related_model
        ]

    @classmethod
    def __getForeignKeyFields(cls):
        return [
            field.name
            for field in cls._model._meta.get_fields()
            if field.is_relation and (field.many_to_one or field.one_to_one)
        ]

    @classmethod
    def __getManyToManyFields(cls):
        return [
            (field.name, field.name)
            for field in cls._model._meta.get_fields()
            if field.is_relation and field.many_to_many
        ]

    @classmethod
    def __getReverseRelations(cls):
        return [
            (field.name, f"{field.name}_set")
            for field in cls._model._meta.get_fields()
            if field.is_relation and field.one_to_many
        ]

    @staticmethod
    def __preCreate(
        name: str, attrs: dict[str, Any], interface: Type[DBBasedInterface]
    ) -> tuple[dict, Type, Type]:
        # Felder aus der Interface-Klasse sammeln
        model_fields = {}
        meta_class = None
        for attr_name, attr_value in interface.__dict__.items():
            if not attr_name.startswith("__"):
                if attr_name == "Meta" and isinstance(attr_value, type):
                    # Meta-Klasse speichern
                    meta_class = attr_value
                elif attr_name == "Factory":
                    # Factory nicht in model_fields speichern
                    pass
                else:
                    model_fields[attr_name] = attr_value
        model_fields["__module__"] = attrs.get("__module__")
        # Meta-Klasse hinzufügen oder erstellen
        if meta_class:
            rules = None
            model_fields["Meta"] = meta_class

            if hasattr(meta_class, "rules"):
                rules = meta_class.rules
                delattr(meta_class, "rules")

        # Modell erstellen
        model = type(name, (GeneralManagerModel,), model_fields)
        if meta_class and rules:
            model._meta.rules = rules  # type: ignore
            # full_clean Methode hinzufügen
            model.full_clean = getFullCleanMethode(model)
        # Interface-Typ bestimmen
        if issubclass(interface, DBBasedInterface):
            attrs["_interface_type"] = interface._interface_type
            interface_cls = type(interface.__name__, (interface,), {})
            interface_cls._model = model
            attrs["Interface"] = interface_cls
        else:
            raise TypeError("Interface must be a subclass of DBBasedInterface")
        # add factory class
        factory_definition = getattr(interface, "Factory", None)
        factory_attributes = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        factory_class = type(f"{name}Factory", (AutoFactory,), factory_attributes)
        factory_class._meta.model = model
        attrs["Factory"] = factory_class

        return attrs, interface_cls, model

    @staticmethod
    def __postCreate(
        mcs: Type[GeneralManagerMeta],
        new_class: Type[GeneralManager],
        interface_class: Type[DBBasedInterface],
        model: Type[GeneralManagerModel],
    ) -> None:
        interface_class._parent_class = new_class
        model._general_manager_class = new_class

    @classmethod
    def handleInterface(cls) -> tuple[Callable, Callable]:
        """
        This method returns a pre and a post GeneralManager creation method
        and is called inside the GeneralManagerMeta class to initialize the
        Interface.
        The pre creation method is called before the GeneralManager instance
        is created to modify the kwargs.
        The post creation method is called after the GeneralManager instance
        is created to modify the instance and add additional data.
        """
        return cls.__preCreate, cls.__postCreate


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

    @staticmethod
    def readOnlyPostCreate(func):
        def wrapper(
            mcs: Type[GeneralManagerMeta],
            new_class: Type[GeneralManager],
            interface_cls: Type[ReadOnlyInterface],
            model: Type[GeneralManagerModel],
        ):
            func(mcs, new_class, interface_cls, model)
            mcs.read_only_classes.append(interface_cls)

        return wrapper

    @classmethod
    def handleInterface(cls) -> tuple[Callable, Callable]:
        """
        This method returns a pre and a post GeneralManager creation method
        and is called inside the GeneralManagerMeta class to initialize the
        Interface.
        The pre creation method is called before the GeneralManager instance
        is created to modify the kwargs.
        The post creation method is called after the GeneralManager instance
        is created to modify the instance and add additional data.
        """
        return cls.__preCreate, cls.readOnlyPostCreate(cls.__postCreate)


class DatabaseInterface(DBBasedInterface):
    _interface_type = "database"

    @classmethod
    def create(
        cls, creator_id: int, history_comment: str | None = None, **kwargs
    ) -> int:
        cls.__checkForInvalidKwargs(cls._model, kwargs=kwargs)
        kwargs, many_to_many_kwargs = cls.__sortKwargs(cls._model, kwargs)
        instance = cls._model()
        for key, value in kwargs.items():
            setattr(instance, key, value)
        for key, value in many_to_many_kwargs.items():
            getattr(instance, key).set(value)
        return cls.__save_with_history(instance, creator_id, history_comment)

    def update(
        self, creator_id: int, history_comment: str | None = None, **kwargs
    ) -> int:
        self.__checkForInvalidKwargs(self._model, kwargs=kwargs)
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
    def __checkForInvalidKwargs(model: Type[models.Model], kwargs: dict):
        attributes = vars(model)
        fields = model._meta.get_fields()
        for key in kwargs:
            if key not in attributes and key not in fields:
                raise ValueError(f"{key} does not exsist in {model.__name__}")

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
