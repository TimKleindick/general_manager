# Write a Custom Interface

Sometimes your data lives outside of Django or requires a bespoke persistence strategy. Implement a custom interface by subclassing `InterfaceBase`.

## Step 1: Subclass InterfaceBase

```python
from general_manager.interface.base_interface import InterfaceBase
from general_manager.manager import GeneralManager, Input
from typing import Generator

class ExternalReportInterface(InterfaceBase):
    input_fields = {
        "id": Input(int),
        "year": Input(int),
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._payload = self.fetch_report()

    def fetch_report(self) -> dict[str, object]:
        return external_api.get_report(self.identification["id"], self.identification["year"])
```

## Step 2: Implement manager operations

Provide the methods your manager needs. For read-only data, implement `filter`, `exclude`, and `all`. For write capabilities, add `create`, `update`, and `delete` (the legacy `deactivate` name now simply proxies to `delete`).

```python
class ExternalReportInterface(InterfaceBase):
    ...
    @classmethod
    def all(cls) -> Generator[dict[str, object], None, None]:
        for payload in external_api.get_reports():
            yield {"id": payload["id"], "year": payload["year"]}

    def update(self, **kwargs):
        external_api.update_report(self.identification["id"], **kwargs)
    ...
```

## Step 3: Handle dependency tracking

Call `DependencyTracker.track()` when you fetch data so cache invalidation works:

```python
from general_manager.cache.cache_tracker import DependencyTracker

    def fetch_report(self) -> dict[str, object]:
        DependencyTracker.track(
            "ExternalReport",
            "request_query",
            str(self.identification),
        )
        return external_api.get_report(...)
```

Supported operations are `filter`, `exclude`, `identification`, `request_query`,
and `all`; any other operation raises `ValueError`. `manager_name`, `operation`,
and `identifier` must be strings, and malformed values raise `TypeError`.
Concrete invalid-input exception subclasses and messages are internal details.
Calls outside an active
`with DependencyTracker()` context are ignored after validation. Nested contexts
record dependencies in both the nested collector and each enclosing collector,
and duplicate dependency tuples collapse because collectors are sets. Returned
collector sets remain usable after the context exits. The tracker is
thread-local, not async task-local.

## Step 4: Wire up permissions

Attach a permission class like any other manager. Custom interfaces participate fully in permission checks and GraphQL type generation as long as they expose attribute metadata via `get_attribute_types()`.

## Step 5: Document limitations

External systems may not support transactions or historical lookups. Document these limitations in your API layer and adjust your GraphQL mutations to surface meaningful errors when remote calls fail.
