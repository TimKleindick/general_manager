from __future__ import annotations
import json
from typing import (
    Type,
    ClassVar,
    Any,
    Callable,
    TYPE_CHECKING,
    Generator,
    TypeVar,
    cast,
)
from django.db import models, transaction
from django.contrib.auth import get_user_model
from simple_history.utils import update_change_reason  # type: ignore
from datetime import datetime, timedelta
from simple_history.models import HistoricalRecords  # type: ignore
from general_manager.measurement.measurement import Measurement
from general_manager.measurement.measurementField import MeasurementField
from decimal import Decimal
from general_manager.factory.factories import AutoFactory
from django.core.exceptions import ValidationError
from general_manager.interface.baseInterface import (
    InterfaceBase,
    Bucket,
    classPostCreationMethod,
    classPreCreationMethod,
    generalManagerClassName,
    attributes,
    interfaceBaseClass,
    newlyCreatedGeneralManagerClass,
    newlyCreatedInterfaceClass,
    relatedClass,
    GeneralManagerType,
    AttributeTypedDict,
)
from general_manager.manager.input import Input

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta
    from django.contrib.auth.models import AbstractUser
    from general_manager.rule.rule import Rule

modelsModel = TypeVar("modelsModel", bound=models.Model)


def getFullCleanMethode(model: Type[models.Model]) -> Callable[..., None]:
    def full_clean(self: models.Model, *args: Any, **kwargs: Any):
        errors: dict[str, Any] = {}
        try:
            super(model, self).full_clean(*args, **kwargs)  # type: ignore
        except ValidationError as e:
            errors.update(e.message_dict)

        rules: list[Rule] = getattr(self._meta, "rules")
        for rule in rules:
            if not rule.evaluate(self):
                error_message = rule.getErrorMessage()
                if error_message:
                    errors.update(error_message)

        if errors:
            raise ValidationError(errors)

    return full_clean


class GeneralManagerModel(models.Model):
    _general_manager_class: ClassVar[Type[GeneralManager]]
    is_active = models.BooleanField(default=True)
    changed_by = models.ForeignKey(get_user_model(), on_delete=models.PROTECT)
    changed_by_id: int
    history = HistoricalRecords(inherit=True)

    @property
    def _history_user(self) -> AbstractUser:
        return self.changed_by

    @_history_user.setter
    def _history_user(self, value: AbstractUser) -> None:
        self.changed_by = value

    class Meta:
        abstract = True


