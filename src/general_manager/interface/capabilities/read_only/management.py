"""Capabilities that power ReadOnlyInterface behavior."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Optional, Type, cast, ClassVar

from django.core.checks import Warning
from django.db import (
    IntegrityError,
    connection as django_connection,
    models,
    transaction as django_transaction,
)

from general_manager.interface.utils.models import GeneralManagerBasisModel
from general_manager.interface.utils.errors import (
    InvalidReadOnlyDataFormatError,
    InvalidReadOnlyDataTypeError,
    MissingReadOnlyDataError,
    MissingReadOnlyBindingError,
    MissingUniqueFieldError,
)
from general_manager.logging import get_logger

from ..base import CapabilityName
from ..builtin import BaseCapability
from ._compat import call_with_observability

logger = get_logger("interface.read_only")


def _resolve_logger():
    """
    Resolve the logger to use for read-only capability operations.

    Returns:
        The logger instance from the `read_only` package if present, otherwise the module-level `logger`.
    """
    from general_manager.interface.capabilities import read_only as read_only_package

    patched = getattr(read_only_package, "logger", None)
    return patched or logger


if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import (
        OrmInterfaceBase,
    )
    from general_manager.manager.general_manager import GeneralManager


class ReadOnlyManagementCapability(BaseCapability):
    """Provide schema verification and data-sync behavior for read-only interfaces."""

    name: ClassVar[CapabilityName] = "read_only_management"

    def get_unique_fields(self, model: Type[models.Model]) -> set[str]:
        """
        Collect candidate unique field names from a Django model's metadata.

        Parameters:
            model (Type[models.Model]): Django model class to inspect.

        Returns:
            set[str]: Unique field names discovered from the model's `local_fields` (fields with `unique=True`), `unique_together`, and `UniqueConstraint` definitions. Returns an empty set if the model has no metadata.
        """
        opts = getattr(model, "_meta", None)
        if opts is None:
            return set()

        unique_fields: set[str] = set()
        local_fields = getattr(opts, "local_fields", []) or []

        for field in local_fields:
            field_name = getattr(field, "name", None)
            if not field_name or field_name == "id":
                continue
            if getattr(field, "unique", False):
                unique_fields.add(field_name)

        raw_unique_together = getattr(opts, "unique_together", []) or []
        if isinstance(raw_unique_together, (list, tuple)):
            iterable = raw_unique_together
        else:  # pragma: no cover - defensive branch
            iterable = [raw_unique_together]

        for entry in iterable:
            if isinstance(entry, str):
                unique_fields.add(entry)
                continue
            if isinstance(entry, (list, tuple, set)):
                unique_fields.update(entry)

        for constraint in getattr(opts, "constraints", []) or []:
            if isinstance(constraint, models.UniqueConstraint):
                unique_fields.update(getattr(constraint, "fields", []))

        return unique_fields

    def ensure_schema_is_up_to_date(
        self,
        interface_cls: type["OrmInterfaceBase[Any]"],
        manager_cls: Type["GeneralManager"],
        model: Type[models.Model],
        *,
        connection=None,
    ) -> list[Warning]:
        """
        Verify that the Django model's declared schema matches the actual database table and return any schema-related warnings.

        Performs the following checks and returns corresponding Django `Warning` objects when applicable:
        - Model metadata (`_meta`) is missing.
        - `db_table` is not defined on the model meta.
        - The named database table does not exist.
        - The table's columns differ from the model's local field columns (missing or extra columns).

        Parameters:
            connection (optional): Database connection to use for introspection. If omitted, the default Django connection is used.

        Returns:
            list[Warning]: A list of Django system-check `Warning` objects describing discovered mismatches; returns an empty list when no issues are found.
        """
        payload_snapshot = {
            "manager": manager_cls.__name__,
            "model": getattr(model, "__name__", str(model)),
        }

        def _perform() -> list[Warning]:
            """
            Validate that the given Django model's metadata and database table match, returning any schema-related warnings.

            Performs checks for missing model metadata, missing or empty db_table, non-existent database table, and mismatched columns between the model and the actual table; each problem is reported as a Django `Warning` describing the issue and referencing the model.

            Returns:
                list[Warning]: A list of Django `Warning` instances for detected issues; an empty list if no schema problems are found.
            """
            opts = getattr(model, "_meta", None)
            if opts is None:
                return [
                    Warning(
                        "Model metadata missing!",
                        hint=(
                            f"ReadOnlyInterface '{manager_cls.__name__}' cannot validate "
                            "schema because the model does not expose Django metadata."
                        ),
                        obj=model,
                    )
                ]

            db_connection = connection or django_connection

            def table_exists(table_name: str) -> bool:
                """
                Determine whether a table with the given name exists in the current database connection.

                Returns:
                    `true` if the table exists, `false` otherwise.
                """
                with db_connection.cursor() as cursor:
                    tables = db_connection.introspection.table_names(cursor)
                return table_name in tables

            def compare_model_to_table(
                model_arg: Type[models.Model], table: str
            ) -> tuple[list[str], list[str]]:
                """
                Compare a Django model's declared column names to the actual columns of a database table.

                Parameters:
                    model_arg (Type[models.Model]): The Django model class whose local field column names will be compared.
                    table (str): The database table name to compare against.

                Returns:
                    tuple[list[str], list[str]]: A tuple of two lists:
                        - The first list contains column names that are declared on the model but missing from the table.
                        - The second list contains column names that exist in the table but are not declared on the model.
                """
                model_opts = getattr(model_arg, "_meta", None)
                with db_connection.cursor() as cursor:
                    desc = db_connection.introspection.get_table_description(
                        cursor, table
                    )
                existing_cols = {col.name for col in desc}
                local_fields = getattr(model_opts, "local_fields", []) or []
                model_cols = {
                    cast(
                        str,
                        getattr(field, "column", None) or getattr(field, "name", ""),
                    )
                    for field in local_fields
                }
                model_cols.discard("")
                missing = model_cols - existing_cols
                extra = existing_cols - model_cols
                return list(missing), list(extra)

            table = getattr(opts, "db_table", None)
            if not table:
                return [
                    Warning(
                        "Model metadata incomplete!",
                        hint=(
                            f"ReadOnlyInterface '{manager_cls.__name__}' must define "
                            "a db_table on the model meta data."
                        ),
                        obj=model,
                    )
                ]

            if not table_exists(table):
                return [
                    Warning(
                        "Database table does not exist!",
                        hint=f"ReadOnlyInterface '{manager_cls.__name__}' (Table '{table}') does not exist in the database.",
                        obj=model,
                    )
                ]
            missing, extra = compare_model_to_table(model, table)
            if missing or extra:
                return [
                    Warning(
                        "Database schema mismatch!",
                        hint=(
                            f"ReadOnlyInterface '{manager_cls.__name__}' has missing columns: {missing} or extra columns: {extra}. \n"
                            "        Please update the model or the database schema, to enable data synchronization."
                        ),
                        obj=model,
                    )
                ]
            return []

        return call_with_observability(
            interface_cls,
            operation="read_only.ensure_schema",
            payload=payload_snapshot,
            func=_perform,
        )

    def sync_data(
        self,
        interface_cls: type["OrmInterfaceBase[Any]"],
        *,
        connection: Optional[Any] = None,
        transaction: Optional[Any] = None,
        integrity_error: Optional[Any] = None,
        json_module: Optional[Any] = None,
        logger_instance: Optional[Any] = None,
        unique_fields: set[str] | None = None,
        schema_validated: bool = False,
    ) -> None:
        """
        Synchronize the interface's bound read-only JSON data into the underlying Django model, creating, updating, and deactivating records to match the input.

        Parses the read-only payload defined on the interface's parent class, enforces a set of unique identifying fields to match incoming items to existing rows, writes only model-editable fields, marks matched records active, creates missing records, and deactivates previously active records not present in the incoming data. If schema validation is enabled (or performed), aborts when schema warnings are detected.

        Parameters:
            interface_cls (type[OrmInterfaceBase[Any]]): Read-only interface class whose parent class must expose `_data` and model binding.
            connection: Optional Django DB connection to use instead of the default.
            transaction: Optional Django transaction management module or object to use instead of the default.
            integrity_error: Optional exception class to treat as a DB integrity error (defaults to Django's IntegrityError).
            json_module: Optional JSON-like module to parse JSON strings (defaults to the standard library json).
            logger_instance: Optional logger to record sync results; falls back to the capability's resolved logger.
            unique_fields (set[str] | None): Explicit set of field names to use as the unique identifier for items; when omitted, the model's unique metadata is used.
            schema_validated (bool): When True, skip runtime schema validation; when False, ensure_schema_is_up_to_date is called before syncing and the sync is aborted if warnings are returned.
        """
        parent_class = getattr(interface_cls, "_parent_class", None)
        model = getattr(interface_cls, "_model", None)
        if parent_class is None or model is None:
            raise MissingReadOnlyBindingError(
                getattr(interface_cls, "__name__", str(interface_cls))
            )

        payload_snapshot = {
            "manager": getattr(parent_class, "__name__", None),
            "model": getattr(model, "__name__", None),
            "schema_validated": schema_validated,
        }

        def _perform() -> None:
            """
            Perform the core read-only data synchronization for the bound interface.

            Parses the interface's bound JSON data, validates or verifies schema state when required, and ensures the model's rows reflect the incoming data by creating new records, updating existing records, and deactivating records absent from the input. Uses the configured unique fields to identify records and only writes model fields that are allowed and editable; records changes are logged when any create/update/deactivate occurs.

            Raises:
                MissingReadOnlyDataError: if the parent interface has no `_data` attribute.
                InvalidReadOnlyDataTypeError: if the bound data is not a JSON string or a list.
                InvalidReadOnlyDataFormatError: if a JSON string does not decode to a list or an item is missing a required unique field.
                MissingUniqueFieldError: if no unique fields can be determined for the model.
                IntegrityError: if a create operation violates database constraints and reconciliation fails.
            """
            db_connection = connection or django_connection
            db_transaction = transaction or django_transaction
            integrity_error_cls = integrity_error or IntegrityError
            json_lib = json_module or json

            if not schema_validated:
                warnings = self.ensure_schema_is_up_to_date(
                    interface_cls,
                    parent_class,
                    model,
                    connection=db_connection,
                )
                if warnings:
                    _resolve_logger().warning(
                        "readonly schema out of date",
                        context={
                            "manager": parent_class.__name__,
                            "model": model.__name__,
                        },
                    )
                    return

            json_data = getattr(parent_class, "_data", None)
            if json_data is None:
                raise MissingReadOnlyDataError(parent_class.__name__)

            if isinstance(json_data, str):
                parsed_data = json_lib.loads(json_data)
                if not isinstance(parsed_data, list):
                    raise InvalidReadOnlyDataFormatError()
            elif isinstance(json_data, list):
                parsed_data = json_data
            else:
                raise InvalidReadOnlyDataTypeError()

            data_list = cast(list[dict[str, Any]], parsed_data)
            calculated_unique_fields = (
                unique_fields
                if unique_fields is not None
                else self.get_unique_fields(model)
            )
            unique_field_order = tuple(sorted(calculated_unique_fields))
            if not calculated_unique_fields:
                raise MissingUniqueFieldError(parent_class.__name__)

            changes: dict[str, list[models.Model]] = {
                "created": [],
                "updated": [],
                "deactivated": [],
            }

            model_opts = getattr(model, "_meta", None)
            local_fields = getattr(model_opts, "local_fields", []) or []
            editable_fields = {
                getattr(f, "name", "")
                for f in local_fields
                if getattr(f, "name", None)
                and getattr(f, "editable", True)
                and not getattr(f, "primary_key", False)
            }
            editable_fields.discard("is_active")

            manager = (
                model.all_objects if hasattr(model, "all_objects") else model.objects
            )
            active_logger = logger_instance or _resolve_logger()

            with db_transaction.atomic():
                json_unique_values: set[tuple[Any, ...]] = set()

                for idx, data in enumerate(data_list):
                    try:
                        lookup = {field: data[field] for field in unique_field_order}
                    except KeyError as exc:
                        missing = exc.args[0]
                        raise InvalidReadOnlyDataFormatError() from KeyError(
                            f"Item {idx} missing unique field '{missing}'."
                        )
                    unique_identifier = tuple(
                        lookup[field] for field in unique_field_order
                    )
                    json_unique_values.add(unique_identifier)
                    instance = cast(
                        GeneralManagerBasisModel | None,
                        manager.filter(**lookup).first(),
                    )
                    is_created = False
                    if instance is None:
                        allowed_fields = {
                            getattr(f, "name", "")
                            for f in local_fields
                            if getattr(f, "name", None)
                        }
                        allowed_fields.discard("")
                        create_kwargs = {
                            k: v for k, v in data.items() if k in allowed_fields
                        }
                        try:
                            instance = cast(
                                GeneralManagerBasisModel,
                                model.objects.create(**create_kwargs),
                            )
                            is_created = True
                        except integrity_error_cls:
                            instance = cast(
                                GeneralManagerBasisModel | None,
                                manager.filter(**lookup).first(),
                            )
                            if instance is None:
                                raise
                    if instance is None:
                        continue
                    updated = False
                    for field_name in editable_fields.intersection(data.keys()):
                        value = data[field_name]
                        if getattr(instance, field_name, None) != value:
                            setattr(instance, field_name, value)
                            updated = True
                    if updated or not getattr(instance, "is_active", True):
                        instance.is_active = True  # type: ignore[attr-defined]
                        instance.save()
                        changes["created" if is_created else "updated"].append(instance)

                existing_instances = model.objects.filter(is_active=True)
                for existing_instance in existing_instances:
                    lookup = {
                        field: getattr(existing_instance, field)
                        for field in unique_field_order
                    }
                    unique_identifier = tuple(
                        lookup[field] for field in unique_field_order
                    )
                    if unique_identifier not in json_unique_values:
                        existing_instance.is_active = False  # type: ignore[attr-defined]
                        existing_instance.save()
                        changes["deactivated"].append(existing_instance)

            if any(changes.values()):
                active_logger.info(
                    "readonly data synchronized",
                    context={
                        "manager": parent_class.__name__,
                        "model": model.__name__,
                        "created": len(changes["created"]),
                        "updated": len(changes["updated"]),
                        "deactivated": len(changes["deactivated"]),
                    },
                )

        return call_with_observability(
            interface_cls,
            operation="read_only.sync_data",
            payload=payload_snapshot,
            func=_perform,
        )

    def get_startup_hooks(
        self,
        interface_cls: type["OrmInterfaceBase[Any]"],
    ) -> tuple[Callable[[], None], ...]:
        """
        Provide a startup hook that triggers read-only data synchronization for the given interface.

        Parameters:
            interface_cls (type[OrmInterfaceBase[Any]]): Interface class used to derive the bound manager and model.

        Returns:
            tuple[Callable[[], None], ...]: A one-element tuple containing a callable that runs synchronization when invoked,
            or an empty tuple if the interface lacks the necessary manager/model metadata. The callable invokes the capability's
            sync logic and silently skips if the read-only binding is not yet available.
        """

        manager_cls = getattr(interface_cls, "_parent_class", None)
        model = getattr(interface_cls, "_model", None)
        if manager_cls is None or model is None:
            # Without metadata we cannot bind to the manager/model pair, so we
            # skip registration and rely on a later call once binding occurs.
            _resolve_logger().debug(
                "read-only startup hook registration deferred",
                context={
                    "interface": getattr(interface_cls, "__name__", None),
                    "has_parent": manager_cls is not None,
                    "has_model": model is not None,
                },
            )
            return tuple()

        def _sync() -> None:
            """
            Attempt to synchronize read-only data for the interface during startup.

            Calls the capability's sync_data for the captured interface. If the read-only
            binding is not available (raises MissingReadOnlyBindingError), logs a debug
            message and returns without raising.
            """
            try:
                self.sync_data(interface_cls)
            except MissingReadOnlyBindingError:
                _resolve_logger().debug(
                    "read-only startup hook unavailable",
                    context={
                        "interface": getattr(interface_cls, "__name__", None),
                        "has_parent": manager_cls is not None,
                        "has_model": model is not None,
                    },
                )

        return (_sync,)

    def get_system_checks(
        self,
        interface_cls: type["OrmInterfaceBase[Any]"],
    ) -> tuple[Callable[[], list[Warning]], ...]:
        """
        Provide a system check function that validates the read-only model schema against the database.

        Parameters:
            interface_cls (type[OrmInterfaceBase[Any]]): The read-only interface class whose binding (parent manager and model) will be inspected.

        Returns:
            tuple[Callable[[], list[Warning]], ...]: A tuple containing a single callable. When invoked, the callable returns a list of `Warning` objects produced by `ensure_schema_is_up_to_date` if both the parent manager and model are present; otherwise it returns an empty list.
        """

        def _check() -> list[Warning]:
            """
            Run a read-only schema validation for the enclosing interface and return any warnings.

            Returns:
                list[Warning]: A list of Django `Warning` objects describing schema problems; empty if no warnings were produced or if the interface lacks manager or model metadata.
            """
            manager_cls = getattr(interface_cls, "_parent_class", None)
            model = getattr(interface_cls, "_model", None)
            if manager_cls is None or model is None:
                return []
            return self.ensure_schema_is_up_to_date(
                interface_cls,
                manager_cls,
                model,
            )

        return (_check,)
