"""Read-only interface that mirrors JSON datasets into Django models."""

from __future__ import annotations
import json

from typing import Type, Any, Callable, TYPE_CHECKING, cast, ClassVar
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
from django.core.checks import Warning
import logging

if TYPE_CHECKING:
    from general_manager.manager.generalManager import GeneralManager


logger = logging.getLogger(__name__)


class MissingReadOnlyDataError(ValueError):
    """Raised when a ReadOnlyInterface lacks the required `_data` attribute."""

    def __init__(self, interface_name: str) -> None:
        """
        Exception raised when a ReadOnlyInterface is missing the required `_data` attribute.

        Parameters:
            interface_name (str): Name of the interface class; used to construct the exception message.
        """
        super().__init__(
            f"ReadOnlyInterface '{interface_name}' must define a '_data' attribute."
        )


class MissingUniqueFieldError(ValueError):
    """Raised when a ReadOnlyInterface has no unique fields defined."""

    def __init__(self, interface_name: str) -> None:
        """
        Initialize an error for a read-only interface that defines no unique fields.

        Parameters:
            interface_name (str): Name of the interface class missing at least one unique field; this name is included in the exception message.
        """
        super().__init__(
            f"ReadOnlyInterface '{interface_name}' must declare at least one unique field."
        )


class InvalidReadOnlyDataFormatError(TypeError):
    """Raised when the `_data` JSON does not decode to a list of dictionaries."""

    def __init__(self) -> None:
        """
        Exception raised when the `_data` JSON does not decode to a list of dictionaries.

        Initializes the exception with the message "_data JSON must decode to a list of dictionaries."
        """
        super().__init__("_data JSON must decode to a list of dictionaries.")


class InvalidReadOnlyDataTypeError(TypeError):
    """Raised when the `_data` attribute is neither JSON string nor list."""

    def __init__(self) -> None:
        """
        Initialize the InvalidReadOnlyDataTypeError with a standard error message.

        Raises a TypeError indicating that the `_data` attribute must be either a JSON string or a list of dictionaries.
        """
        super().__init__("_data must be a JSON string or a list of dictionaries.")


class ReadOnlyInterface(DBBasedInterface[GeneralManagerBasisModel]):
    """Interface that reads static JSON data into a managed read-only model."""

    _interface_type: ClassVar[str] = "readonly"
    _parent_class: ClassVar[Type["GeneralManager"]]

    @staticmethod
    def getUniqueFields(model: Type[models.Model]) -> set[str]:
        """
        Determine which fields on the given Django model uniquely identify its instances.

        The result includes fields declared with `unique=True` (excluding a primary key named "id"), any fields in `unique_together` tuples, and fields referenced by `UniqueConstraint` objects.

        Parameters:
            model (type[models.Model]): Django model to inspect.

        Returns:
            set[str]: Names of fields that participate in unique constraints for the model.
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
        Synchronize the Django model with the parent manager's class-level `_data` JSON.

        Parses the parent class's `_data` (JSON string or list of dicts), ensures the model schema is up to date, and within a single transaction creates, updates, or deactivates model instances to match the parsed data. Newly created or updated instances are marked `is_active = True`; existing active instances absent from the data are marked `is_active = False`. Logs a summary when any changes occur.

        Raises:
            MissingReadOnlyDataError: If the parent manager class does not define `_data`.
            InvalidReadOnlyDataFormatError: If `_data` is a JSON string that does not decode to a list of dictionaries.
            InvalidReadOnlyDataTypeError: If `_data` is neither a string nor a list.
            MissingUniqueFieldError: If the model exposes no unique fields to identify records.
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
            raise MissingReadOnlyDataError(parent_class.__name__)

        # Parse JSON into Python structures
        if isinstance(json_data, str):
            parsed_data = json.loads(json_data)
            if not isinstance(parsed_data, list):
                raise InvalidReadOnlyDataFormatError()
        elif isinstance(json_data, list):
            parsed_data = json_data
        else:
            raise InvalidReadOnlyDataTypeError()

        data_list = cast(list[dict[str, Any]], parsed_data)

        unique_fields = cls.getUniqueFields(model)
        if not unique_fields:
            raise MissingUniqueFieldError(parent_class.__name__)

        changes: dict[str, list[models.Model]] = {
            "created": [],
            "updated": [],
            "deactivated": [],
        }

        with transaction.atomic():
            json_unique_values: set[Any] = set()

            # data synchronization
            for idx, data in enumerate(data_list):
                try:
                    lookup = {field: data[field] for field in unique_fields}
                except KeyError as e:
                    missing = e.args[0]
                    raise InvalidReadOnlyDataFormatError() from KeyError(
                        f"Item {idx} missing unique field '{missing}'."
                    )
                unique_identifier = tuple(lookup[field] for field in unique_fields)
                json_unique_values.add(unique_identifier)

                instance, is_created = model.objects.get_or_create(**lookup)
                updated = False
                editable_fields = {
                    f.name
                    for f in model._meta.local_fields
                    if getattr(f, "editable", True)
                    and not getattr(f, "primary_key", False)
                } - {"is_active"}
                for field_name in editable_fields.intersection(data.keys()):
                    value = data[field_name]
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
        Check whether the database schema matches the model definition.

        Parameters:
            new_manager_class (type[GeneralManager]): Manager class owning the interface.
            model (type[models.Model]): Django model whose table should be inspected.

        Returns:
            list[Warning]: Warnings describing schema mismatches; empty when up to date.
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
                    "Database table does not exist!",
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
        ) -> None:
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
            base_model_class: type[GeneralManagerBasisModel] = GeneralManagerBasisModel,
        ) -> tuple[
            attributes, interfaceBaseClass, type[GeneralManagerBasisModel] | None
        ]:
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
