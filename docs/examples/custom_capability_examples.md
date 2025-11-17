# Custom Capability Examples

The snippets below illustrate how to build reusable capabilities outside of the core library.

## Cache warm-up capability

```python
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.logging import get_logger


class CacheWarmupCapability(BaseCapability):
    """Warm a cache backend during startup."""

    name = "cache_warmup"

    def __init__(self, *, cache_backend: str) -> None:
        self.cache_backend = cache_backend
        self.logger = get_logger("interface.cache_warmup")

    def get_startup_hooks(self, interface_cls):
        def warm_cache() -> None:
            self.logger.info(
                "warming cache",
                context={
                    "interface": interface_cls.__name__,
                    "backend": self.cache_backend,
                },
            )
            # Real cache warm-up logic goes here.

        return [warm_cache]
```

Attach it via `InterfaceCapabilityConfig`:

```python
from general_manager.interface.capabilities.configuration import InterfaceCapabilityConfig

class ExternalReportInterface(InterfaceBase):
    configured_capabilities = (
        InterfaceCapabilityConfig(
            CacheWarmupCapability,
            options={"cache_backend": "reports"},
        ),
    )
```

## Audit notification capability

```python
class AuditNotificationCapability(BaseCapability):
    """Emit audit events whenever mutations happen."""

    name = "audit_notification"

    def __init__(self, *, topic: str) -> None:
        self.topic = topic
        self.logger = get_logger("interface.audit_notification")

    def notify(self, action: str, payload: dict[str, Any]) -> None:
        self.logger.info(
            "audit event",
            context={"topic": self.topic, "action": action, "payload": payload},
        )

    def inject_mutation_hooks(self, interface_cls):
        def _notify(instance, action: str, payload: dict[str, Any]) -> None:
            self.notify(action, payload)

        setattr(interface_cls, "_audit_notify", _notify)
```
