# Write a Custom Interface

Sometimes your data lives outside of Django or requires a bespoke persistence strategy. Implement a custom interface by subclassing `InterfaceBase`.

## Step 1: Subclass InterfaceBase

```python
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.manager import GeneralManager, Input

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

Provide the methods your manager needs. For read-only data, implement `filter`, `exclude`, and `all`. For write capabilities, add `create`, `update`, and `deactivate`.

```python
class ExternalReportInterface(InterfaceBase):
    ...
    @classmethod
    def all(cls) -> list[dict[str, object]]:
        for payload in external_api.get_reports():
            yield {"id": payload["id"], "year": payload["year"]}

    def update(self, **kwargs):
        external_api.update_report(self.identification["id"], **kwargs)
    ...
```

## Step 3: Handle dependency tracking

Call `DependencyTracker.track()` when you fetch data so cache invalidation works:

```python
from general_manager.cache.cacheTracker import DependencyTracker

    def fetch_report(self) -> dict[str, object]:
        DependencyTracker.track("ExternalReport", "fetch", str(self.identification))
        return external_api.get_report(...)
```

## Step 4: Wire up permissions

Attach a permission class like any other manager. Custom interfaces participate fully in permission checks and GraphQL type generation as long as they expose attribute metadata via `getAttributeTypes()`.

## Step 5: Document limitations

External systems may not support transactions or historical lookups. Document these limitations in your API layer and adjust your GraphQL mutations to surface meaningful errors when remote calls fail.
