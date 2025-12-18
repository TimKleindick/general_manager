# Startup hooks with custom ordering

This recipe shows how to expose a startup hook from a capability, declare its ordering, and have it run automatically at app startup.

## 1) Create a capability with a startup hook and dependency resolver

```python
from typing import ClassVar, Set, Type
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.base_interface import InterfaceBase

class WarmupCapability(BaseCapability):
    name: ClassVar[str] = "warmup"

    def get_startup_hooks(self, interface_cls: Type[InterfaceBase]):
        def warmup():
            # expensive setup here
            print(f"warmup for {interface_cls.__name__}")

        return (warmup,)

    def get_startup_hook_dependency_resolver(self, interface_cls: Type[InterfaceBase]):
        # Order this hook after any interfaces it depends on.
        def resolver(iface: Type[object]) -> Set[Type[object]]:
            return getattr(iface, "startup_dependencies", set())

        return resolver
```

The `get_startup_hook_dependency_resolver` returns a callable that maps an interface to the interfaces it depends on. Hooks that share the same resolver are ordered topologically, so dependent interfaces run first.

## 2) Attach the capability to an interface and declare dependencies

```python
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.configuration import InterfaceCapabilityConfig

class CountryInterface(InterfaceBase):
    _interface_type = "readonly"
    startup_dependencies: set[Type[InterfaceBase]] = set()
    configured_capabilities = (
        InterfaceCapabilityConfig(WarmupCapability),
    )

class CityInterface(InterfaceBase):
    _interface_type = "readonly"
    # Ensure City warmup runs after Country warmup
    startup_dependencies = {CountryInterface}
    configured_capabilities = (
        InterfaceCapabilityConfig(WarmupCapability),
    )
```

## 3) Resulting behavior

- At startup, the `WarmupCapability` registers a startup hook for each interface.
- Hooks are grouped by their dependency resolver and ordered so `CountryInterface` runs before `CityInterface`.
- No changes to `apps.py` or test harnesses are required—the registries handle ordering automatically.

## Tips

- Each capability can provide its own dependency resolver; hooks from different capabilities order independently.
- If you don’t need ordering, omit `get_startup_hook_dependency_resolver`.
- In tests, you can inspect `registered_startup_hook_entries()` to verify hooks are registered as expected.
