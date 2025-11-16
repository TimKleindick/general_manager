# How-To: Create a Custom Capability

Capabilities encapsulate cohesive behaviour that interfaces can compose. Follow these steps to add a new capability to the ecosystem.

## 1. Subclass `BaseCapability`

Give the capability a unique `name` so interfaces can reference it via `InterfaceCapabilityConfig`.

```python
from general_manager.interface.capabilities.builtin import BaseCapability

class CacheWarmupCapability(BaseCapability):
    name = "cache_warmup"
```

## 2. Implement the required methods

Add the methods your interface will call—`get_data`, `filter`, `pre_create`, etc. Keep the capability focused on a single responsibility.

```python
class CacheWarmupCapability(BaseCapability):
    ...
    def get_startup_hooks(self, interface_cls):
        def warm_cache():
            CacheBackend.preload(interface_cls._parent_class)
        return [warm_cache]
```

## 3. Optional: expose startup hooks or system checks

Implement `get_startup_hooks(interface_cls)` or `get_system_checks(interface_cls)` to plug into the global registries. InterfaceBase will register them automatically when the capability binds.

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

> See `docs/examples/custom_capability_examples.md` for a complete sample.

## 5. Document options and side effects

Explain any `options` the capability accepts, what hooks it registers, and how it affects interface behaviour. Include tests that exercise its public methods to keep refactors safe.
