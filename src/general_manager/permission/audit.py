"""Lightweight audit logging hooks for permission evaluations."""

from __future__ import annotations

import atexit
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread, Lock
from typing import Literal, Protocol, TypedDict, cast, runtime_checkable

from django.apps import apps
from django.db import connections, models
from django.utils import timezone


AuditAction = Literal["create", "read", "update", "delete", "mutation"]
type AuditMetadataValue = (
    str
    | int
    | float
    | bool
    | None
    | list[AuditMetadataValue]
    | dict[str, AuditMetadataValue]
)
type AuditMetadata = Mapping[str, AuditMetadataValue]


class SerializedAuditEvent(TypedDict):
    """JSON-compatible representation written by the built-in audit loggers."""

    timestamp: str
    action: AuditAction
    attributes: list[str]
    granted: bool
    bypassed: bool
    manager: str | None
    user_id: str | None
    user: str | None
    permissions: list[str]
    metadata: dict[str, AuditMetadataValue] | None


@dataclass(slots=True)
class PermissionAuditEvent:
    """
    Payload describing a permission evaluation outcome.

    Attributes:
        action: CRUD or mutation action that was evaluated.
        attributes: Attribute names covered by this evaluation, in the order
            the permission check reported them.
        granted: True when the action was permitted.
        user: User object involved in the evaluation. Built-in loggers store
            `str(user.pk)` when a `pk` attribute exists, otherwise `repr(user)`.
        manager: Name of the manager class when applicable.
        permissions: Permission expressions that were considered.
        bypassed: True when the decision relied on a superuser bypass.
        metadata: Optional JSON-compatible context. Built-in file and database
            loggers persist this mapping as-is after copying it to a plain dict.
    """

    action: AuditAction
    attributes: tuple[str, ...]
    granted: bool
    user: object
    manager: str | None
    permissions: tuple[str, ...] = ()
    bypassed: bool = False
    metadata: AuditMetadata | None = None


@runtime_checkable
class AuditLogger(Protocol):
    """Protocol describing the expected behaviour of an audit logger implementation."""

    def record(self, event: PermissionAuditEvent) -> None:
        """Persist or forward a permission audit event."""


class _NoOpAuditLogger:
    """Fallback logger used when no audit logger is configured."""

    __slots__ = ()

    def record(self, _event: PermissionAuditEvent) -> None:
        """Ignore the audit event."""
        return


_NOOP_LOGGER = _NoOpAuditLogger()
_audit_logger: AuditLogger = _NOOP_LOGGER
_SETTINGS_KEY = "GENERAL_MANAGER"
_AUDIT_LOGGER_KEY = "AUDIT_LOGGER"


class InvalidAuditLoggerOptionsError(TypeError):
    """Raised when an AUDIT_LOGGER mapping uses non-mapping options."""

    def __init__(self) -> None:
        super().__init__("AUDIT_LOGGER options must be a mapping.")


def configure_audit_logger(logger: AuditLogger | None) -> None:
    """
    Configure the audit logger used by permission checks.

    Parameters:
        logger: Concrete logger implementation. Passing `None` resets the
            process-global logger to the built-in no-op implementation.
    """
    global _audit_logger
    _audit_logger = logger or _NOOP_LOGGER


def get_audit_logger() -> AuditLogger:
    """
    Return the currently configured audit logger.

    The default and reset state is an internal no-op logger that satisfies the
    `AuditLogger` protocol.
    """
    return _audit_logger


def audit_logging_enabled() -> bool:
    """Return True when a non-no-op audit logger is currently configured."""
    return _audit_logger is not _NOOP_LOGGER


def emit_permission_audit_event(event: PermissionAuditEvent) -> None:
    """
    Forward an audit event to the configured logger when logging is enabled.

    The disabled state is a no-op. When a logger is configured, this function
    calls `logger.record(event)` directly and lets logger exceptions propagate to
    the caller. Delivery ordering and threading are logger-specific; the built-in
    buffered loggers enqueue events in call order.

    Parameters:
        event: Event payload to record.
    """
    if _audit_logger is _NOOP_LOGGER:
        return
    _audit_logger.record(event)


def _serialize_event(event: PermissionAuditEvent) -> SerializedAuditEvent:
    """Convert an audit event into a JSON-serialisable mapping."""
    user_pk = getattr(event.user, "pk", None)
    user_id = None if user_pk is None else str(user_pk)
    return {
        "timestamp": timezone.now().isoformat(),
        "action": event.action,
        "attributes": list(event.attributes),
        "granted": event.granted,
        "bypassed": event.bypassed,
        "manager": event.manager,
        "user_id": user_id,
        "user": None if user_id is not None else repr(event.user),
        "permissions": list(event.permissions),
        "metadata": None if event.metadata is None else dict(event.metadata),
    }


