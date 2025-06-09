from __future__ import annotations
import json

from typing import Type, Any, Callable, TYPE_CHECKING
from django.db import models, transaction
from general_manager.interface.databaseBasedInterface import (
    DBBasedInterface,
    GeneralManagerModel,
    classPreCreationMethod,
    classPostCreationMethod,
)

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager
    from general_manager.manager.meta import GeneralManagerMeta


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
