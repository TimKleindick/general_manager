# How-To: Create a Custom Interface Type

This guide walks through building a new interface type that composes existing capabilities.

## 1. Subclass the appropriate base

- Use `InterfaceBase` for non-ORM data sources.
- Use `OrmInterfaceBase` when you need the built-in Django ORM plumbing.

```python
from typing import ClassVar

from general_manager.interface.base_interface import InterfaceBase
from general_manager.manager.input import Input

class ExternalReportInterface(InterfaceBase):
    _interface_type = "external_report"
    input_fields: ClassVar[dict[str, Input[type[int]]]] = {
        "id": Input(int),
        "year": Input(int),
    }
```

`OrmInterfaceBase` already declares `{"id": Input(int)}` and loads an ORM row for
that primary key. Override `input_fields` only when your custom ORM interface
needs additional constructor inputs. Preserve the `"id"` field unless you also
override initialization and row loading, and keep the annotation precise so
strict type checking can validate the declared `Input` types.

## 2. Declare capabilities

Set `configured_capabilities` to bundles or explicit `InterfaceCapabilityConfig`
entries. `InterfaceCapabilityConfig(CapabilityClass, options=None)` constructs
the capability with no keyword arguments; a supplied mapping is copied to a plain
`dict` when the capability is instantiated and expanded as keyword arguments.
`CapabilitySet(label, entries)` stores concrete config entries as a tuple.
GeneralManager expands bundles one level in order and does not deduplicate them,
so a later handler with the same capability name replaces an earlier one when the
interface binds handlers. The expansion helpers do not runtime-validate invalid
entries if static typing is bypassed; interface binding is the validation
boundary.

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