def _instantiate_logger_reference(
    value: object,
    options: Mapping[str, object] | None = None,
) -> object:
    """Instantiate a logger class or factory while preserving logger instances."""
    if isinstance(value, type):
        factory = cast(Callable[..., object], value)
        return factory(**dict(options or {}))
    if callable(value) and not isinstance(value, AuditLogger):
        factory = cast(Callable[..., object], value)
        return factory(**dict(options or {}))
    return value


def _resolve_logger_reference(value: object) -> AuditLogger | None:
    """Resolve audit logger setting values into concrete logger instances."""
    if value is None:
        return None
    if isinstance(value, str):
        from django.utils.module_loading import import_string

        resolved: object = import_string(value)
    elif isinstance(value, Mapping):
        from django.utils.module_loading import import_string

        config = cast(Mapping[str, object], value)
        logger_reference = config.get("class")
        options_value = config.get("options", {})
        if logger_reference is None:
            return None
        if options_value is None:
            options: Mapping[str, object] = {}
        elif isinstance(options_value, Mapping):
            options = cast(Mapping[str, object], options_value)
        else:
            raise InvalidAuditLoggerOptionsError
        resolved_reference = (
            import_string(logger_reference)
            if isinstance(logger_reference, str)
            else logger_reference
        )
        resolved = _instantiate_logger_reference(resolved_reference, options)
    else:
        resolved = value

    resolved = _instantiate_logger_reference(resolved)

    return resolved if isinstance(resolved, AuditLogger) else None


def configure_audit_logger_from_settings(django_settings: object) -> None:
    """
    Configure the audit logger based on Django settings.

    `GENERAL_MANAGER["AUDIT_LOGGER"]` takes precedence over a top-level
    `AUDIT_LOGGER` setting. Values may be:

    - `None` or missing to reset to the no-op logger.
    - An `AuditLogger` instance.
    - A dotted import path to an `AuditLogger` instance, class, or factory.
    - A zero-argument callable returning an `AuditLogger`.
    - A mapping with `{"class": <path-or-callable>, "options": {...}}`; options
      are passed as keyword arguments when constructing/calling the reference.

    Import and constructor errors propagate. Resolved objects that do not satisfy
    `AuditLogger` disable logging by resetting to the no-op logger.

    Raises:
        InvalidAuditLoggerOptionsError: If a mapping configuration provides an
            `options` value that is not a mapping.
    """
    config_candidate: object = getattr(django_settings, _SETTINGS_KEY, None)
    logger_setting: object = None
    if isinstance(config_candidate, Mapping):
        config = cast(Mapping[str, object], config_candidate)
        if _AUDIT_LOGGER_KEY in config:
            logger_setting = config[_AUDIT_LOGGER_KEY]
        else:
            logger_setting = getattr(django_settings, _AUDIT_LOGGER_KEY, None)
    else:
        logger_setting = getattr(django_settings, _AUDIT_LOGGER_KEY, None)

    logger_instance = _resolve_logger_reference(logger_setting)
    configure_audit_logger(logger_instance)


_MODEL_CACHE: dict[str, type[models.Model]] = {}
_MODEL_CACHE_LOCK = Lock()


def _build_field_definitions() -> dict[str, object]:
    return {
        "created_at": models.DateTimeField(auto_now_add=True),
        "action": models.CharField(max_length=32),
        "attributes": models.JSONField(default=list),
        "granted": models.BooleanField(),
        "bypassed": models.BooleanField(),
        "manager": models.CharField(max_length=255, null=True, blank=True),
        "user_id": models.CharField(max_length=255, null=True, blank=True),
        "user_repr": models.TextField(null=True, blank=True),
        "permissions": models.JSONField(default=list),
        "metadata": models.JSONField(null=True, blank=True),
    }


def _get_audit_model(table_name: str) -> type[models.Model]:
    """Return (and register) a concrete audit model for the given table."""
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(table_name)
        if cached is not None:
            return cached

        app_config = apps.get_app_config("general_manager")
        for model in app_config.get_models():
            if model._meta.db_table == table_name:
                _MODEL_CACHE[table_name] = model
                return model

        attrs: dict[str, object] = _build_field_definitions()
        attrs["__module__"] = __name__
        attrs["Meta"] = type(
            "Meta",
            (),
            {"db_table": table_name, "app_label": "general_manager"},
        )
        model_name = f"PermissionAuditEntry_{abs(hash(table_name))}"
        model = type(model_name, (models.Model,), attrs)
        registry_key = model.__name__.lower()
        if registry_key not in app_config.models:
            apps.register_model("general_manager", model)
        _MODEL_CACHE[table_name] = model
        return model


