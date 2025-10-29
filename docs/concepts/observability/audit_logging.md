# Audit Logging

Audit logging captures every permission decision made by the system. General Manager plugs audit loggers via the ``AUDIT_LOGGER`` setting, and ships with two ready-to-use implementations. During ``AppConfig.ready`` the package reads ``GENERAL_MANAGER['AUDIT_LOGGER']`` (or the top-level ``AUDIT_LOGGER``) and automatically wires the logger; when unset, logging is disabled with zero overhead.

## Configuration via settings

```python
# settings.py

GENERAL_MANAGER = {
    "AUDIT_LOGGER": "general_manager.permission.audit.DatabaseAuditLogger",
}
```

Pass either the dotted import path of a logger class/instance or a mapping:

```python
GENERAL_MANAGER = {
    "AUDIT_LOGGER": {
        "class": "general_manager.permission.audit.DatabaseAuditLogger",
        "options": {
            "using": "replica",  # database alias
            "table_name": "gm_permission_audit",
            "batch_size": 500,
            "flush_interval": 0.25,
        },
    }
}
```

## PermissionAuditEvent

Each audit logger receives a ``PermissionAuditEvent`` instance containing:

- ``action`` – one of ``create``, ``read``, ``update``, ``delete``, or ``mutation``.
- ``attributes`` – attributes evaluated during the check.
- ``granted`` / ``bypassed`` – decision outcome, including superuser shortcuts.
- ``manager`` – name of the permissioned manager, if available.
- ``user_id`` / ``user_repr`` – primary key when available or best-effort string representation.
- ``permissions`` – evaluated expressions (e.g., ``isAdmin``).
- ``metadata`` – custom context supplied by loggers or future extensions.

## Built-in loggers

### FileAuditLogger

Streams events as newline-delimited JSON to the given path. Uses a background worker with batching to keep request latency low.

`FileAuditLogger` writes newline-delimited JSON to the given path using a background worker. When configuring imperatively, call `configure_audit_logger`.

`DatabaseAuditLogger` persists events via Django’s ORM (default table: `general_manager_permissionauditlog`). The logger creates the table on demand; on SQLite it falls back to synchronous writes for compatibility with in-memory tests. Use `using` to target a different database or `table_name` for custom storage.

```python
# apps.py
from django.apps import AppConfig

from general_manager.permission import configure_audit_logger
from general_manager.permission import DatabaseAuditLogger, FileAuditLogger


class GeneralManagerIntegrationConfig(AppConfig):
    name = "project.general_manager_integration"

    def ready(self) -> None:
        configure_audit_logger(DatabaseAuditLogger())
        # or configure_audit_logger(FileAuditLogger("/var/log/general-manager-audit.log"))
```

## Custom loggers

### Logger options

- ``FileAuditLogger`` accepts ``batch_size`` and ``flush_interval`` (seconds) besides the file path.
- ``DatabaseAuditLogger`` accepts ``using`` (database alias), ``table_name`` (audit table name), ``batch_size`` (events per bulk insert) and ``flush_interval`` (seconds between background flushes).

Implement the `AuditLogger` protocol (`record(event: PermissionAuditEvent) -> None`) and register the logger via `configure_audit_logger()` or the settings hook. For asynchronous pipelines (Kafka, Celery, …) batch events to minimise request overhead.