class DBBasedInterface(InterfaceBase):
    _model: ClassVar[Type[GeneralManagerModel]]
    input_fields: dict[str, Input] = {"id": Input(int)}

    def __init__(
        self,
        *args: list[Any],
        search_date: datetime | None = None,
        **kwargs: dict[str, Any],
    ):
        super().__init__(*args, **kwargs)
        self.pk = self.identification["id"]
        self._instance = self.getData(search_date)

    def getData(self, search_date: datetime | None = None) -> GeneralManagerModel:
        model = self._model
        instance = model.objects.get(pk=self.pk)
        if search_date and not search_date > datetime.now() - timedelta(seconds=5):
            instance = self.getHistoricalRecord(instance, search_date)
        return instance

    @classmethod
    def filter(cls, **kwargs: Any) -> DatabaseBucket:
        return DatabaseBucket(
            cls._model.objects.filter(**kwargs),
            cls._parent_class,
            cls.__createFilterDefinitions(**kwargs),
        )

    @classmethod
    def exclude(cls, **kwargs: Any) -> DatabaseBucket:
        return DatabaseBucket(
            cls._model.objects.exclude(**kwargs),
            cls._parent_class,
            cls.__createFilterDefinitions(**kwargs),
        )

    @staticmethod
    def __createFilterDefinitions(**kwargs: Any) -> dict[str, Any]:
        filter_definitions: dict[str, Any] = {}
        for key, value in kwargs.items():
            filter_definitions[key] = value
        return filter_definitions

    @classmethod
    def getHistoricalRecord(
        cls, instance: GeneralManagerModel, search_date: datetime | None = None
    ) -> GeneralManagerModel:
        return instance.history.filter(history_date__lte=search_date).last()  # type: ignore

    @classmethod
    def getAttributeTypes(cls) -> dict[str, AttributeTypedDict]:
        TRANSLATION: dict[Type[models.Field[Any, Any]], type] = {
            models.fields.BigAutoField: int,
            models.AutoField: int,
            models.CharField: str,
            models.TextField: str,
            models.BooleanField: bool,
            models.IntegerField: int,
            models.FloatField: float,
            models.DateField: datetime,
            models.DateTimeField: datetime,
            MeasurementField: Measurement,
            models.DecimalField: Decimal,
            models.EmailField: str,
            models.FileField: str,
            models.ImageField: str,
            models.URLField: str,
            models.TimeField: datetime,
        }
        fields: dict[str, AttributeTypedDict] = {}
        field_name_list, to_ignore_list = cls.handleCustomFields(cls._model)
        for field_name in field_name_list:
            field: models.Field = getattr(cls._model, field_name)
            fields[field_name] = {
                "type": type(field),
                "is_required": not field.null,
                "is_editable": field.editable,
                "default": field.default,
            }

        for field_name in cls.__getModelFields():
            if field_name not in to_ignore_list:
                field: models.Field = getattr(cls._model, field_name).field
                fields[field_name] = {
                    "type": type(field),
                    "is_required": not field.null,
                    "is_editable": field.editable,
                    "default": field.default,
                }

        for field_name in cls.__getForeignKeyFields():
            field = cls._model._meta.get_field(field_name)
            related_model = field.related_model
            if related_model and hasattr(
                related_model,
                "_general_manager_class",
            ):
                fields[field_name] = {
                    "type": related_model._general_manager_class,
                    "is_required": not field.null,
                    "is_editable": field.editable,
                    "default": field.default,
                }
            elif related_model is not None:
                fields[field_name] = {
                    "type": related_model,
                    "is_required": not field.null,
                    "is_editable": field.editable,
                    "default": field.default,
                }

        for field_name, field_call in [
            *cls.__getManyToManyFields(),
            *cls.__getReverseRelations(),
        ]:
            if field_name in fields.keys():
                if field_call not in fields.keys():
                    field_name = field_call
                else:
                    raise ValueError("Field name already exists.")
            related_model = cls._model._meta.get_field(field_name).related_model
            if related_model and hasattr(
                related_model,
                "_general_manager_class",
            ):
                fields[f"{field_name}_list"] = {
                    "type": related_model._general_manager_class,
                    "is_required": False,
                    "is_editable": False,
                    "default": None,
                }
            elif related_model is not None:
                fields[f"{field_name}_list"] = {
                    "type": related_model,
                    "is_required": False,
                    "is_editable": False,
                    "default": None,
                }

        return {
            field_name: {**field, "type": TRANSLATION.get(field["type"], field["type"])}
            for field_name, field in fields.items()
        }

    @classmethod
    def getAttributes(cls) -> dict[str, Any]:
        field_values: dict[str, Any] = {}

        field_name_list, to_ignore_list = cls.handleCustomFields(cls._model)
        for field_name in field_name_list:
            field_values[field_name] = lambda self, field_name=field_name: getattr(
                self._instance, field_name
            )

        for field_name in cls.__getModelFields():
            if field_name not in to_ignore_list:
                field_values[field_name] = lambda self, field_name=field_name: getattr(
                    self._instance, field_name
                )

        for field_name in cls.__getForeignKeyFields():
            related_model = cls._model._meta.get_field(field_name).related_model
            if related_model and hasattr(
                related_model,
                "_general_manager_class",
            ):
                generalManagerClass = related_model._general_manager_class
                field_values[f"{field_name}"] = (
                    lambda self, field_name=field_name: generalManagerClass(
                        getattr(self._instance, field_name).pk
                    )
                )
            else:
                field_values[f"{field_name}"] = (
                    lambda self, field_name=field_name: getattr(
                        self._instance, field_name
                    )
                )

        for field_name, field_call in [
            *cls.__getManyToManyFields(),
            *cls.__getReverseRelations(),
        ]:
            if field_name in field_values.keys():
                if field_call not in field_values.keys():
                    field_name = field_call
                else:
                    raise ValueError("Field name already exists.")
            if hasattr(
                cls._model._meta.get_field(field_name).related_model,
                "_general_manager_class",
            ):
                field_values[
                    f"{field_name}_list"
                ] = lambda self, field_name=field_name: self._instance._meta.get_field(
                    field_name
                ).related_model._general_manager_class.filter(
                    **{self._instance.__class__.__name__.lower(): self.pk}
                )
            else:
                field_values[f"{field_name}_list"] = (
                    lambda self, field_call=field_call: getattr(
                        self._instance, field_call
                    ).all()
                )
        return field_values

    @staticmethod
    def handleCustomFields(
        model: Type[models.Model] | models.Model,
    ) -> tuple[list[str], list[str]]:
        field_name_list: list[str] = []
        to_ignore_list: list[str] = []
        for field_name in DBBasedInterface._getCustomFields(model):
            to_ignore_list.append(f"{field_name}_value")
            to_ignore_list.append(f"{field_name}_unit")
            field_name_list.append(field_name)

        return field_name_list, to_ignore_list

    @staticmethod
    def _getCustomFields(model: Type[models.Model] | models.Model) -> list[str]:
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
    def _preCreate(
        name: generalManagerClassName, attrs: attributes, interface: interfaceBaseClass
    ) -> tuple[attributes, interfaceBaseClass, relatedClass]:
        # Felder aus der Interface-Klasse sammeln
        model_fields: dict[str, Any] = {}
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
        rules: list[Rule] | None = None
        if meta_class:
            model_fields["Meta"] = meta_class

            if hasattr(meta_class, "rules"):
                rules = getattr(meta_class, "rules")
                delattr(meta_class, "rules")

        # Modell erstellen
        model = type(name, (GeneralManagerModel,), model_fields)
        if meta_class and rules:
            setattr(model._meta, "rules", rules)
            # full_clean Methode hinzufügen
            model.full_clean = getFullCleanMethode(model)
        # Interface-Typ bestimmen
        attrs["_interface_type"] = interface._interface_type
        interface_cls = type(interface.__name__, (interface,), {})
        setattr(interface_cls, "_model", model)
        attrs["Interface"] = interface_cls

        # add factory class
        factory_definition = getattr(interface, "Factory", None)
        factory_attributes: dict[str, Any] = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        factory_class = type(f"{name}Factory", (AutoFactory,), factory_attributes)
        # factory_class._meta.model = model
        attrs["Factory"] = factory_class

        return attrs, interface_cls, model

    @staticmethod
    def _postCreate(
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        model: relatedClass,
    ) -> None:
        interface_class._parent_class = new_class
        setattr(model, "_general_manager_class", new_class)

    @classmethod
    def handleInterface(
        cls,
    ) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        This method returns a pre and a post GeneralManager creation method
        and is called inside the GeneralManagerMeta class to initialize the
        Interface.
        The pre creation method is called before the GeneralManager instance
        is created to modify the kwargs.
        The post creation method is called after the GeneralManager instance
        is created to modify the instance and add additional data.
        """
        return cls._preCreate, cls._postCreate

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        This method returns the field type for the given field name.
        """
        field = cls._model._meta.get_field(field_name)
        if field.is_relation and field.related_model:
            return field.related_model._general_manager_class
        return type(field)


