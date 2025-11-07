"""Read-only interface that mirrors JSON datasets into Django models."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Type, cast

from django.core.checks import Warning
from django.db import connection, models, transaction, IntegrityError

from general_manager.interface.database_based_interface import (
    DBBasedInterface,
    GeneralManagerBasisModel,
    attributes,
    classPostCreationMethod,
    classPreCreationMethod,
    generalManagerClassName,
    interfaceBaseClass,
)
from general_manager.interface.utils.errors import (
    InvalidReadOnlyDataFormatError,
    InvalidReadOnlyDataTypeError,
    MissingReadOnlyDataError,
    MissingUniqueFieldError,
)
from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager


logger = get_logger("interface.read_only")


class ReadOnlyInterface(DBBasedInterface[GeneralManagerBasisModel]):
    """Interface that reads static JSON data into a managed read-only model."""

    _interface_type: ClassVar[str] = "readonly"
    _parent_class: ClassVar[Type["GeneralManager"]]

    @staticmethod
    def get_unique_fields(model: Type[models.Model]) -> set[str]:
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
    def sync_data(cls) -> None:
        """
        Synchronize the Django model table with the parent manager's class-level `_data` payload.

        Parses the parent manager's `_data` (either a JSON string decoding to a list of dicts or an already-parsed list of dicts) and updates the model to match: create records present in the data, update editable fields of existing records, and deactivate previously active records that are not present in the data. Logs a summary if any records were created, updated, or deactivated.

        Raises:
            MissingReadOnlyDataError: If the parent manager class does not define `_data`.
            InvalidReadOnlyDataFormatError: If a JSON string `_data` does not decode to a list of dictionaries or an item is missing required unique fields.
            InvalidReadOnlyDataTypeError: If `_data` is neither a string nor a list.
            MissingUniqueFieldError: If the model exposes no unique fields usable to identify records.
        """
        if cls.ensure_schema_is_up_to_date(cls._parent_class, cls._model):
            logger.warning(
                "readonly schema out of date",
                context={
                    "manager": cls._parent_class.__name__,
                    "model": cls._model.__name__,
                },
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

        unique_fields = cls.get_unique_fields(model)
        unique_field_order = tuple(sorted(unique_fields))
        if not unique_fields:
            raise MissingUniqueFieldError(parent_class.__name__)

        changes: dict[str, list[models.Model]] = {
            "created": [],
            "updated": [],
            "deactivated": [],
        }

        editable_fields = {
            f.name
            for f in model._meta.local_fields
            if getattr(f, "editable", True) and not getattr(f, "primary_key", False)
        } - {"is_active"}

        manager = model.all_objects if hasattr(model, "all_objects") else model.objects

        with transaction.atomic():
            json_unique_values: set[tuple[Any, ...]] = set()

            # data synchronization
            for idx, data in enumerate(data_list):
                try:
                    lookup = {field: data[field] for field in unique_field_order}
                except KeyError as e:
                    missing = e.args[0]
                    raise InvalidReadOnlyDataFormatError() from KeyError(
                        f"Item {idx} missing unique field '{missing}'."
                    )
                unique_identifier = tuple(lookup[field] for field in unique_field_order)
                json_unique_values.add(unique_identifier)
                instance = manager.filter(**lookup).first()
                is_created = False
                if instance is None:
                    # sanitize input and create with race-safety
                    allowed_fields = {f.name for f in model._meta.local_fields}
                    create_kwargs = {
                        k: v for k, v in data.items() if k in allowed_fields
                    }
                    try:
                        instance = model.objects.create(**create_kwargs)
                        is_created = True
                    except IntegrityError:
                        # created concurrently — fetch it
                        instance = manager.filter(**lookup).first()
                        if instance is None:
                            raise
                updated = False
                for field_name in editable_fields.intersection(data.keys()):
                    value = data[field_name]
                    if getattr(instance, field_name, None) != value:
                        setattr(instance, field_name, value)
                        updated = True
                if updated or not instance.is_active:  # type: ignore[union-attr]
                    instance.is_active = True  # type: ignore[union-attr]
                    instance.save()
                    changes["created" if is_created else "updated"].append(instance)

            # deactivate instances not in JSON data
            existing_instances = model.objects.filter(is_active=True)
            for instance in existing_instances:
                lookup = {
                    field: getattr(instance, field) for field in unique_field_order
                }
                unique_identifier = tuple(lookup[field] for field in unique_field_order)
                if unique_identifier not in json_unique_values:
                    instance.is_active = False  # type: ignore[attr-defined]
                    instance.save()
                    changes["deactivated"].append(instance)

        if changes["created"] or changes["updated"] or changes["deactivated"]:
            logger.info(
                "readonly data synchronized",
                context={
                    "manager": parent_class.__name__,
                    "model": model.__name__,
                    "created": len(changes["created"]),
                    "updated": len(changes["updated"]),
                    "deactivated": len(changes["deactivated"]),
                },
            )

    @staticmethod
    def ensure_schema_is_up_to_date(
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
    def read_only_post_create(func: Callable[..., Any]) -> Callable[..., Any]:
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
    def read_only_pre_create(func: Callable[..., Any]) -> Callable[..., Any]:
        """
        Wrap a manager pre-creation function to ensure the interface has a Meta with use_soft_delete=True before invocation.

        The returned wrapper creates a dummy Meta on the provided interface if one does not exist, sets Meta.use_soft_delete = True, and then calls the original pre-creation function with the same arguments (including the original `base_model_class`).

        Parameters:
            func (Callable[..., Any]): A pre-creation hook that accepts (name, attrs, interface, base_model_class) and returns (attrs, interface, base_model_class | None).

        Returns:
            Callable[..., Any]: A wrapper function that performs the Meta initialization and soft-delete enabling, then returns the wrapped function's result.
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
            Ensure the interface has a Meta with soft-delete enabled, then invoke the wrapped pre-create function.

            Parameters:
                name: The name of the manager class being created.
                attrs: Attributes to assign to the manager class.
                interface: The interface base class; a `Meta` class will be created on it if missing and `use_soft_delete` will be set to True.
                base_model_class: The base model class to pass through to the wrapped function (defaults to GeneralManagerBasisModel).

            Returns:
                A tuple of (attrs, interface, base_model_class_or_none) as returned by the wrapped function.
            """
            meta = getattr(interface, "Meta", None)
            if meta is None:
                meta = type("Meta", (), {})
                interface.Meta = meta  # type: ignore[attr-defined]
            meta.use_soft_delete = True  # type: ignore[union-attr]
            return func(name, attrs, interface, base_model_class)

        return wrapper

    @classmethod
    def handle_interface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Return the pre- and post-creation hook methods for integrating the interface with a manager meta-class system.

        The returned tuple includes:
        - A pre-creation method that ensures the base model class is set for read-only operation.
        - A post-creation method that registers the manager class as read-only.

        Returns:
            tuple: The pre-creation and post-creation hook methods for manager class lifecycle integration.
        """
        return cls.read_only_pre_create(cls._pre_create), cls.read_only_post_create(
            cls._post_create
        )
