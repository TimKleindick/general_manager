"""Capabilities that power ReadOnlyInterface behavior."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, Type, cast, ClassVar

from django.core.checks import Warning
from django.db import (
    IntegrityError,
    connection as django_connection,
    models,
    transaction as django_transaction,
)

from general_manager.interface.base_interface import interfaceBaseClass
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
    from general_manager.interface.capabilities import read_only as read_only_package

    patched = getattr(read_only_package, "logger", None)
    return patched or logger


if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import (
        OrmInterfaceBase,
    )
    from general_manager.interface.interfaces.read_only import (
        ReadOnlyInterface,
    )
    from general_manager.manager.general_manager import GeneralManager


class ReadOnlyManagementCapability(BaseCapability):
    """Provide schema verification and data-sync behavior for read-only interfaces."""

    name: ClassVar[CapabilityName] = "read_only_management"

    def get_unique_fields(self, model: Type[models.Model]) -> set[str]:
        """
        Collect unique field names from the model metadata, tolerating incomplete
        metadata objects that appear in tests or lightweight stand-ins.
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
        payload_snapshot = {
            "manager": manager_cls.__name__,
            "model": getattr(model, "__name__", str(model)),
        }

        def _perform() -> list[Warning]:
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
                with db_connection.cursor() as cursor:
                    tables = db_connection.introspection.table_names(cursor)
                return table_name in tables

            def compare_model_to_table(
                model_arg: Type[models.Model], table: str
            ) -> tuple[list[str], list[str]]:
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
        connection=None,
        transaction=None,
        integrity_error=None,
        json_module=None,
        logger_instance=None,
        unique_fields: set[str] | None = None,
        schema_validated: bool = False,
    ) -> None:
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
        """Expose a startup hook that synchronizes read-only data."""

        def _sync() -> None:
            manager_cls = getattr(interface_cls, "_parent_class", None)
            model = getattr(interface_cls, "_model", None)
            if manager_cls is None or model is None:
                _resolve_logger().debug(
                    "read-only startup hook unavailable",
                    context={
                        "interface": getattr(interface_cls, "__name__", None),
                        "has_parent": manager_cls is not None,
                        "has_model": model is not None,
                    },
                )
                return
            self.sync_data(interface_cls)

        manager_cls = getattr(interface_cls, "_parent_class", None)
        model = getattr(interface_cls, "_model", None)
        if manager_cls is None or model is None:
            _resolve_logger().debug(
                "read-only startup hook registration skipped",
                context={
                    "interface": getattr(interface_cls, "__name__", None),
                    "has_parent": manager_cls is not None,
                    "has_model": model is not None,
                },
            )
            return tuple()

        return (_sync,)

    def get_system_checks(
        self,
        interface_cls: type["OrmInterfaceBase[Any]"],
    ) -> tuple[Callable[[], list[Warning]], ...]:
        """Expose a system check ensuring the read-only schema is current."""

        def _check() -> list[Warning]:
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
