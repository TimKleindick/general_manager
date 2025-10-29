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

Register the logger during Django startup:

```python
from general_manager.permission.audit import configure_audit_logger


def ready(self) -> None:
    configure_audit_logger(PrintAuditLogger())
```

## Buffered logger with batching

Extend `_BufferedAuditLogger` to inherit queueing and background worker support. Only `_handle_batch()` needs to be implemented.

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
- Call `close()` (or `flush()`) during test teardown to ensure all events are processed.

## Wiring the Kafka producer

Instantiate the Kafka producer in your Django settings or app config and pass it to the logger. The example below uses `kafka-python`, but the same pattern applies to `confluent-kafka` or `aiokafka` producers.

```python
# settings.py
from kafka import KafkaProducer
from general_manager.permission.audit import configure_audit_logger
from .logging import KafkaAuditLogger


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

Call `configure_permission_audit_logger()` from your Django `AppConfig.ready()` hook so the logger attaches as soon as the app loads. Producers created with `confluent_kafka.Producer` follow the same pattern—pass the `Producer` instance and encode each event before calling `.produce(topic, value=payload)`.

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

`configure_audit_logger_from_settings()` accepts dotted paths, callables returning loggers, or direct instances.

## Testing the logger

```python
def test_audit_logger_batches(db):
    producer = FakeProducer()
    logger = KafkaAuditLogger(producer, topic="permissions")

    logger.record(PermissionAuditEvent(...))
    logger.flush()

    assert producer.sent_messages  # captured payloads
```

- Use the existing `_serialize_event()` helper when you need a stable JSON shape.
- For async infrastructure, configure `batch_size=1` or `use_worker=False` to simplify deterministic tests.
