# Analyse Permission Audit Logs

This tutorial explains how to enable the permission audit logger, capture events, and analyse them for policy verification. It builds on the infrastructure provided in the [audit logging API](../api/permission.md#audit-logging).

## 1. Enable logging in development

Choose an audit logger implementation. `FileAuditLogger` writes newline-delimited JSON, while `DatabaseAuditLogger` stores entries in a Django-managed table.

```python
# settings.py — merge AUDIT_LOGGER into existing GENERAL_MANAGER settings.
GENERAL_MANAGER = {
    "AUDIT_LOGGER": {
        "class": "general_manager.permission.audit.FileAuditLogger",
        "options": {"path": str(BASE_DIR / "permission.log")},
    }
}
```

For database storage, select the database logger instead:

```python
# settings.py
GENERAL_MANAGER = {
    "AUDIT_LOGGER": {
        "class": "general_manager.permission.audit.DatabaseAuditLogger",
        "options": {"using": "default", "table_name": "gm_permission_log"},
    }
}
```

`configure_audit_logger_from_settings()` resolves those settings during app startup. `GENERAL_MANAGER["AUDIT_LOGGER"]` takes precedence over a top-level `AUDIT_LOGGER`; if neither is present, logging is disabled by installing the no-op logger.

Use the project's existing `BASE_DIR` or another writable path. Do not call
`configure_audit_logger()` directly while importing `settings.py`: startup
resolves the setting afterwards and can replace an earlier logger.

## 2. Trigger and inspect events

Perform a permitted and a denied operation through your application's normal
manager-write or GraphQL path. Concrete permission checks emit
`PermissionAuditEvent` records when logging is active. Plain Python reads do
not automatically enforce or audit read access, and optimized GraphQL plans
may avoid per-row checks; audit counts are not record-access counts. Entries contain:

| Field | Meaning |
| ----- | ------- |
| `timestamp` (JSON), `created_at` (database) | Serialization time for file output; row creation time for database storage. |
| `action` | `"create"`, `"read"`, `"update"`, `"delete"`, or `"mutation"`. |
| `attributes` | Tuple of attribute names that were evaluated. |
| `granted` | `True` when the check passed. |
| `bypassed` | `True` when the request user was a superuser. |
| `manager` | Name of the manager class, if known. |
| `user_id` | Primary key of the user when available. |
| `user` (JSON), `user_repr` (database) | `repr(user)` when no primary key is available; otherwise `null`. |
| `permissions` | All expressions evaluated, including inherited rules from `__based_on__`. |
| `metadata` | JSON-compatible custom context. |

Load events into your preferred analysis tool:

```bash
jq '. | select(.action == "update" and (.granted == false))' permission.log
```

For database logging, run Django ORM queries:

```python
from general_manager.permission.audit import DatabaseAuditLogger, get_audit_logger

logger = get_audit_logger()
if not isinstance(logger, DatabaseAuditLogger):
    raise RuntimeError("Select DatabaseAuditLogger before using this example")
logger.flush()
denied = logger.model.objects.using("default").filter(granted=False).order_by("-created_at")
```

## 3. Build dashboards and alerts

1. **Aggregate denied events** by `manager` and `attributes` to spot missing rules.
2. **Track bypasses** (`bypassed=True`) to ensure superuser usage stays intentional.
3. **Correlate application-owned audit records** with request metadata. The
   following emits an explicit record after your application has determined the
   outcome; it does not perform a permission check or attach metadata to checks
   already performed by the framework:

   ```python
   from general_manager.permission.audit import (
       PermissionAuditEvent,
       emit_permission_audit_event,
   )

   emit_permission_audit_event(
       PermissionAuditEvent(
           action="update",
           attributes=("status",),
           granted=False,
           user=request_user,
           manager="Project",
           permissions=("isSelf",),
           metadata={"request_id": request_id},
       )
   )
   ```

4. **Automate regression detection** by running a nightly query that compares denied counts across deployments.

## 4. Test your logging setup

- In unit tests, inject a stub logger implementing `record()` to capture events.
- Assert that permission checks produce both success and failure entries.
- Call `flush()` on buffered loggers (`FileAuditLogger`, and `DatabaseAuditLogger` on non-SQLite databases) whenever accepted events must be persisted while logging continues. Call `close()` only at the terminal lifecycle boundary: it rejects later `record()` calls, waits for accepted events to drain, and re-raises a worker persistence failure. Repeated `close()` calls return after a successful close and report the retained failure after an unsuccessful one. There is no close timeout, so a persistence operation that never returns keeps `close()` blocked rather than reporting false success.

Use these records to investigate evaluated decisions and verify specific permission paths; their presence alone does not prove the entire access policy is correct.
