# Startup hooks with custom ordering

This advanced recipe registers capability hooks with a shared dependency
resolver so supported Django management commands run them in the intended order.
Read [startup maintenance](../howto/startup_maintenance.md) for deployment work;
ordinary WSGI/ASGI initialization does not execute this command runner.
Put the following definitions in an installed app's `managers.py`, or import
their module from there, so GeneralManager discovers them during startup.

## 1) Create a capability with a startup hook and dependency resolver

```python
from collections.abc import Set as AbstractSet

from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.interface.base_interface import InterfaceBase


def warmup_dependencies(iface: type[object]) -> AbstractSet[type[object]]:
    return getattr(iface, "startup_dependencies", set())


class WarmupCapability(LoggingObservabilityCapability):
    def get_startup_hooks(self, interface_cls: type[InterfaceBase]):
        def warmup():
            # expensive setup here
            print(f"warmup for {interface_cls.__name__}")

        return (warmup,)

    def get_startup_hook_dependency_resolver(
        self,
        interface_cls: type[InterfaceBase],
    ):
        # Return the same callable object for every interface in this group.
        return warmup_dependencies
```

The `get_startup_hook_dependency_resolver` returns a callable that maps an interface to the interfaces it depends on. Hooks that share the same resolver object are grouped by the startup runner and ordered topologically, so dependency interfaces run before interfaces that depend on them. Plain `iter_interface_startup_hooks()` is a registry iterator only; it does not apply dependency ordering.

## 2) Attach the capability to an interface and declare dependencies

```python
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.configuration import InterfaceCapabilityConfig

class CountryInterface(InterfaceBase):
    startup_dependencies: set[type[InterfaceBase]] = set()
    configured_capabilities = (
        InterfaceCapabilityConfig(WarmupCapability),
    )

class CityInterface(InterfaceBase):
    # Ensure City warmup runs after Country warmup
    startup_dependencies = {CountryInterface}
    configured_capabilities = (
        InterfaceCapabilityConfig(WarmupCapability),
    )
```

These are standalone hook-owner classes, not complete storage interfaces.
Initialize their declared capabilities before the command runner takes its
registry snapshot:

```python
CountryInterface.get_capabilities()
CityInterface.get_capabilities()
```

In a real manager, attach the configuration to its concrete interface; manager
initialization already performs capability binding. The explicit calls above
make that step visible for these standalone classes.

## 3) Resulting behavior

- Capability initialization registers a startup hook for each interface.
- Hooks are grouped by their dependency resolver and ordered so `CountryInterface` runs before `CityInterface`.
- Dependencies outside the selected interfaces are ignored. Circular or self-dependencies do not raise; those interfaces run after the acyclic portion in their original relative order.
- The command runner executes registered hooks before management-command work.
  For these snippets, it prints Country warm-up before City warm-up.
- A fresh resolver closure for each interface would create separate groups and
  would not establish this cross-interface dependency order.

## 4) How the app startup hook reaches your code

`GeneralmanagerConfig.ready()` imports each installed app's optional
`<app>.managers` module before initializing manager classes. Keep manager class
definitions there when possible; otherwise import your custom manager module
from an app config that runs before `general_manager`. Prefer the discoverable
`managers.py` path so application ordering is not needed.
`GeneralmanagerConfig.ready()` installs the management-command startup-hook
runner and registers system checks before importing manager modules. Installing
the runner does not execute registered hooks immediately; the patched command
runner executes them before supported Django management commands run. After
manager initialization, GeneralManager configures remote APIs, observability,
search, workflow, and scheduled maintenance, and builds GraphQL only when
`AUTOCREATE_GRAPHQL` is enabled. Errors from managers module imports propagate
through Django startup, and errors from startup-hook execution are logged by the
runner when the hook phase is reached.

## Tips

- Each capability can provide its own dependency resolver; hooks from different capabilities order independently.
- If you don’t need ordering, omit `get_startup_hook_dependency_resolver`.
- In tests, use `registered_startup_hook_entries()` to inspect hooks with their resolvers, `registered_startup_hooks()` when you only need hook callables, and `clear_startup_hooks()` to reset the process-local registry.
