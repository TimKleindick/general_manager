# Expose Managers via GraphQL

This guide shows the minimum setup to expose GeneralManager classes through GraphQL, then covers practical options for mutation payloads and warm-up.

## 1. Register managers and enable GraphQL schema generation

Ensure your manager classes are imported during startup (for example in your app's `apps.py`) so `GeneralmanagerConfig.ready()` can register them into the GraphQL schema.

Minimal example:

```python
from django.db import models

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.permission import ManagerBasedPermission


class Plant(GeneralManager):
    class Interface(DatabaseInterface):
        name = models.CharField(max_length=100)

        class Meta:
            app_label = "my_app"

    class Permission(ManagerBasedPermission):
        __read__ = ["public"]
        __create__ = ["public"]
        __update__ = ["public"]
        __delete__ = ["public"]
```

Once loaded, GeneralManager auto-generates:

- GraphQL object types for managers.
- Query fields for list/read access.
- Create/update/delete mutations when supported by the interface.

## 2. Query with filters, sorting, and pagination

List fields accept `filter`, `exclude`, `order_by`, `page`, and `page_size`.

```graphql
query {
  plants(filter: {name__icontains: "north"}, orderBy: "-name", page: 1, pageSize: 20) {
    pageInfo { totalCount currentPage totalPages pageSize }
    items { id name }
  }
}
```

## 3. Use mutation relation aliases safely

Mutation inputs accept canonical relation keys and GraphQL-friendly aliases:

- `tags_id_list` (canonical) and `tags_list` (alias) are both accepted for many-to-many relations.
- `_plant_id` (canonical) and `_plant` (alias) are both accepted for foreign-key IDs.

Example:

```graphql
mutation CreateAsset($name: String!, $Plant: ID!, $tagsList: [ID]) {
  createAsset(name: $name, Plant: $Plant, tagsList: $tagsList) {
    success
    errors
    Asset { id name }
  }
}
```

GeneralManager normalises these aliases before delegating to the interface layer.

## 4. Warm up expensive GraphQL properties

Mark expensive derived fields with `warm_up=True`:

```python
from general_manager.api.property import graph_ql_property


class Asset(GeneralManager):
    class Interface(DatabaseInterface):
        rank = models.IntegerField()

        class Meta:
            app_label = "my_app"

    @graph_ql_property(sortable=True, warm_up=True)
    def score(self) -> int:
        return -self.rank
```

Warm-up settings:

```python
GENERAL_MANAGER = {
    "GRAPHQL_WARMUP_ENABLED": True,   # default True
    "GRAPHQL_WARMUP_ASYNC": False,    # default False
}
```

You can also define `GRAPHQL_WARMUP_ENABLED` and `GRAPHQL_WARMUP_ASYNC` as top-level Django settings.

When async warm-up is enabled and Celery is available, GeneralManager enqueues warm-up tasks. Otherwise it falls back to synchronous warm-up at startup.

## 5. Add subscription support (optional)

Configure Django Channels and subscribe to manager changes:

```graphql
subscription ($id: ID!) {
  onPlantChange(id: $id) {
    action
    item { id name }
  }
}
```

Use the subscription concepts page for dependency-aware behaviour details:
- [GraphQL subscriptions](../concepts/graphql/subscriptions.md)
