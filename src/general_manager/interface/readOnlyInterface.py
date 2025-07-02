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


class ReadOnlyInterface(DBBasedInterface[GeneralManagerBasisModel]):
    _interface_type = "readonly"
    _parent_class: Type[GeneralManager]

    @staticmethod
    def getUniqueFields(model: Type[models.Model]) -> set[str]:
        """
        Return a set of field names that uniquely identify instances of the specified Django model.
        
        Considers fields marked as unique (excluding "id"), as well as fields defined in `unique_together` and `UniqueConstraint` constraints.
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
        Synchronizes the associated Django model with JSON data from the parent class, ensuring the database records match the provided data exactly.
        
        Parses the JSON data, creates or updates model instances based on unique fields, and deactivates any database records not present in the JSON data. Raises a ValueError if required attributes are missing, if the JSON data is invalid, or if no unique fields are defined.
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
        Check if the database schema for a Django model matches its model definition.
        
        Returns:
            A list of Django Warning objects describing schema issues, such as missing tables or column mismatches. Returns an empty list if the schema is up to date.
        """

        def table_exists(table_name: str) -> bool:
            """
            Determine whether a database table with the specified name exists.
            
            Parameters:
                table_name (str): Name of the database table to check.
            
            Returns:
                bool: True if the table exists, False otherwise.
            """
            with connection.cursor() as cursor:
                tables = connection.introspection.table_names(cursor)
            return table_name in tables

        def compare_model_to_table(
            model: Type[models.Model], table: str
        ) -> tuple[list[str], list[str]]:
            """
            Compares the fields of a Django model to the columns of a specified database table.
            
            Returns:
                A tuple containing two lists:
                    - The first list contains column names defined in the model but missing from the database table.
                    - The second list contains column names present in the database table but not defined in the model.
            """
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
        Decorator for post-creation hooks that registers a new manager class as read-only.
        
        After the wrapped post-creation function is executed, the newly created manager class is added to the meta-class's list of read-only classes, marking it as a read-only interface.
        """

        def wrapper(
            new_class: Type[GeneralManager],
            interface_cls: Type[ReadOnlyInterface],
            model: Type[GeneralManagerBasisModel],
        ):
            """
            Registers a newly created manager class as read-only after executing the wrapped post-creation function.
            
            This function appends the new manager class to the list of read-only classes in the meta system, ensuring it is recognized as a read-only interface.
            """
            from general_manager.manager.meta import GeneralManagerMeta

            func(new_class, interface_cls, model)
            GeneralManagerMeta.read_only_classes.append(new_class)

        return wrapper

    @staticmethod
    def readOnlyPreCreate(func: Callable[..., Any]) -> Callable[..., Any]:
        """
        Decorator for pre-creation hook functions that ensures the base model class is set to `GeneralManagerBasisModel`.
        
        Wraps a pre-creation function, injecting `GeneralManagerBasisModel` as the `base_model_class` argument before the manager class is created.
        """

        def wrapper(
            name: generalManagerClassName,
            attrs: attributes,
            interface: interfaceBaseClass,
            base_model_class=GeneralManagerBasisModel,
        ):
            """
            Wraps a function to ensure the `base_model_class` argument is set to `GeneralManagerBasisModel` before invocation.
            
            Parameters:
                name: The name of the manager class being created.
                attrs: Attributes for the manager class.
                interface: The interface base class to use.
            
            Returns:
                The result of calling the wrapped function with `base_model_class` set to `GeneralManagerBasisModel`.
            """
            return func(
                name, attrs, interface, base_model_class=GeneralManagerBasisModel
            )

        return wrapper

    @classmethod
    def handleInterface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Return the pre- and post-creation hook methods for integrating the interface with a manager meta-class system.
        
        The returned tuple includes:
        - A pre-creation method that ensures the base model class is set for read-only operation.
        - A post-creation method that registers the manager class as read-only.
        
        Returns:
            tuple: The pre-creation and post-creation hook methods for manager class lifecycle integration.
        """
        return cls.readOnlyPreCreate(cls._preCreate), cls.readOnlyPostCreate(
            cls._postCreate
        )
