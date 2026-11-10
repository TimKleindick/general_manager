# Custom Audit Logger

Implement a bespoke audit logger to forward permission events to your observability stack. The interface is defined by the [audit logging API](../api/permission.md#audit-logging).

## Minimal synchronous logger

```python
from typing import Final
from general_manager.permission.audit import AuditLogger, PermissionAuditEvent


class PrintAuditLogger(AuditLogger):
    prefix: Final[str] = "[permission]"

    def record(self, event: PermissionAuditEvent) -> None:
        print(self.prefix, event.action, event.granted, event.attributes)
```

Put `PrintAuditLogger` in an importable application module such as
`myproject.audit`. Select it through settings, preserving the other entries:

```python
GENERAL_MANAGER = {
    "AUDIT_LOGGER": "myproject.audit.PrintAuditLogger",
}
```

A manual startup override is also possible, but its app config must run after
`general_manager` in `INSTALLED_APPS` so it is not replaced by settings setup:

```python
from general_manager.permission.audit import configure_audit_logger


def ready(self) -> None:
    configure_audit_logger(PrintAuditLogger())
```

## Buffered logger with batching

For a fully public integration, implement `AuditLogger.record()` directly and manage your own queue. General Manager also ships an internal `_BufferedAuditLogger` helper that the built-in loggers use; subclass it only when you are comfortable following internal API changes. Only `_handle_batch()` needs to be implemented.

```python
import json
from general_manager.permission.audit import _BufferedAuditLogger, PermissionAuditEvent


class KafkaAuditLogger(_BufferedAuditLogger):
    def __init__(self, producer, topic: str, *, batch_size: int = 500) -> None:
        super().__init__(batch_size=batch_size, flush_interval=0.2)
        self._producer = producer  # e.g. kafka.KafkaProducer
        self._topic = topic

    def _handle_batch(self, events: list[PermissionAuditEvent]) -> None:
        for event in events:
            payload = json.dumps(
                {
                    "action": event.action,
                    "granted": event.granted,
                    "attributes": event.attributes,
                    "manager": event.manager,
                    "user": getattr(event.user, "id", None),
                    "permissions": event.permissions,
                    "metadata": event.metadata,
                }
            ).encode("utf-8")
            self._producer.send(self._topic, payload)
        self._producer.flush()
```

- The worker thread flushes automatically on application exit.
- Call `flush()` when accepted events must be persisted while the logger stays open; call `close()` at the terminal lifecycle boundary.
- After `close()`, later `record()` calls raise `AuditLoggerClosedError`. A successful `flush()` does not close the logger.
- `close()` waits for accepted events to drain and re-raises worker persistence failures. It has no timeout, so a stuck persistence operation remains visible as a blocked close rather than a false-success return.

## Wiring the Kafka producer

This optional example requires an application-owned Kafka deployment and the
`kafka-python` package. Create the synchronous producer in an application
startup function and pass it to the logger. Other clients need an adapter for
their own methods and lifecycle; an async producer cannot be substituted for
this synchronous `_handle_batch()` implementation.

```python
# myproject/audit_setup.py
from kafka import KafkaProducer
from general_manager.permission.audit import configure_audit_logger
from .audit import KafkaAuditLogger


def configure_permission_audit_logger() -> None:
    producer = KafkaProducer(
        bootstrap_servers=["kafka:9092"],
        value_serializer=lambda value: value,  # already JSON bytes
    )
    configure_audit_logger(
        KafkaAuditLogger(
            producer=producer,
            topic="permission-events",
            batch_size=200,
        )
    )
```

Call `configure_permission_audit_logger()` from an application `AppConfig.ready()` that runs after `general_manager`. Producers created with `confluent_kafka.Producer` follow the same pattern—pass the `Producer` instance and encode each event before calling `.produce(topic, value=payload)`.

## Settings-based configuration

Expose the logger through Django settings so deployments can switch implementations without code changes:

```python
# settings.py
GENERAL_MANAGER = {
    "AUDIT_LOGGER": {
        "class": "path.to.KafkaAuditLogger",
        "options": {
            "producer": kafka_producer,
            "topic": "permissions",
        },
    }
}
```

`configure_audit_logger_from_settings()` accepts dotted paths, callables returning loggers, direct instances, or a mapping with `class` plus `options`. `GENERAL_MANAGER["AUDIT_LOGGER"]` takes precedence over the top-level `AUDIT_LOGGER` setting.

## Testing the logger

```python
def test_audit_logger_batches(db):
    producer = FakeProducer()
    logger = KafkaAuditLogger(producer, topic="permissions")

    logger.record(PermissionAuditEvent(...))
    logger.flush()

    assert producer.sent_messages  # captured payloads
```

- Define an application-owned serialized event shape. `_serialize_event()` is an internal helper, not a stable public integration contract.
- For deterministic tests, provide a synchronous fake producer and explicitly flush accepted events. The shown `KafkaAuditLogger` exposes `batch_size` but no `use_worker` option.