class ReadOnlyInterface(DBBasedInterface):
    _interface_type = "readonly"

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
        if isinstance(json_data, list):
            data_list: list[Any] = json_data
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
            json_unique_values: set[Any] = set()

            # Daten synchronisieren
            for data in data_list:
                lookup = {field: data[field] for field in unique_fields}
                unique_identifier = tuple(lookup[field] for field in unique_fields)
                json_unique_values.add(unique_identifier)

                instance, _ = model.objects.get_or_create(**lookup)
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
    def readOnlyPostCreate(func: Callable[..., Any]) -> Callable[..., Any]:
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
    def handleInterface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        This method returns a pre and a post GeneralManager creation method
        and is called inside the GeneralManagerMeta class to initialize the
        Interface.
        The pre creation method is called before the GeneralManager instance
        is created to modify the kwargs.
        The post creation method is called after the GeneralManager instance
        is created to modify the instance and add additional data.
        """
        return cls._preCreate, cls.readOnlyPostCreate(cls._postCreate)


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
        instance.changed_by_id = creator_id
        instance.full_clean()
        if history_comment:
            update_change_reason(instance, history_comment)
        instance.save()

        return instance.pk


class DatabaseBucket(Bucket[GeneralManagerType]):

    def __init__(
        self,
        data: models.QuerySet[modelsModel],
        manager_class: Type[GeneralManagerType],
        filter_definitions: dict[str, list[Any]] = {},
        exclude_definitions: dict[str, list[Any]] = {},
    ):
        if data is None:
            data = manager_class.filter(**filter_definitions).exclude(
                **exclude_definitions
            )
        self._data = data

        self._manager_class = manager_class
        self.filters = {**filter_definitions}
        self.excludes = {**exclude_definitions}

    def __iter__(self) -> Generator[GeneralManagerType]:
        for item in self._data:
            yield self._manager_class(item.pk)

    def __or__(
        self,
        other: Bucket[GeneralManagerType] | GeneralManager[GeneralManagerType],
    ) -> DatabaseBucket[GeneralManagerType]:
        from general_manager.manager.generalManager import GeneralManager

        if isinstance(other, GeneralManager) and other.__class__ == self._manager_class:
            return self.__or__(self.filter(id__in=[getattr(other, "id")]))
        if not isinstance(other, self.__class__):
            raise ValueError("Cannot combine different bucket types")
        if self._manager_class != other._manager_class:
            raise ValueError("Cannot combine different bucket managers")
        return self.__class__(
            self._data | other._data,
            self._manager_class,
            {},
        )

    def __mergeFilterDefinitions(
        self, basis: dict[str, list[Any]], **kwargs: Any
    ) -> dict[str, list[Any]]:
        kwarg_filter: dict[str, list[Any]] = {}
        for key, value in basis.items():
            kwarg_filter[key] = value
        for key, value in kwargs.items():
            if key not in kwarg_filter:
                kwarg_filter[key] = []
            kwarg_filter[key].append(value)
        return kwarg_filter

    def filter(self, **kwargs: Any) -> DatabaseBucket:
        merged_filter = self.__mergeFilterDefinitions(self.filters, **kwargs)
        return self.__class__(
            self._data.filter(**kwargs),
            self._manager_class,
            merged_filter,
            self.excludes,
        )

    def exclude(self, **kwargs: Any) -> DatabaseBucket:
        merged_exclude = self.__mergeFilterDefinitions(self.excludes, **kwargs)
        return self.__class__(
            self._data.exclude(**kwargs),
            self._manager_class,
            self.filters,
            merged_exclude,
        )

    def first(self) -> GeneralManagerType | None:
        first_element = self._data.first()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def last(self) -> GeneralManagerType | None:
        first_element = self._data.last()
        if first_element is None:
            return None
        return self._manager_class(first_element.pk)

    def count(self) -> int:
        return self._data.count()

    def all(self) -> DatabaseBucket:
        return self.__class__(self._data.all(), self._manager_class)

    def get(self, **kwargs: Any) -> GeneralManagerType:
        element = self._data.get(**kwargs)
        return self._manager_class(element.pk)

    def __getitem__(self, item: int | slice) -> GeneralManagerType | DatabaseBucket:
        if isinstance(item, slice):
            return self.__class__(self._data[item], self._manager_class)
        return self._manager_class(self._data[item].pk)

    def __len__(self) -> int:
        return self._data.count()

    def __repr__(self) -> str:
        return f"{self._manager_class.__name__}Bucket ({self._data})"

    def __contains__(self, item: GeneralManagerType | models.Model) -> bool:
        from general_manager.manager.generalManager import GeneralManager

        if isinstance(item, GeneralManager):
            return getattr(item, "id") in self._data.values_list("pk", flat=True)
        return item in self._data

    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> DatabaseBucket:
        if isinstance(key, str):
            key = (key,)
        if reverse:
            sorted_data = self._data.order_by(*[f"-{k}" for k in key])
        else:
            sorted_data = self._data.order_by(*key)
        return self.__class__(sorted_data, self._manager_class)
