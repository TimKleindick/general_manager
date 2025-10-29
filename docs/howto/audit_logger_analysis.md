# Analyse Permission Audit Logs

This tutorial explains how to enable the permission audit logger, capture events, and analyse them for policy verification. It builds on the infrastructure provided in the [audit logging API](../api/permission.md#audit-logging).

## 1. Enable logging in development

Choose an audit logger implementation. `FileAuditLogger` writes newline-delimited JSON, while `DatabaseAuditLogger` stores entries in a Django-managed table.

```python
# settings.py
from general_manager.permission.audit import FileAuditLogger, configure_audit_logger


configure_audit_logger(
    FileAuditLogger(path="/var/log/general_manager/permission.log")
)
```

Alternatively, configure via Django settings:

```python
# settings.py
GENERAL_MANAGER = {
    "AUDIT_LOGGER": {
        "class": "general_manager.permission.audit.DatabaseAuditLogger",
        "options": {"using": "default", "table_name": "gm_permission_log"},
    }
}
```

`configure_audit_logger_from_settings()` resolves those settings during app startup (see `general_manager.apps:PermissionAuditReady`).

## 2. Trigger and inspect events

Each call to `BasePermission.check_*` or mutation permissions emits a `PermissionAuditEvent` when logging is active. Entries contain:

| Field | Meaning |
| ----- | ------- |
| `timestamp` | Recorded when the event is serialised. |
| `action` | `"create"`, `"read"`, `"update"`, `"delete"`, or `"mutation"`. |
| `attributes` | Tuple of attribute names that were evaluated. |
| `granted` | `True` when the check passed. |
| `bypassed` | `True` when the request user was a superuser. |
| `manager` | Name of the manager class, if known. |
| `user_id` | Primary key of the user (falls back to `repr(user)` when missing). |
| `permissions` | All expressions evaluated, including inherited rules from `__based_on__`. |

Load events into your preferred analysis tool:

```bash
jq '. | select(.action == "update" and (.granted == false))' permission.log
```

For database logging, run Django ORM queries:

```python
from general_manager.permission.audit import DatabaseAuditLogger

logger = DatabaseAuditLogger(table_name="gm_permission_log", using="default")
denied = logger.model.objects.filter(granted=False).order_by("-created_at")
```

## 3. Build dashboards and alerts

1. **Aggregate denied events** by `manager` and `attributes` to spot missing rules.
2. **Track bypasses** (`bypassed=True`) to ensure superuser usage stays intentional.
3. **Correlate with request metadata** by attaching extra data via the `metadata` field. When calling permission checks manually, pass context:

   ```python
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
- Call `close()` or `flush()` on buffered loggers (`FileAuditLogger`, `DatabaseAuditLogger`) in teardown hooks to ensure the worker thread finishes processing events.

With logging enabled and observed, you can prove your permission model behaves as intended and quickly diagnose unexpected access results.
