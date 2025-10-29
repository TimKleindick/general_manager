"""Lightweight audit logging hooks for permission evaluations."""

from __future__ import annotations

import atexit
import json
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread, Lock
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable, Literal

from django.apps import apps
from django.db import connections, models
from django.utils import timezone


AuditAction = Literal["create", "read", "update", "delete", "mutation"]


@dataclass(slots=True)
class PermissionAuditEvent:
    """
    Payload describing a permission evaluation outcome.

    Attributes:
        action (AuditAction): CRUD or mutation action that was evaluated.
        attributes (tuple[str, ...]): Collection of attribute names covered by this evaluation.
        granted (bool): True when the action was permitted.
        user (Any): User object involved in the evaluation; consumers may extract ids.
        manager (str | None): Name of the manager class (when applicable).
        permissions (tuple[str, ...]): Permission expressions that were considered.
        bypassed (bool): True when the decision relied on a superuser bypass.
        metadata (Mapping[str, Any] | None): Optional additional context.
    """

    action: AuditAction
    attributes: tuple[str, ...]
    granted: bool
    user: Any
    manager: str | None
    permissions: tuple[str, ...] = ()
    bypassed: bool = False
    metadata: Mapping[str, Any] | None = None


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


def configure_audit_logger(logger: AuditLogger | None) -> None:
    """
    Configure the audit logger used by permission checks.

    Parameters:
        logger (AuditLogger | None): Concrete logger implementation. Passing ``None``
            resets the logger to a no-op implementation.
    """
    global _audit_logger
    _audit_logger = logger or _NOOP_LOGGER


def get_audit_logger() -> AuditLogger:
    """Return the currently configured audit logger."""
    return _audit_logger


def audit_logging_enabled() -> bool:
    """Return True when audit logging is active."""
    return _audit_logger is not _NOOP_LOGGER


def emit_permission_audit_event(event: PermissionAuditEvent) -> None:
    """
    Forward an audit event to the configured logger when logging is enabled.

    Parameters:
        event (PermissionAuditEvent): Event payload to record.
    """
    if _audit_logger is _NOOP_LOGGER:
        return
    _audit_logger.record(event)


def _serialize_event(event: PermissionAuditEvent) -> dict[str, Any]:
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
        "metadata": event.metadata,
    }


def _resolve_logger_reference(value: Any) -> AuditLogger | None:
    """Resolve audit logger setting values into concrete logger instances."""
    if value is None:
        return None
    if isinstance(value, str):
        from django.utils.module_loading import import_string

        resolved = import_string(value)
    elif isinstance(value, Mapping):
        from django.utils.module_loading import import_string

        class_path = value.get("class")
        options = value.get("options", {})
        if class_path is None:
            return None
        resolved = (
            import_string(class_path) if isinstance(class_path, str) else class_path
        )
        if isinstance(resolved, type):
            resolved = resolved(**options)
        elif callable(resolved):
            resolved = resolved(**options)
        return resolved if hasattr(resolved, "record") else None
    else:
        resolved = value

    if isinstance(resolved, type):
        resolved = resolved()
    elif callable(resolved) and not hasattr(resolved, "record"):
        resolved = resolved()

    if resolved is None or not hasattr(resolved, "record"):
        return None
    return resolved  # type: ignore[return-value]


def configure_audit_logger_from_settings(django_settings: Any) -> None:
    """
    Configure the audit logger based on Django settings.

    Expects either ``settings.GENERAL_MANAGER['AUDIT_LOGGER']`` or a top-level
    ``settings.AUDIT_LOGGER`` value pointing to an audit logger implementation
    (instance, callable, or dotted import path).
    """
    config: Mapping[str, Any] | None = getattr(django_settings, _SETTINGS_KEY, None)
    logger_setting: Any = None
    if isinstance(config, Mapping):
        logger_setting = config.get(_AUDIT_LOGGER_KEY)
    if logger_setting is None:
        logger_setting = getattr(django_settings, _AUDIT_LOGGER_KEY, None)

    logger_instance = _resolve_logger_reference(logger_setting)
    configure_audit_logger(logger_instance)


_MODEL_CACHE: dict[str, type[models.Model]] = {}
_MODEL_CACHE_LOCK = Lock()


def _build_field_definitions() -> dict[str, models.Field[Any, Any]]:
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

        attrs: dict[str, Any] = _build_field_definitions()
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
    """Base class implementing a background worker that processes audit events in batches."""

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
        if self._use_worker:
            self._queue: SimpleQueue[PermissionAuditEvent | object] = SimpleQueue()
            self._worker = Thread(target=self._worker_loop, daemon=True)
            self._worker.start()
            atexit.register(self.close)
        else:
            self._queue = None  # type: ignore[assignment]
            self._worker = None  # type: ignore[assignment]

    def record(self, event: PermissionAuditEvent) -> None:
        if self._closed.is_set():
            return
        if not self._use_worker:
            self._handle_batch((event,))
            return
        self._queue.put(event)

    def close(self) -> None:
        if self._closed.is_set() or not self._use_worker:
            return
        self._closed.set()
        self._queue.put(self._SENTINEL)
        self._worker.join(timeout=2.0)

    def flush(self) -> None:
        """Block until all queued events are processed."""
        if self._use_worker:
            self.close()

    def _worker_loop(self) -> None:
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
    """Persist audit events as newline-delimited JSON records in a file."""

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
    """Store audit events inside a dedicated database table using Django connections."""

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
