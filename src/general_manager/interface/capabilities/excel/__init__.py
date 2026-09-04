"""Capabilities for Excel-backed interfaces."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, ClassVar, cast

from django.core.checks import Warning

from general_manager.bucket.excel_bucket import ExcelBucket
from general_manager.cache.dependency_index import (
    begin_dependency_data_change,
    drain_invalidated_cache_keys_for_graphql_rewarm,
    end_dependency_data_change,
    is_dependency_data_change_active,
    invalidate_manager_cache_for_value_changes,
    invalidate_manager_cache,
    record_invalidated_cache_keys_for_graphql_rewarm,
)
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.excel import (
    ExcelConfigurationError,
    ExcelError,
    ExcelField,
    ExcelMeta,
    ExcelMutationPayloadError,
    ExcelRowSnapshot,
    ExcelStructureError,
    ExcelSyncDelta,
    ExcelWriteConflictError,
    build_excel_meta,
)
from general_manager.interface.excel_store import (
    DEFAULT_EXCEL_STORE,
    row_fingerprint,
)
from general_manager.interface.excel_workbook import (
    ExcelWorkbookAdapter,
    ExcelWorkbookRecord,
    WorkbookFingerprint,
    workbook_fingerprint,
)
from general_manager.logging import get_logger
from general_manager.manager.input import Input

if TYPE_CHECKING:
    from general_manager.interface.interfaces.excel import ExcelInterface

logger = get_logger("interface.excel")

EXCEL_SYSTEM_CHECK_ID = "general_manager.excel.W001"


class ExcelReadCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "read"

    def get_data(self, interface_instance: "ExcelInterface") -> dict[str, Any]:
        """Return the mirrored Excel row for an interface instance."""
        interface_cls = type(interface_instance)
        key = interface_instance.identification[interface_cls.excel_meta.key]
        mirror = DEFAULT_EXCEL_STORE.mirror_for(interface_cls)
        try:
            interface_cls.sync_from_excel()
        except ExcelStructureError:
            if key not in mirror.rows:
                raise
        snapshot = mirror.rows[key]
        _set_observed_snapshot(interface_instance, snapshot)
        return dict(snapshot.values)

    def get_attribute_types(
        self,
        interface_cls: type["ExcelInterface"],
    ) -> dict[str, dict[str, Any]]:
        """Describe the declared Excel fields for manager metadata."""
        return {
            name: {
                "type": field.python_type,
                "default": field.default,
                "is_editable": field.editable,
                "is_required": field.required,
                "is_derived": False,
            }
            for name, field in interface_cls.excel_fields.items()
        }

    def get_attributes(self, interface_cls: type["ExcelInterface"]) -> dict[str, Any]:
        """Build lazy attribute readers for the declared Excel fields."""
        return {
            name: lambda interface_instance, name=name: interface_instance.get_data()[
                name
            ]
            for name in interface_cls.excel_fields
        }

    def get_field_type(
        self,
        interface_cls: type["ExcelInterface"],
        field_name: str,
    ) -> type:
        """Return the Python type declared for an Excel field."""
        return interface_cls.excel_fields[field_name].python_type


class ExcelQueryCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "query"

    def filter(
        self,
        interface_cls: type["ExcelInterface"],
        **kwargs: Any,
    ) -> ExcelBucket[Any]:
        """Return an Excel bucket filtered by the supplied lookups."""
        return ExcelBucket(interface_cls._parent_class, interface_cls).filter(**kwargs)

    def exclude(
        self,
        interface_cls: type["ExcelInterface"],
        **kwargs: Any,
    ) -> ExcelBucket[Any]:
        """Return an Excel bucket excluding the supplied lookups."""
        return ExcelBucket(interface_cls._parent_class, interface_cls).exclude(**kwargs)

    def all(self, interface_cls: type["ExcelInterface"]) -> ExcelBucket[Any]:
        """Return a bucket containing all rows for the interface."""
        return ExcelBucket(interface_cls._parent_class, interface_cls)


class ExcelSyncCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "excel_sync"

    def get_startup_hooks(
        self,
        interface_cls: type["ExcelInterface"],
    ) -> tuple[Callable[[], None], ...]:
        """Return startup hooks that warm normalized Excel interfaces."""
        if not _is_normalized_excel_interface(interface_cls):
            return tuple()

        def _sync() -> None:
            try:
                self.sync_from_excel(interface_cls, force=True)
            except (ExcelError, OSError) as error:
                logger.warning(
                    "Excel startup sync failed.",
                    context=_excel_log_context(interface_cls, error),
                )
            except Exception as error:
                logger.exception(
                    "Excel startup sync failed.",
                    context=_excel_log_context(interface_cls, error),
                )

        return (_sync,)

    def get_system_checks(
        self,
        interface_cls: type["ExcelInterface"],
    ) -> tuple[Callable[[], list[Warning]], ...]:
        """Return Django checks for normalized Excel interfaces."""
        if not _is_normalized_excel_interface(interface_cls):
            return tuple()

        def _check() -> list[Warning]:
            try:
                self.validate_workbook_structure(interface_cls)
            except Exception as error:  # noqa: BLE001
                # Django system checks must report configuration issues, not abort.
                return [_excel_system_check_warning(interface_cls, error)]
            return []

        return (_check,)

    def sync_from_excel(
        self,
        interface_cls: type["ExcelInterface"],
        *,
        force: bool = False,
    ) -> ExcelSyncDelta:
        """Refresh the shared mirror and invalidate dependencies for changes."""
        lock = DEFAULT_EXCEL_STORE.lock_for(interface_cls.excel_meta.workbook)
        with lock:
            adapter = ExcelWorkbookAdapter(interface_cls.excel_meta)
            mirror = DEFAULT_EXCEL_STORE.mirror_for(interface_cls)
            previous_rows = mirror.rows
            previous_fingerprint = mirror.fingerprint
            started_dependency_data_change = False
            owns_outermost_dependency_data_change = False
            try:
                if (
                    not force
                    and mirror.fingerprint
                    == workbook_fingerprint(interface_cls.excel_meta.workbook)
                    and mirror.structure_error is None
                ):
                    return ExcelSyncDelta(created=(), updated=(), deleted=())
                rows = adapter.read_rows()
                _validate_declared_read_headers(interface_cls, rows.headers)
                parsed_rows = self._parse_rows(interface_cls, rows.records)
                baseline_missing = mirror.fingerprint is None
                has_delta = baseline_missing or _rows_changed(mirror.rows, parsed_rows)
                if has_delta:
                    owns_outermost_dependency_data_change = (
                        not is_dependency_data_change_active()
                    )
                    begin_dependency_data_change()
                    started_dependency_data_change = True
                try:
                    delta = DEFAULT_EXCEL_STORE.replace(
                        interface_cls,
                        rows=parsed_rows,
                        fingerprint=rows.fingerprint,
                        publish=False,
                    )
                    if has_delta:
                        invalidated_cache_keys = (
                            _invalidate_dependency_cache_from_delta(
                                interface_cls,
                                delta,
                            )
                        )
                        if baseline_missing:
                            invalidated_cache_keys.update(
                                invalidate_manager_cache(
                                    interface_cls._parent_class.__name__
                                )
                            )
                        if invalidated_cache_keys:
                            record_invalidated_cache_keys_for_graphql_rewarm(
                                invalidated_cache_keys,
                            )
                finally:
                    if started_dependency_data_change:
                        end_dependency_data_change()
            except Exception as error:
                # Keep the baseline so a failed invalidation is retried even when
                # the mirror cache is unavailable and the workbook is unchanged.
                mirror.rows = previous_rows
                mirror.fingerprint = previous_fingerprint
                DEFAULT_EXCEL_STORE.set_error(interface_cls, error)
                logger.exception(
                    "Excel synchronization failed; previous snapshot retained."
                )
                raise
            DEFAULT_EXCEL_STORE.publish(interface_cls)
            if owns_outermost_dependency_data_change:
                _enqueue_graphql_rewarm_for_invalidated_keys(
                    drain_invalidated_cache_keys_for_graphql_rewarm()
                )
            return delta

    def validate_workbook_structure(
        self,
        interface_cls: type["ExcelInterface"],
    ) -> None:
        """Validate workbook headers and row structure without publishing data."""
        rows = ExcelWorkbookAdapter(interface_cls.excel_meta).read_rows()
        _validate_declared_read_headers(interface_cls, rows.headers)
        parsed_rows: set[Any] = set()
        for record in rows.records:
            parsed = self._parse_record(interface_cls, record.values)
            key = parsed[interface_cls.excel_meta.key]
            if key in parsed_rows:
                raise ExcelStructureError.duplicate_key(key)
            parsed_rows.add(key)

    def _parse_rows(
        self,
        interface_cls: type["ExcelInterface"],
        records: Iterable[ExcelWorkbookRecord],
    ) -> dict[Any, ExcelRowSnapshot]:
        parsed_rows: dict[Any, ExcelRowSnapshot] = {}
        for record in records:
            parsed = self._parse_record(interface_cls, record.values)
            key = parsed[interface_cls.excel_meta.key]
            if key in parsed_rows:
                raise ExcelStructureError.duplicate_key(key)
            parsed_rows[key] = ExcelRowSnapshot(
                key=key,
                values=parsed,
                fingerprint=row_fingerprint(parsed),
            )
        return parsed_rows

    def _parse_record(
        self,
        interface_cls: type["ExcelInterface"],
        raw: dict[str, Any],
    ) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for name, field in interface_cls.excel_fields.items():
            header = next(
                (
                    candidate
                    for candidate in field.header_candidates(name)
                    if candidate in raw
                ),
                None,
            )
            if header is None:
                raise ExcelStructureError.missing_column(name)
            if name == interface_cls.excel_meta.key and raw[header] in (None, ""):
                raise ExcelStructureError.blank_key()
            parsed[name] = field.parse(raw[header])
        key = parsed[interface_cls.excel_meta.key]
        if key in (None, ""):
            raise ExcelStructureError.blank_key()
        return parsed


class ExcelCreateCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "create"

    def create(
        self,
        interface_cls: type["ExcelInterface"],
        *,
        creator_id: int | None = None,
        history_comment: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Validate and append a new row to the Excel workbook."""
        del creator_id, history_comment
        _validate_payload_fields(interface_cls, kwargs)
        key, values = _parse_create_values(interface_cls, kwargs)
        lock = DEFAULT_EXCEL_STORE.lock_for(interface_cls.excel_meta.workbook)
        with lock:
            interface_cls.sync_from_excel(force=True)
            mirror = DEFAULT_EXCEL_STORE.mirror_for(interface_cls)
            if key in mirror.rows:
                raise ExcelWriteConflictError.existing_key(key)
            _validate_declared_write_headers(interface_cls)
            _write_adapter(interface_cls).append_row(values)
            interface_cls.sync_from_excel(force=True)
        return {interface_cls.excel_meta.key: key}


class ExcelUpdateCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "update"

    def update(
        self,
        interface_instance: "ExcelInterface",
        *,
        creator_id: int | None = None,
        history_comment: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Validate and write changes to an existing Excel row."""
        del creator_id, history_comment
        interface_cls = type(interface_instance)
        key = interface_instance.identification[interface_cls.excel_meta.key]
        workbook_key = _dump_key_value(interface_cls, key)
        _validate_payload_fields(interface_cls, kwargs)
        if interface_cls.excel_meta.key in kwargs:
            raise ExcelMutationPayloadError.cannot_update_key(
                interface_cls.excel_meta.key
            )
        values = _parse_update_values(interface_cls, kwargs)
        lock = DEFAULT_EXCEL_STORE.lock_for(interface_cls.excel_meta.workbook)
        with lock:
            expected_fingerprint = _refresh_for_write(interface_instance)
            _validate_declared_write_headers(interface_cls)
            if values:
                _write_adapter(interface_cls).update_row(
                    workbook_key,
                    values,
                    expected_fingerprint=expected_fingerprint,
                )
            _set_observed_snapshot(
                interface_instance,
                _synced_snapshot(interface_cls, key),
            )
        return {interface_cls.excel_meta.key: key}


class ExcelDeleteCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "delete"

    def delete(
        self,
        interface_instance: "ExcelInterface",
        *,
        creator_id: int | None = None,
        history_comment: str | None = None,
    ) -> dict[str, Any]:
        """Delete an existing Excel row after conflict checks."""
        del creator_id, history_comment
        interface_cls = type(interface_instance)
        key = interface_instance.identification[interface_cls.excel_meta.key]
        workbook_key = _dump_key_value(interface_cls, key)
        lock = DEFAULT_EXCEL_STORE.lock_for(interface_cls.excel_meta.workbook)
        with lock:
            expected_fingerprint = _refresh_for_write(interface_instance)
            _validate_declared_write_headers(interface_cls)
            _write_adapter(interface_cls).delete_row(
                workbook_key,
                expected_fingerprint=expected_fingerprint,
            )
            interface_cls.sync_from_excel(force=True)
            _set_observed_snapshot(interface_instance, None)
        return {interface_cls.excel_meta.key: key}


class ExcelLifecycleCapability(BaseCapability):
    """Normalize Excel field and Meta declarations."""

    name: ClassVar[CapabilityName] = "excel_lifecycle"

    def pre_create(
        self,
        *,
        name: str,
        attrs: dict[str, Any],
        interface: type["ExcelInterface"],
    ) -> tuple[dict[str, Any], type["ExcelInterface"], None]:
        """Prepare and validate a pending Excel-backed manager creation."""
        excel_fields: dict[str, ExcelField[Any]] = {}
        for base in reversed(interface.__mro__):
            for key, value in vars(base).items():
                if isinstance(value, ExcelField):
                    excel_fields[key] = value
        excel_meta = build_excel_meta(getattr(interface, "Meta", None))
        if excel_meta.key not in excel_fields:
            raise ExcelConfigurationError.key_not_excel_field()
        key_field = excel_fields[excel_meta.key]
        input_fields = {excel_meta.key: Input(key_field.python_type)}
        attrs["_interface_type"] = interface._interface_type
        interface_cls = cast(
            type["ExcelInterface"],
            type(interface.__name__, (interface,), {}),
        )
        interface_cls.__module__ = interface.__module__
        interface_cls.__qualname__ = interface.__qualname__
        interface_cls.excel_meta = excel_meta
        interface_cls.excel_fields = excel_fields
        interface_cls.input_fields = input_fields
        attrs["Interface"] = interface_cls
        return attrs, interface_cls, None

    def post_create(
        self,
        *,
        new_class: type,
        interface_class: type["ExcelInterface"],
        model: None = None,
    ) -> None:
        """Attach the parent manager and its Excel synchronization helper."""
        interface_class._parent_class = new_class

        def sync_excel(manager_cls: type) -> ExcelSyncDelta:
            del manager_cls
            return interface_class.sync_from_excel(force=True)

        new_class.sync_excel = classmethod(sync_excel)  # type: ignore[attr-defined]


def _invalidate_dependency_cache_from_delta(
    interface_cls: type["ExcelInterface"],
    delta: ExcelSyncDelta,
) -> set[str]:
    if not (delta.created or delta.updated or delta.deleted):
        return set()
    manager_cls = getattr(interface_cls, "_parent_class", None)
    if manager_cls is None:
        return set()
    key = interface_cls.excel_meta.key
    changes: list[
        tuple[
            Mapping[str, Any],
            Mapping[str, Any],
            Mapping[str, Any],
        ]
    ] = []
    for row in delta.created:
        changes.append(({}, row.values, {key: row.key}))
    for old, new in delta.updated:
        changes.append((old.values, new.values, {key: new.key}))
    for row in delta.deleted:
        changes.append((row.values, {}, {key: row.key}))
    return invalidate_manager_cache_for_value_changes(manager_cls, changes)


def _enqueue_graphql_rewarm_for_invalidated_keys(cache_keys: Iterable[str]) -> None:
    keys = tuple(dict.fromkeys(cache_keys))
    if not keys:
        return
    try:
        from general_manager.api.graphql_warmup import (
            enqueue_graphql_recipe_warmup,
        )

        enqueue_graphql_recipe_warmup(keys)
    except Exception:
        logger.exception("GraphQL warm-up requeue failed.")


def _rows_changed(
    old_rows: Mapping[Any, ExcelRowSnapshot],
    new_rows: Mapping[Any, ExcelRowSnapshot],
) -> bool:
    if old_rows.keys() != new_rows.keys():
        return True
    return any(
        old_rows[key].fingerprint != row.fingerprint for key, row in new_rows.items()
    )


def _parse_create_values(
    interface_cls: type["ExcelInterface"],
    payload: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    parsed: dict[str, Any] = {}
    dumped: dict[str, Any] = {}
    for name, field in interface_cls.excel_fields.items():
        parsed_value = field.parse(payload.get(name))
        parsed[name] = parsed_value
        dumped[_field_header(name, field)] = field.dump(parsed_value)
    key = parsed[interface_cls.excel_meta.key]
    if key in (None, ""):
        raise ExcelStructureError.blank_key()
    return key, dumped


def _validate_payload_fields(
    interface_cls: type["ExcelInterface"],
    payload: Mapping[str, Any],
) -> None:
    unknown = sorted(set(payload) - set(interface_cls.excel_fields))
    if unknown:
        raise ExcelMutationPayloadError.unknown_field(unknown[0])


def _validate_declared_write_headers(interface_cls: type["ExcelInterface"]) -> None:
    rows = ExcelWorkbookAdapter(interface_cls.excel_meta).read_rows()
    headers = set(rows.headers)
    for name, field in interface_cls.excel_fields.items():
        if _field_header(name, field) not in headers:
            raise ExcelStructureError.missing_column(name)


def _is_normalized_excel_interface(interface_cls: type["ExcelInterface"]) -> bool:
    return hasattr(interface_cls, "excel_meta") and hasattr(
        interface_cls, "excel_fields"
    )


def _validate_declared_read_headers(
    interface_cls: type["ExcelInterface"],
    headers: Mapping[str, object],
) -> None:
    header_names = set(headers)
    for name, field in interface_cls.excel_fields.items():
        candidates = field.header_candidates(name)
        if not any(candidate in header_names for candidate in candidates):
            raise _missing_declared_header_error(name, candidates)


def _format_header_candidates(candidates: tuple[str, ...]) -> str:
    if len(candidates) == 1:
        return f"header {candidates[0]!r}"
    return f"one of {', '.join(repr(candidate) for candidate in candidates)}"


def _missing_declared_header_error(
    field_name: str,
    candidates: tuple[str, ...],
) -> ExcelStructureError:
    return ExcelStructureError(
        "Missing Excel column for field "
        f"{field_name!r}; expected {_format_header_candidates(candidates)}."
    )


def _parse_update_values(
    interface_cls: type["ExcelInterface"],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, field in interface_cls.excel_fields.items():
        if name not in payload:
            continue
        parsed_value = field.parse(payload[name])
        if name == interface_cls.excel_meta.key and parsed_value in (None, ""):
            raise ExcelStructureError.blank_key()
        values[_field_header(name, field)] = field.dump(parsed_value)
    return values


def _field_header(name: str, field: ExcelField[Any]) -> str:
    return field.header or name


def _dump_key_value(interface_cls: type["ExcelInterface"], key: Any) -> Any:
    key_field = interface_cls.excel_fields[interface_cls.excel_meta.key]
    return key_field.dump(key)


def _write_adapter(interface_cls: type["ExcelInterface"]) -> ExcelWorkbookAdapter:
    meta = interface_cls.excel_meta
    key_header = _field_header(
        meta.key,
        interface_cls.excel_fields[meta.key],
    )
    return ExcelWorkbookAdapter(
        ExcelMeta(
            workbook=meta.workbook,
            sheet=meta.sheet,
            key=key_header,
            table=meta.table,
            header_row=meta.header_row,
        )
    )


def _excel_system_check_warning(
    interface_cls: type["ExcelInterface"],
    error: Exception,
) -> Warning:
    return Warning(
        str(error),
        hint=(
            f"Check ExcelInterface {interface_cls.__module__}."
            f"{interface_cls.__qualname__} workbook configuration."
        ),
        obj=interface_cls,
        id=EXCEL_SYSTEM_CHECK_ID,
    )


def _excel_log_context(
    interface_cls: type["ExcelInterface"],
    error: Exception,
) -> dict[str, object]:
    meta = interface_cls.excel_meta
    return {
        "interface": f"{interface_cls.__module__}.{interface_cls.__qualname__}",
        "workbook": meta.workbook,
        "sheet": meta.sheet,
        "table": meta.table,
        "header_row": meta.header_row,
        "error": str(error),
    }


def _synced_snapshot(
    interface_cls: type["ExcelInterface"],
    key: Any,
) -> ExcelRowSnapshot:
    interface_cls.sync_from_excel(force=True)
    mirror = DEFAULT_EXCEL_STORE.mirror_for(interface_cls)
    return mirror.rows[key]


def _refresh_for_write(interface_instance: "ExcelInterface") -> WorkbookFingerprint:
    interface_cls = type(interface_instance)
    key = interface_instance.identification[interface_cls.excel_meta.key]
    mirror = DEFAULT_EXCEL_STORE.mirror_for(interface_cls)
    expected_snapshot = interface_instance._excel_observed_snapshot
    interface_cls.sync_from_excel()
    current_snapshot = mirror.rows.get(key)
    if current_snapshot is None:
        _set_observed_snapshot(interface_instance, None)
        raise ExcelWriteConflictError.missing_row(key)
    _set_observed_snapshot(interface_instance, current_snapshot)
    if (
        expected_snapshot is not None
        and expected_snapshot.fingerprint != current_snapshot.fingerprint
    ):
        raise ExcelWriteConflictError.changed_row(key)
    return cast(WorkbookFingerprint, mirror.fingerprint)


def _set_observed_snapshot(
    interface_instance: "ExcelInterface",
    snapshot: ExcelRowSnapshot | None,
) -> None:
    interface_instance._excel_observed_snapshot = snapshot
