# How-To: Create a Custom Interface Type

This guide walks through building a new interface type that composes existing capabilities.

## 1. Subclass the appropriate base

- Use `InterfaceBase` for non-ORM data sources.
- Use `OrmInterfaceBase` when you need the built-in Django ORM plumbing.

```python
from general_manager.interface.base_interface import InterfaceBase
from general_manager.manager.input import Input

class ExternalReportInterface(InterfaceBase):
    _interface_type = "external_report"
    input_fields = {"id": Input(int), "year": Input(int)}
```

## 2. Declare capabilities

Set `configured_capabilities` to bundles or explicit `InterfaceCapabilityConfig` entries.

```python
from general_manager.interface.capabilities.configuration import InterfaceCapabilityConfig
from my_project.interface.capabilities.external import ExternalSyncCapability

class ExternalReportInterface(InterfaceBase):
    ...
    configured_capabilities = (
        InterfaceCapabilityConfig(
            ExternalSyncCapability,
            options={"endpoint": "https://reports.example.com"},
        ),
    )
```

If you need a custom lifecycle handler, set `lifecycle_capability_name` to the capability name so `handle_interface()` uses it.

## 3. Expose the interface on your manager

```python
from general_manager.manager import GeneralManager

class ExternalReport(GeneralManager):
    ...

    class Interface(ExternalReportInterface):
        pass
```

## 4. Update bundles/manifests if needed

If your interface should be reusable by others, consider defining a bundle in `general_manager.interface.bundles` and documenting the capability names in the manifest so tooling can enforce required vs optional capabilities.

## 5. Test & document

- Unit test the interface by exercising the configured capability handlers.
- Document the new interface type and its configuration options so other developers can adopt it.
