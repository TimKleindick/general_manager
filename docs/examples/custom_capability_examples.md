# Custom Capability Examples

Use these patterns when extending an interface's lifecycle, not when defining
ordinary manager fields. The
[custom capability tutorial](../howto/create_custom_capability.md) provides
the complete module layout and keeps the supplied interface handlers.

## Cache warm-up capability

For GraphQL property warm-up, use the existing
[cache tutorial](../howto/cache_dependent_calculation.md#step-4-warm-selected-properties-proactively).
It owns property selection, recipes, invalidation and scheduling. A capability
named `"cache_warmup"` is not a supported new slot in `CapabilityName`, and
logging a cache name does not preload that cache.

If an application has a different startup task, adapt the tutorial's
`StartupMessageCapability`: accept any configuration in its constructor and
return a no-argument callback from `get_startup_hooks()`. The callback must
call a real application implementation. Keep expensive deployment maintenance
in an explicit command.

## Audit notification capability

The supported observability slot already receives operation outcomes. This
example adds a topic to successful-operation logs while preserving its normal
start/error logging:

```python
from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.logging import get_logger


class TopicLoggingCapability(LoggingObservabilityCapability):
    def __init__(self, *, topic: str) -> None:
        super().__init__()
        self.topic = topic
        self.topic_logger = get_logger("application.operations")

    def after_operation(self, *, operation, target, payload, result):
        super().after_operation(
            operation=operation, target=target, payload=payload, result=result
        )
        self.topic_logger.info(
            "interface operation completed",
            context={"topic": self.topic, "operation": operation},
        )
```

Configure it on an existing interface, preserving that interface's bundle:

```python
from general_manager.interface import DatabaseInterface
from general_manager.interface.capabilities.configuration import InterfaceCapabilityConfig

# Inside a manager's Interface(DatabaseInterface):
configured_capabilities = (
    *DatabaseInterface.configured_capabilities,
    InterfaceCapabilityConfig(
        TopicLoggingCapability,
        options={"topic": "project-operations"},
    ),
)
```

The class must be imported before the manager interface is defined. These are
operation logs, not a durable audit outbox or a promise of transaction commit.
For durable events and follow-up jobs use the
[workflow guide](../howto/workflow_events.md); for permission decisions use the
[audit-logging integration](../concepts/observability/audit_logging.md).
