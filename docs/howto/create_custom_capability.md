# How-To: Create a Custom Capability

Capabilities provide the operations and hooks that an interface composes.
Start with a supplied interface unless you need to change one of those
responsibilities. This example adds a configurable startup log message while
preserving the existing operation logger.

Use the [quickstart](../quickstart.md) application. Put the capability in
`projects/capabilities.py`, then import it from `projects/managers.py` as
shown below. No new cache backend or preload helper is required.

## 1. Subclass `BaseCapability`

`LoggingObservabilityCapability` already subclasses `BaseCapability` and
implements the operation hooks for the `"observability"` slot. Extend it so
the example retains that behavior:

```python
from general_manager.interface import InterfaceBase
from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.logging import get_logger


class StartupMessageCapability(LoggingObservabilityCapability):
    def __init__(self, *, message: str) -> None:
        super().__init__()
        self.message = message
        self.startup_logger = get_logger("application.startup")

    def get_startup_hooks(self, interface_cls: type[InterfaceBase]):
        def announce() -> None:
            self.startup_logger.info(
                self.message,
                context={"interface": interface_cls.__name__},
            )

        return (announce,)
```

The constructor accepts exactly the `message` option supplied in Step 4.
Capability names come from the fixed `CapabilityName` vocabulary; inheriting
the observability slot does not add a new arbitrary capability name.

## 2. Implement the required methods

Implement the contract of the slot you replace. Here the inherited
`before_operation`, `after_operation`, and `on_error` methods retain
operation logging; `get_startup_hooks` adds one no-argument callback.

A name in the registry does not invent a call site. Methods such as a custom
`preload()` or `inject_mutation_hooks()` run only if your interface or an
existing framework hook actually calls them. For mutation-triggered application
work, prefer the [workflow event integration](workflow_events.md).

## 3. Optional: expose startup hooks or system checks

InterfaceBase registers callbacks returned by `get_startup_hooks(interface_cls)`
and checks returned by `get_system_checks(interface_cls)` when binding the
capability. The management-command startup runner executes the registered callbacks;
ordinary WSGI/ASGI application readiness alone does not run them. Use the
[startup-hook recipe](../examples/startup_hooks.md) for command timing.

The example does not depend on another interface. If an application hook does,
add a dependency resolver method to that capability:

```python
# Define once at module level, shared by the dependent interfaces:
def startup_dependencies(iface):
    return getattr(iface, "_dependencies", set())


# Optional method inside the capability class:
def get_startup_hook_dependency_resolver(self, interface_cls):
    return startup_dependencies
```

This variant assumes the application deliberately supplies `_dependencies`.
Interfaces that need ordering together must return the same resolver callable;
a newly created nested function for each interface creates separate groups. Follow the
[startup-hook recipe](../examples/startup_hooks.md) for a complete dependency
ordering example.

## 4. Wire it into bundles/configs

Keep the interface's existing capabilities and append the configured observer.
In `projects/managers.py`, update the quickstart Project declaration:

Import the capability and configuration helper at module level:

```python
from general_manager.interface.capabilities.configuration import InterfaceCapabilityConfig
from .capabilities import StartupMessageCapability
```

Add only this configuration inside the existing `Project.Interface` class.
Keep the quickstart's fields and `Project.Permission` unchanged:

```python
configured_capabilities = (
    *DatabaseInterface.configured_capabilities,
    InterfaceCapabilityConfig(
        StartupMessageCapability,
        options={"message": "Project interface is ready"},
    ),
)
```

The later handler replaces the active `"observability"` slot while the ORM
read/write/history handlers remain configured. With logging configured to
display INFO messages from `general_manager.application.startup`, a management command that runs startup
hooks reports `Project interface is ready`. This changes runtime configuration,
not the Project fields.

## 5. Document options and side effects

Document accepted constructor options, callback timing, and failures. Startup
hooks should be appropriate to repeat in application processes; avoid
unbounded data loading or writes that depend on unapplied migrations. If the
hook must perform deployment maintenance, use an explicit management command
and the [startup-maintenance guide](startup_maintenance.md).

### Binding and configuration details

Give the capability a supported `name` so interfaces can reference it via
`InterfaceCapabilityConfig` and retrieve it from the capability registry.
If the capability should only bind to interfaces that expose specific methods or
attributes, declare them in `required_attributes`. `BaseCapability.setup()` checks
those names with `hasattr()`, raises `CapabilityBindingError` with sorted missing
names when any are absent, and otherwise registers the capability instance in the
interface class's `_capability_handlers` mapping after converting the current
registry to a plain `dict`. If that registry does not exist yet, setup starts
from an empty plain `dict`. `teardown()` removes that registered name using the
same plain-`dict` conversion, also starts from an empty mapping when no registry
exists, and is a no-op if the handler is already absent. Non-`AttributeError`
failures raised while checking required attributes propagate unchanged.
`BaseCapability` has no constructor options of its own; add an `__init__()` only
when your capability needs configuration.
When catching a binding failure, `CapabilityBindingError` exposes
`capability_name` and `reason` attributes in addition to the formatted
`RuntimeError` message `Capability '<name>' could not be attached: <reason>`.
The reason string is preserved exactly, including empty or multiline values.

`options=None` calls the capability constructor without keyword arguments. Any
mapping you pass is copied and expanded as keyword arguments, so the original
mapping is not mutated and even a custom falsey mapping is still honored.
Capability bundles are represented with `CapabilitySet(label, entries)` and may
be mixed with direct entries on `configured_capabilities`; bundles expand one
level in order.

Older manifest-driven paths use `build_capabilities(interface_cls, names,
overrides)` with `CapabilityOverride` values. Overrides are zero-argument
capability classes or callables and take precedence over the default
`CAPABILITY_CLASS_MAP` entry for the same name. The factory preserves name order,
creates a fresh default handler for every duplicate name, and calls an override
once per duplicate occurrence. Unknown names without overrides raise `KeyError`.

> See the [capability cookbook](../examples/custom_capability_examples.md) for another observer variant.

## 6. Replace or rely on observability logging

The built-in `LoggingObservabilityCapability` already implements the
`"observability"` capability slot for the default interface bundles. It has no
constructor options and writes to `get_logger("interface.observability")`.
Operation wrappers call the capability with:

```python
capability.before_operation(operation="request.query.execute", target=Interface, payload={...})
capability.after_operation(operation="request.query.execute", target=Interface, payload={...}, result=result)
capability.on_error(operation="request.query.execute", target=Interface, payload={...}, error=exc)
```

Each log call uses the message `"interface operation start"`,
`"interface operation end"`, or `"interface operation error"` and passes
structured metadata as `context=...`. The context records the operation name, a
string target name, sorted payload keys, selected request metadata, result
metadata overrides for end events, and exception details for error events.
Selected metadata keys are included when present even if their value is `None`.
End-event result metadata is read only when `result.metadata` is a
`collections.abc.Mapping`, and result metadata values replace same-named payload
values for `status_code`, `retry_count`, and `request_id`. `AttributeError`
from optional `target.__name__`, `result.metadata`, and `error.status_code`
lookup is treated as missing; other lookup errors propagate. Error events
include `status_code` only when `error.status_code` exists.

To forward these events somewhere else, provide another capability with
`name = "observability"` and the same three hook methods, then configure it with
`InterfaceCapabilityConfig` or a capability override. Hook exceptions propagate
through the operation wrapper, so custom observers should avoid raising for
best-effort telemetry paths.
