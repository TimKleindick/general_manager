from __future__ import annotations
import json

from typing import Type, Any, Callable, TYPE_CHECKING
from django.db import models, transaction
from general_manager.interface.databaseBasedInterface import (
    DBBasedInterface,
    GeneralManagerBasisModel,
    classPreCreationMethod,
    classPostCreationMethod,
    generalManagerClassName,
    attributes,
    interfaceBaseClass,
)
from django.db import connection
from typing import ClassVar
from django.core.checks import Warning
import logging

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager


logger = logging.getLogger(__name__)


class ReadOnlyInterface(DBBasedInterface):
    _interface_type = "readonly"
    _model: Type[GeneralManagerBasisModel]
    _parent_class: Type[GeneralManager]

    @staticmethod
    def getUniqueFields(model: Type[models.Model]) -> set[str]:
        """
        Returns a list of unique fields for the ReadOnlyInterface.

        This method is used to retrieve the unique fields that are defined in the parent class.
        """
        opts = model._meta
        unique_fields: set[str] = set()

        for field in opts.local_fields:
            if getattr(field, "unique", False):
                if field.name == "id":
                    continue
                unique_fields.add(field.name)

        for ut in opts.unique_together:
            unique_fields.update(ut)

        for constraint in opts.constraints:
            if isinstance(constraint, models.UniqueConstraint):
                unique_fields.update(constraint.fields)

        return unique_fields

    @classmethod
    def syncData(cls) -> None:
        """
        Synchronizes the database model with JSON data, ensuring exact correspondence.

        This method parses JSON data from the parent class and updates the associated Django model so that its records exactly match the JSON content. It creates or updates instances based on unique fields and deletes any database entries not present in the JSON data. Raises a ValueError if required attributes are missing or if the JSON data is invalid.
        """
        if cls.ensureSchemaIsUpToDate(cls._parent_class, cls._model):
            logger.warning(
                f"Schema for ReadOnlyInterface '{cls._parent_class.__name__}' is not up to date."
            )
            return

        model = cls._model
        parent_class = cls._parent_class
        json_data = getattr(parent_class, "_data", None)
        if json_data is None:
            raise ValueError(
                f"For ReadOnlyInterface '{parent_class.__name__}' must set '_data'"
            )

        # JSON-Daten parsen
        if isinstance(json_data, str):
            data_list = json.loads(json_data)
        elif isinstance(json_data, list):
            data_list: list[Any] = json_data
        else:
            raise ValueError("_data must be a JSON string or a list of dictionaries")

        unique_fields = cls.getUniqueFields(model)
        if not unique_fields:
            raise ValueError(
                f"For ReadOnlyInterface '{parent_class.__name__}' must have at least one unique field."
            )

        changes = {
            "created": [],
            "updated": [],
            "deactivated": [],
        }

        with transaction.atomic():
            json_unique_values: set[Any] = set()

            # data synchronization
            for data in data_list:
                lookup = {field: data[field] for field in unique_fields}
                unique_identifier = tuple(lookup[field] for field in unique_fields)
                json_unique_values.add(unique_identifier)

                instance, is_created = model.objects.get_or_create(**lookup)
                updated = False
                for field_name, value in data.items():
                    if getattr(instance, field_name, None) != value:
                        setattr(instance, field_name, value)
                        updated = True
                if updated or not instance.is_active:
                    instance.is_active = True
                    instance.save()
                    changes["created" if is_created else "updated"].append(instance)

            # deactivate instances not in JSON data
            existing_instances = model.objects.filter(is_active=True)
            for instance in existing_instances:
                lookup = {field: getattr(instance, field) for field in unique_fields}
                unique_identifier = tuple(lookup[field] for field in unique_fields)
                if unique_identifier not in json_unique_values:
                    instance.is_active = False
                    instance.save()
                    changes["deactivated"].append(instance)

        if changes["created"] or changes["updated"] or changes["deactivated"]:
            logger.info(
                f"Data changes for ReadOnlyInterface '{parent_class.__name__}': "
                f"Created: {len(changes['created'])}, "
                f"Updated: {len(changes['updated'])}, "
                f"Deactivated: {len(changes['deactivated'])}"
            )

    @staticmethod
    def ensureSchemaIsUpToDate(
        new_manager_class: Type[GeneralManager], model: Type[models.Model]
    ) -> list[Warning]:
        """
        This method is called to ensure that the schema of the model is up to date.
        """

        def table_exists(table_name: str) -> bool:
            with connection.cursor() as cursor:
                tables = connection.introspection.table_names(cursor)
            return table_name in tables

        def compare_model_to_table(
            model: Type[models.Model], table: str
        ) -> tuple[list[str], list[str]]:
            with connection.cursor() as cursor:
                desc = connection.introspection.get_table_description(cursor, table)
            existing_cols = {col.name for col in desc}
            model_cols = {field.column for field in model._meta.local_fields}
            missing = model_cols - existing_cols
            extra = existing_cols - model_cols
            return list(missing), list(extra)

        table = model._meta.db_table
        if not table_exists(table):
            return [
                Warning(
                    f"Database table does not exist!",
                    hint=f"ReadOnlyInterface '{new_manager_class.__name__}' (Table '{table}') does not exist in the database.",
                    obj=model,
                )
            ]
        missing, extra = compare_model_to_table(model, table)
        if missing or extra:
            return [
                Warning(
                    "Database schema mismatch!",
                    hint=(
                        f"ReadOnlyInterface '{new_manager_class.__name__}' has missing columns: {missing} or extra columns: {extra}. \n"
                        "        Please update the model or the database schema, to enable data synchronization."
                    ),
                    obj=model,
                )
            ]
        return []

    @staticmethod
    def readOnlyPostCreate(func: Callable[..., Any]) -> Callable[..., Any]:
        """
        Decorator for post-creation hooks that registers the interface class as read-only.

        Wraps a function to be called after a class creation event, then appends the interface
        class to the meta-class's `read_only_classes` list.
        """

        def wrapper(
            new_class: Type[GeneralManager],
            interface_cls: Type[ReadOnlyInterface],
            model: Type[GeneralManagerBasisModel],
        ):
            from general_manager.manager.meta import GeneralManagerMeta

            func(new_class, interface_cls, model)
            GeneralManagerMeta.read_only_classes.append(new_class)

        return wrapper

    @staticmethod
    def readOnlyPreCreate(func: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(
            name: generalManagerClassName,
            attrs: attributes,
            interface: interfaceBaseClass,
            base_model_class=GeneralManagerBasisModel,
        ):
            return func(
                name, attrs, interface, base_model_class=GeneralManagerBasisModel
            )

        return wrapper

    @classmethod
    def handleInterface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Returns pre- and post-creation methods for integrating the interface with a GeneralManager.

        The pre-creation method modifies keyword arguments before a GeneralManager instance is created. The post-creation method, wrapped with a decorator, modifies the instance after creation to add additional data. These methods are intended for use by the GeneralManagerMeta class during the manager's lifecycle.
        """
        return cls.readOnlyPreCreate(cls._preCreate), cls.readOnlyPostCreate(
            cls._postCreate
        )
