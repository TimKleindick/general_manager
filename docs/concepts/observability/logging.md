---
title: Logging & Observability
description: Configure GeneralManager logging through Django's LOGGING settings with structured context.
---

# Logging & Observability

GeneralManager follows Django's logging conventions: the package never configures handlers on its own and instead expects projects to wire everything through `settings.LOGGING`. The `general_manager.logging` helpers keep logger names consistent and add lightweight context metadata that downstream formatters can consume.

## Logger naming scheme

All loggers live under the `general_manager` namespace so they can be targeted collectively or by component:

| Component | Logger name | Usage |
| --- | --- | --- |
| Root | `general_manager` | Package-wide fallbacks and custom app code. |
| App bootstrap | `general_manager.apps` | Startup sync, GraphQL wiring, ASGI integration, deferred checks. |
| Read-only interface | `general_manager.interface.capabilities.read_only` | Schema drift warnings and sync summaries. |
| Permissions | `general_manager.permission.base` | CRUD permission evaluation flow. |
| Cache dependency index | `general_manager.cache.dependency_index` | Cache invalidation + signals. |
| Manager lifecycle | `general_manager.manager.general` | Create/update/delete actions and queryset queries. |
| Manager metaclass | `general_manager.manager.meta` | Class bootstrap, descriptor issues, GraphQL registration queue. |
| GraphQL API | `general_manager.api.graphql` | Schema build, mutation registration, subscription fan-out, error handling. |
| Cache decorator | `general_manager.cache.decorator` | Cache hits/misses with dependency counts. |
| Rule engine | `general_manager.rule.engine` | Rule evaluation results and generated error payloads. |
| Public API exports | `general_manager.utils.public_api` | Lazy import successes/failures when resolving public exports. |

Use `general_manager.logging.get_logger("cache.dependency_index")` to obtain an adapter that automatically prefixes the logger name, sets the `component` extra value, and merges optional `context` dictionaries.

```python
from general_manager.logging import get_logger

logger = get_logger("api.billing")

def charge(customer_id: str, amount: str) -> None:
    logger.info(
        "prepared billing request",
        context={"customer_id": customer_id, "amount": amount},
    )
```

## Django settings integration

Configure handlers, formatters, and filters centrally in `settings.py`. The snippet below emits structured JSON logs, forwards `context` payloads, and raises alerts whenever multiple errors occur inside `general_manager` components.

```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "fmt": "%(asctime)s %(name)s %(levelname)s %(message)s "
            "%(component)s %(context)s %(request_id)s",
        },
    },
    "filters": {
        "request_id": {
            "()": "django_structlog.filters.RequestIdFilter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id"],
        },
    },
    "loggers": {
        "general_manager": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "general_manager.permission.base": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
```

Place project-specific handlers (e.g. OpenSearch, CloudWatch) inside the same dictionary if you need aggregation beyond stdout.

## Structured context & correlation IDs

`GeneralManagerLoggerAdapter` adds two structured fields:

- `component`: automatically filled with the dotted suffix (e.g. `permission.base`).
- `context`: arbitrary mapping merged per log call. Non-mapping values raise `TypeError` to avoid silently dropping data.

Couple the adapter with existing middleware, such as [`django-request-id`](https://pypi.org/project/django-request-id/) or `django-structlog`, so request or job identifiers are attached to every log that GeneralManager emits. Downstream observability pipelines can then slice by `component`, join logs with traces (OpenTelemetry), and power alerts (e.g. “≥5 `ERROR` per minute from `general_manager.cache.dependency_index`”).

## Emitted events per component

- **`general_manager.apps`**: emits `DEBUG` summaries when syncing read-only data during startup, registering schema checks, wiring GraphQL HTTP endpoints, and integrating Channels subscriptions. Warnings surface misconfigured ASGI modules or missing dependencies.
- **`general_manager.interface.capabilities.read_only`**: warns when schema drift is detected and reports totals for created/updated/deactivated rows after each sync (`created`, `updated`, `deactivated` keys).
- **`general_manager.manager.general`**: records lifecycle actions (`manager created/updated/deleted`) with IDs, user IDs, and touched fields, plus `DEBUG` entries for filters, excludes, and `all()` queries.
- **`general_manager.permission.base`**: logs permission denials at `INFO`, identifying the manager, CRUD action, field, and affected user ID so you can correlate with API failures or audits.
- **`general_manager.cache.decorator` / `general_manager.cache.dependency_index`**: traces cache hits and misses (including dependency counts), and logs structured invalidation metadata (`key`, `lookup`, `action`, `value`) when dependency-based invalidations fire.
- **`general_manager.manager.meta`**: tracks class registration, interface wiring, and warns or errors when descriptor resolution fails (e.g., missing attributes or evaluation exceptions).
- **`general_manager.api.graphql`**: highlights mutation/interface registration, subscription dispatch, and categorises errors (`permission`, `user`, `internal`) with the original exception names so alerting can differentiate failures.
- **`general_manager.rule.engine`**: notes rule initialisation, evaluation outcomes, skipped evaluations due to `None` values, and variable sets involved in generated error messages.
- **`general_manager.utils.public_api`**: warns when consumers access undeclared exports and emits `DEBUG` records when a lazy export resolves, including target module metadata.

Each log call supplies structured payloads that you can reference directly in filters or dashboards (for example, filter by `context.manager` to isolate a single GeneralManager subclass).

## Rollout checklist

1. Define or extend your `LOGGING` dictionary with the handlers/formatters you need.
2. Update Django middleware to set a request or correlation ID and propagate it in log filters.
3. Enable structured log shipping (ELK, OpenSearch, CloudWatch, etc.) and create dashboards that pivot on `component` + `context`.
4. Add regression tests that assert significant flows log at the intended level using `assertLogs("general_manager…")`.
5. Review log levels quarterly; tune noisy components back to `INFO`/`WARNING` to keep alert fatigue low.
