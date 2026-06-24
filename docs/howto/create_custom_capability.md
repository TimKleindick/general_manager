# How-To: Create a Custom Capability

Capabilities encapsulate cohesive behaviour that interfaces can compose. Follow these steps to add a new capability to the ecosystem.

## 1. Subclass `BaseCapability`

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

```python
from typing import ClassVar

from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability

class CacheWarmupCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "observability"
    required_attributes: ClassVar[tuple[str, ...]] = ("get_data",)
```

The built-in `CapabilityName` vocabulary is fixed. Use one of those names when
you are replacing or extending an existing capability slot. The example above
uses `"observability"` because it adds startup-time side effects around an
interface without changing the read/query/mutation handlers.

## 2. Implement the required methods

Add the methods your interface will call—`get_data`, `filter`, `pre_create`, etc. Keep the capability focused on a single responsibility.

```python
class CacheWarmupCapability(BaseCapability):
    ...
    def get_startup_hooks(self, interface_cls: type[object]):
        def warm_cache():
            CacheBackend.preload(interface_cls._parent_class)
        return (warm_cache,)
```

## 3. Optional: expose startup hooks or system checks

Implement `get_startup_hooks(interface_cls)` or `get_system_checks(interface_cls)` to plug into the global registries. InterfaceBase will register them automatically when the capability binds.

Startup hooks can also declare how they should be ordered relative to other interfaces. If your hook needs related read-only data to exist, expose a dependency resolver:

```python
class CacheWarmupCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "observability"

    def get_startup_hooks(self, interface_cls: type[object]):
        return (lambda: CacheBackend.preload(interface_cls._parent_class),)

    def get_startup_hook_dependency_resolver(self, interface_cls: type[object]):
        # Return a callable that yields dependent interfaces; ordering is computed per hook set.
        def resolver(iface):
            return getattr(iface, "_dependencies", set())
        return resolver
```

Each startup hook set is ordered independently using its resolver, so multiple capabilities on the same interface can each define their own dependency graph without interfering with one another.

## 4. Wire it into bundles/configs

- Add the capability to a bundle (e.g., extend `general_manager.interface.bundles.database`).
- Or configure it directly via `InterfaceCapabilityConfig` on the interface class. For example:

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

> See `docs/examples/custom_capability_examples.md` for a complete sample.

## 5. Document options and side effects

Explain any `options` the capability accepts, what hooks it registers, and how it affects interface behaviour. Include tests that exercise its public methods to keep refactors safe.

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