class _BufferedAuditLogger:
    """Base class implementing a background worker that processes events in batches."""

    _SENTINEL = object()

    def __init__(
        self,
        *,
        batch_size: int = 100,
        flush_interval: float = 0.5,
        use_worker: bool = True,
    ) -> None:
        self._batch_size = max(batch_size, 1)
        self._flush_interval = flush_interval
        self._use_worker = use_worker
        self._closed = Event()
        self._queue: SimpleQueue[PermissionAuditEvent | object] | None
        self._worker: Thread | None
        if self._use_worker:
            self._queue = SimpleQueue()
            self._worker = Thread(target=self._worker_loop, daemon=True)
            self._worker.start()
            atexit.register(self.close)
        else:
            self._queue = None
            self._worker = None

    def record(self, event: PermissionAuditEvent) -> None:
        """Queue or synchronously persist an audit event unless the logger is closed."""
        if self._closed.is_set():
            return
        if not self._use_worker:
            self._handle_batch((event,))
            return
        if self._queue is None:
            return
        self._queue.put(event)

    def close(self) -> None:
        """
        Stop the worker after processing queued events.

        Calls after the first close are ignored. Synchronous loggers created with
        `use_worker=False` have no worker to close.
        """
        if self._closed.is_set():
            return
        self._closed.set()
        if not self._use_worker:
            return
        if self._queue is None or self._worker is None:
            return
        self._queue.put(self._SENTINEL)
        self._worker.join(timeout=2.0)

    def flush(self) -> None:
        """
        Block until queued events are processed.

        For worker-backed loggers this closes the worker, so later `record()`
        calls are ignored. For synchronous loggers it is a no-op.
        """
        self.close()

    def _worker_loop(self) -> None:
        if self._queue is None:
            return
        pending: list[PermissionAuditEvent] = []
        while True:
            try:
                item = self._queue.get(timeout=self._flush_interval)
            except Empty:
                item = None
            if item is self._SENTINEL:
                break
            if isinstance(item, PermissionAuditEvent):
                pending.append(item)
            if len(pending) >= self._batch_size or (item is None and pending):
                self._handle_batch(pending)
                pending = []
        if pending:
            self._handle_batch(pending)

    def _handle_batch(self, events: Iterable[PermissionAuditEvent]) -> None:
        raise NotImplementedError


class FileAuditLogger(_BufferedAuditLogger):
    """
    Persist audit events as newline-delimited JSON records in a file.

    The parent directory is created during initialization. Events are appended in
    the built-in serialized shape. A background worker is used by default; call
    `flush()` or `close()` during teardown to process queued events. After
    closing, later `record()` calls are ignored.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        batch_size: int = 100,
        flush_interval: float = 0.5,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(batch_size=batch_size, flush_interval=flush_interval)

    def _handle_batch(self, events: Iterable[PermissionAuditEvent]) -> None:
        records = [json.dumps(_serialize_event(event)) for event in events]
        if not records:
            return
        data = "\n".join(records) + "\n"
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(data)


class DatabaseAuditLogger(_BufferedAuditLogger):
    """
    Store audit events inside a dedicated database table using Django connections.

    The table is created on demand if it is missing. Non-SQLite connections use
    the background worker; SQLite writes synchronously to support in-memory test
    databases. Call `flush()` or `close()` during teardown when the worker is
    active. After closing, later `record()` calls are ignored.
    """

    def __init__(
        self,
        *,
        using: str = "default",
        table_name: str = "general_manager_permissionauditlog",
        batch_size: int = 100,
        flush_interval: float = 0.5,
    ) -> None:
        self._using = using
        self.table_name = table_name
        self.model = _get_audit_model(table_name)
        connection = connections[self._using]
        use_worker = connection.vendor != "sqlite"
        super().__init__(
            batch_size=batch_size,
            flush_interval=flush_interval,
            use_worker=use_worker,
        )
        self._ensure_table()

    def _ensure_table(self) -> None:
        connection = connections[self._using]
        table_names = connection.introspection.table_names()
        if self.model._meta.db_table in table_names:
            return
        with connection.schema_editor(atomic=False) as editor:
            editor.create_model(self.model)

    def _handle_batch(self, events: Iterable[PermissionAuditEvent]) -> None:
        entries = []
        for event in events:
            serialized = _serialize_event(event)
            entries.append(
                self.model(
                    action=event.action,
                    attributes=serialized["attributes"],
                    granted=event.granted,
                    bypassed=event.bypassed,
                    manager=event.manager,
                    user_id=serialized["user_id"],
                    user_repr=serialized["user"],
                    permissions=serialized["permissions"],
                    metadata=serialized["metadata"],
                )
            )
        if not entries:
            return
        self.model.objects.using(self._using).bulk_create(
            entries, batch_size=self._batch_size
        )


__all__ = [
    "AuditLogger",
    "DatabaseAuditLogger",
    "FileAuditLogger",
    "PermissionAuditEvent",
    "audit_logging_enabled",
    "configure_audit_logger",
    "configure_audit_logger_from_settings",
    "emit_permission_audit_event",
    "get_audit_logger",
]
