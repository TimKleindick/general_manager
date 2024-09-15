import json
from typing import Type, ClassVar
from django.db import models


class InterfaceBase:
    _model: ClassVar[Type[models.Model]]
    _parent_class: ClassVar[Type]
    _interface_type: ClassVar[str]


from django.db import transaction


class ReadOnlyInterface(InterfaceBase):
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


class DatabaseInterface(InterfaceBase):
    _interface_type = "database"
