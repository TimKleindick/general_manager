"""Capabilities that power ReadOnlyInterface behavior."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Optional, Type, cast

from django.core.checks import Warning
from django.db import (
    IntegrityError,
    connection as django_connection,
    models,
    transaction as django_transaction,
)

from general_manager.measurement.measurement_field import MeasurementField
from general_manager.interface.utils.models import GeneralManagerBasisModel
from general_manager.interface.utils.errors import (
    InvalidReadOnlyDataFormatError,
    InvalidReadOnlyDataTypeError,
    MissingReadOnlyDataError,
    MissingReadOnlyBindingError,
    MissingUniqueFieldError,
    ReadOnlyRelationLookupError,
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

    @staticmethod
    def _related_readonly_interfaces(
        interface_cls: type["OrmInterfaceBase[Any]"],
    ) -> set[type["OrmInterfaceBase[Any]"]]:
        """
        Discover read-only interface classes referenced by concrete relation fields on the given interface's model.

        Inspects the model's fields and collects distinct read-only interface classes referenced by relation fields (excluding the provided interface).

        Parameters:
            interface_cls (type): The ORM interface class whose model will be inspected.

        Returns:
            set[type]: A set of read-only interface classes referenced from the model's relation fields (excluding `interface_cls`).
        """

        model = getattr(interface_cls, "_model", None)
        opts = getattr(model, "_meta", None)
        if not opts or not hasattr(opts, "get_fields"):
            return set()

        related: set[type["OrmInterfaceBase[Any]"]] = set()
        for field in opts.get_fields():
            if not getattr(field, "is_relation", False) or getattr(
                field, "auto_created", False
            ):
                continue
            remote_model = getattr(getattr(field, "remote_field", None), "model", None)
            manager_cls = getattr(remote_model, "_general_manager_class", None)
            candidate = getattr(manager_cls, "Interface", None)
            if (
                isinstance(candidate, type)
                and candidate is not interface_cls
                and getattr(candidate, "_interface_type", None) == "readonly"
            ):
                related.add(candidate)
        return related

    def get_startup_hook_dependency_resolver(
        self, interface_cls: type["OrmInterfaceBase[Any]"]
    ) -> Callable[[type[object]], set[type[object]]]:
        """
        Return a resolver function that identifies read-only interfaces which must run before a given interface's startup hook.

        Parameters:
            interface_cls (type[OrmInterfaceBase[Any]]): The interface class for which to obtain a startup-hook dependency resolver.

        Returns:
            Callable[[type[object]], Set[type[object]]]: A callable that, when invoked with an interface class, returns a set of read-only interface classes that should be executed prior to that interface's startup hook.
        """

        return cast(
            Callable[[type[object]], set[type[object]]],
            self._related_readonly_interfaces,
        )

    def get_unique_fields(self, model: Type[models.Model]) -> set[str]:
        """
        Gather candidate unique field names declared on the Django model, remapping measurement-backed fields to their public attribute names.

        Includes fields marked `unique=True`, fields listed in `unique_together` (or equivalent tuple/list/set entries), and fields referenced by `UniqueConstraint` definitions. Excludes the primary key named "id". If the model has no `_meta`, returns an empty set.

        Returns:
            set[str]: A set of unique field names (with MeasurementField-backed value attributes remapped to their wrapper attribute names).
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

        measurement_fields = {
            name: field
            for name, field in vars(model).items()
            if isinstance(field, MeasurementField)
        }
        if measurement_fields:
            value_attr_map: dict[str, list[str]] = {}
            for name, field in measurement_fields.items():
                value_attr = field.value_attr
                value_attr_map.setdefault(value_attr, []).append(name)
            duplicates = {
                value_attr: names
                for value_attr, names in value_attr_map.items()
                if len(names) > 1
            }
            if duplicates:
                raise ValueError(
                    "Duplicate MeasurementField value_attr mappings detected: "
                    + ", ".join(
                        f"{value_attr} -> {sorted(names)}"
                        for value_attr, names in sorted(duplicates.items())
                    )
                )
        reverse_measurement_map = {
            field.value_attr: name for name, field in measurement_fields.items()
        }

        remapped_unique_fields = {
            reverse_measurement_map.get(field, field) for field in unique_fields
        }
        return remapped_unique_fields

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
                local_fields = (
                    getattr(model_opts, "local_concrete_fields", None)
                    or getattr(model_opts, "local_fields", [])
                    or []
                )
                model_cols: set[str] = set()
                for field in local_fields:
                    if hasattr(field, "concrete") and not field.concrete:
                        continue
                    column = cast(str | None, getattr(field, "column", None))
                    if column:
                        model_cols.add(column)
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

        in_progress: set[type["OrmInterfaceBase[Any]"]] = getattr(
            self, "_sync_stack", set()
        )
        if interface_cls in in_progress:
            return None
        in_progress.add(interface_cls)
        self._sync_stack = in_progress

        def _perform() -> None:
            """
            Synchronize the bound read-only JSON payload into the Django model so the table's active records match the incoming data.

            Parses the interface's bound JSON data, resolves related objects, and applies creates, updates, and deactivations using the configured unique fields. Only editable model fields are written; many-to-many assignments are applied post-save. If schema validation is requested and the schema is out of date, the sync aborts without making changes.

            Raises:
                MissingReadOnlyDataError: if the parent interface has no `_data` attribute.
                InvalidReadOnlyDataTypeError: if the bound data is neither a JSON string nor a list.
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

            relation_fields = {
                f.name: f
                for f in getattr(model_opts, "get_fields", lambda: [])()
                if getattr(f, "is_relation", False)
                and not getattr(f, "auto_created", False)
            }

            related_interfaces: list[type["OrmInterfaceBase[Any]"]] = []
            for field in relation_fields.values():
                remote_model = getattr(
                    getattr(field, "remote_field", None), "model", None
                )
                general_manager_cls = getattr(
                    remote_model, "_general_manager_class", None
                )
                candidate_interface = getattr(general_manager_cls, "Interface", None)
                if (
                    candidate_interface
                    and candidate_interface is not interface_cls
                    and candidate_interface not in related_interfaces
                    and isinstance(candidate_interface, type)
                    and getattr(candidate_interface, "_interface_type", None)
                    == "readonly"
                ):
                    related_interfaces.append(candidate_interface)

            for related_interface in related_interfaces:
                related_capability = cast(
                    ReadOnlyManagementCapability,
                    related_interface.require_capability(
                        "read_only_management",
                        expected_type=ReadOnlyManagementCapability,
                    ),
                )
                related_capability.sync_data(
                    related_interface,
                    connection=db_connection,
                    transaction=db_transaction,
                    integrity_error=integrity_error_cls,
                    json_module=json_lib,
                    logger_instance=active_logger,
                    unique_fields=None,
                    schema_validated=schema_validated,
                )

            def _resolve_to_instance(
                field_name: str,
                remote_model: Type[models.Model],
                raw_value: object,
                idx: int,
            ) -> models.Model | object:
                """
                Resolve a related model instance from a lookup dict or return the original value.

                Parameters:
                    field_name (str): Name of the relation field used for logging and error context.
                    remote_model (Type[models.Model]): The related Django model to query.
                    raw_value (object): A value from the payload; if a dict it is treated as a filter lookup for `remote_model`.
                    idx (int): Index of the current item in the incoming payload, used for logging context.

                Returns:
                    models.Model | object: The resolved model instance when `raw_value` is a dict that matches exactly one record;
                    otherwise returns `raw_value` unchanged.

                Raises:
                    ReadOnlyRelationLookupError: If the lookup dict results in zero or multiple matches.
                """
                if not isinstance(raw_value, dict):
                    return raw_value
                lookup = cast(dict[str, object], raw_value)

                def _flatten_lookup(
                    lookup_dict: dict[str, object],
                ) -> dict[str, object]:
                    """
                    Flatten a nested dictionary into a single-level dict by joining nested keys with '__'.

                    Parameters:
                        lookup_dict (dict[str, object]): Nested mapping whose leaf values will be preserved. Keys are converted to strings before joining.

                    Returns:
                        dict[str, object]: A flat mapping where each key is the path of nested keys joined with '__' and each value is the corresponding leaf value.
                    """
                    flattened: dict[str, object] = {}

                    def _walk(prefix: str, value: object) -> None:
                        """
                        Recursively flattens a nested mapping into keys joined by '__' and stores results in the outer `flattened` mapping.

                        This helper traverses `value`; when `value` is a dict it recurses into each item, appending the child key to `prefix` with a '__' separator. When `value` is not a dict, it assigns `flattened[prefix] = value`. The function mutates the surrounding `flattened` mapping and returns None.

                        Parameters:
                            prefix (str): Current key path used as the target key in `flattened`.
                            value (object): The value or nested mapping to flatten.
                        """
                        if isinstance(value, dict):
                            for key, child in value.items():
                                _walk(f"{prefix}__{key}", child)
                            return
                        flattened[prefix] = value

                    for key, value in lookup_dict.items():
                        _walk(str(key), value)
                    return flattened

                lookup = _flatten_lookup(lookup)
                qs = remote_model.objects.filter(**lookup)
                matches = list(qs[:2])
                match_count = len(matches)
                if match_count != 1:
                    match_count = qs.count() if match_count == 2 else match_count
                    active_logger.warning(
                        "readonly relation lookup failed",
                        context={
                            "manager": parent_class.__name__,
                            "model": model.__name__,
                            "field": field_name,
                            "lookup": lookup,
                            "matches": match_count,
                            "index": idx,
                        },
                    )
                    raise ReadOnlyRelationLookupError(
                        parent_class.__name__, field_name, match_count, lookup
                    )
                return matches[0]

            def _resolve_many_to_many(
                field_name: str,
                remote_model: Type[models.Model],
                raw_value: object,
                idx: int,
            ) -> list[models.Model | object]:
                """
                Resolve a many-to-many field payload into a list of related model instances or lookup identifiers.

                Parameters:
                        field_name (str): Name of the many-to-many field being resolved.
                        remote_model (Type[models.Model]): The related model class for the field.
                        raw_value (object): The incoming payload for the field; may be None or a list of entries (each entry is a lookup dict or identifier).
                        idx (int): Index of the parent item in the incoming payload (used for error context).

                Returns:
                        list[models.Model | object]: A list containing either resolved related model instances or the original lookup identifiers for each entry in the payload. Returns an empty list when `raw_value` is None.

                Raises:
                        InvalidReadOnlyDataFormatError: If `raw_value` is present but is not a list.
                """
                if raw_value is None:
                    return []
                if not isinstance(raw_value, list):
                    raise InvalidReadOnlyDataFormatError()
                resolved: list[models.Model | object] = []
                for entry in raw_value:
                    resolved.append(
                        _resolve_to_instance(field_name, remote_model, entry, idx)
                    )
                return resolved

            def _resolve_relations(
                data: dict[str, Any],
                idx: int,
            ) -> tuple[dict[str, Any], dict[str, list[models.Model | object]]]:
                """
                Resolve relation payloads in a single data item into model instances and collect many-to-many assignments for post-save processing.

                Parameters:
                        data (dict[str, Any]): Incoming record payload; keys may include relation field names whose values are lookup dicts or lists.
                        idx (int): Index of the record within the incoming payload used to annotate errors and logs.

                Returns:
                        tuple:
                                - resolved (dict[str, Any]): A copy of `data` where foreign key and one-to-one relation values are replaced with resolved model instances and many-to-many fields are removed.
                                - m2m_assignments (dict[str, list[models.Model | object]]): Mapping of many-to-many field names to lists of resolved related model instances to assign after the main instance is saved.
                """
                resolved = dict(data)
                m2m_assignments: dict[str, list[models.Model | object]] = {}
                for field_name, field in relation_fields.items():
                    if field_name not in data:
                        continue
                    remote_model = cast(Type[models.Model], field.remote_field.model)
                    if isinstance(field, models.ManyToManyField):
                        resolved.pop(field_name, None)
                        m2m_assignments[field_name] = _resolve_many_to_many(
                            field_name,
                            remote_model,
                            data[field_name],
                            idx,
                        )
                    elif isinstance(field, (models.ForeignKey, models.OneToOneField)):
                        resolved[field_name] = _resolve_to_instance(
                            field_name,
                            remote_model,
                            data[field_name],
                            idx,
                        )
                return resolved, m2m_assignments

            with db_transaction.atomic():
                json_unique_values: set[tuple[Any, ...]] = set()
                processed_pks: list[Any] = []

                for idx, data in enumerate(data_list):
                    resolved_data, m2m_assignments = _resolve_relations(data, idx)
                    try:
                        lookup = {
                            field: resolved_data[field] for field in unique_field_order
                        }
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
                        if "is_active" in allowed_fields:
                            resolved_data.setdefault("is_active", True)
                        create_kwargs = {
                            k: v
                            for k, v in resolved_data.items()
                            if k in allowed_fields
                        }
                        try:
                            instance = cast(
                                GeneralManagerBasisModel,
                                manager.create(**create_kwargs),
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
                    for field_name in editable_fields.intersection(
                        resolved_data.keys()
                    ):
                        value = resolved_data[field_name]
                        if getattr(instance, field_name, None) != value:
                            updated = True
                        setattr(instance, field_name, value)
                    if updated and hasattr(instance, "save"):
                        try:
                            instance.save()
                        except Exception as exc:
                            active_logger.warning(
                                "readonly instance save failed",
                                context={
                                    "manager": parent_class.__name__,
                                    "model": model.__name__,
                                    "error": str(exc),
                                },
                            )
                            raise
                    for field_name, related_values in m2m_assignments.items():
                        m2m_manager = getattr(instance, field_name)
                        current_ids = set(
                            m2m_manager.all().values_list("pk", flat=True)
                        )
                        normalized_values = [
                            getattr(obj, "pk", obj) for obj in related_values
                        ]
                        new_ids = set(normalized_values)
                        if current_ids != new_ids:
                            m2m_manager.set(normalized_values)
                            updated = True
                    needs_activation = not getattr(instance, "is_active", True)
                    if updated or needs_activation or is_created:
                        activation_manager = getattr(model, "all_objects", None)
                        if activation_manager is not None and hasattr(
                            activation_manager, "filter"
                        ):
                            activation_manager.filter(
                                pk=getattr(instance, "pk", None)
                            ).update(  # type: ignore[arg-type]
                                is_active=True
                            )
                            if hasattr(instance, "refresh_from_db"):
                                instance.refresh_from_db()
                            elif hasattr(instance, "save"):
                                instance.save()
                        else:
                            instance.is_active = True  # type: ignore[attr-defined]
                            if hasattr(instance, "save"):
                                instance.save()
                        changes["created" if is_created else "updated"].append(instance)
                    processed_pks.append(getattr(instance, "pk", None))

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

                if processed_pks and hasattr(model, "all_objects"):
                    model.all_objects.filter(pk__in=processed_pks).update(  # type: ignore[arg-type]
                        is_active=True
                    )

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

        try:
            return call_with_observability(
                interface_cls,
                operation="read_only.sync_data",
                payload=payload_snapshot,
                func=_perform,
            )
        finally:
            in_progress.discard(interface_cls)

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
