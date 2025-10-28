"""Lightweight audit logging hooks for permission evaluations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable, Literal


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


def _resolve_logger_reference(value: Any) -> AuditLogger | None:
    """Resolve audit logger setting values into concrete logger instances."""
    if value is None:
        return None
    if isinstance(value, str):
        from django.utils.module_loading import import_string

        resolved = import_string(value)
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
