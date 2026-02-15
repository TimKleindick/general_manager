# Schema Auto-Generation

`general_manager.api.graphql.GraphQL` inspects manager interfaces and creates matching Graphene types and mutations.

## Type mapping

For each manager class, GeneralManager:

1. Registers a Graphene `ObjectType` with fields derived from interface attribute types.
2. Creates resolvers that read values from the underlying manager or bucket.
3. Adds fields for every `@graph_ql_property` method. Union types and optional hints are converted into GraphQL-friendly types.
4. Registers measurement scalars (`MeasurementScalar`) and object wrappers so units stay intact.

## Mutations

Create, update, and delete mutations are added automatically when the interface overrides the base method. Each mutation returns:

- `success`: boolean indicating whether the operation completed.
- `errors`: list of validation or permission errors when present.
- A field with the manager name containing the affected object.

Custom mutations use the `@graph_ql_mutation` decorator from `general_manager.api.mutation`. The decorator analyses the function signature to generate GraphQL input arguments and return types.

### Relation key aliases in mutation inputs

The GraphQL layer normalises relation payload keys before delegating to manager `create()` and `update()`:

- Many-to-many aliases ending in `*_list` are accepted and converted to `*_id_list`.
- Relation foreign-key aliases like `_plant` are accepted and converted to `_plant_id` when the field points to another manager.

This keeps mutation payloads compatible with both legacy GraphQL clients and canonical ORM-style argument names.

```graphql
mutation CreateAsset($name: String!, $Plant: ID!, $tagsList: [ID]) {
  createAsset(name: $name, Plant: $Plant, tagsList: $tagsList) {
    success
    Asset { id name }
  }
}
```

In the example above, `Plant` and `tagsList` are normalised to the canonical relation keys expected by database interfaces.

## GraphQL property warm-up

Mark expensive GraphQL properties with `warm_up=True` to prefill caches after startup:

```python
from general_manager.api.property import graph_ql_property

class Asset(GeneralManager):
    @graph_ql_property(sortable=True, warm_up=True)
    def score(self) -> int:
        return expensive_score_calculation(self)
```

Warm-up behaviour:

- Only properties explicitly marked `warm_up=True` are executed.
- For sortable warm-up properties (without custom `query_annotation`), both ascending and descending sort paths are primed.
- Warm-up can run synchronously, or asynchronously through Celery when enabled.

Settings:

```python
GENERAL_MANAGER = {
    "GRAPHQL_WARMUP_ENABLED": True,   # default True
    "GRAPHQL_WARMUP_ASYNC": False,    # default False
}
```

Equivalent top-level Django settings (`GRAPHQL_WARMUP_ENABLED`, `GRAPHQL_WARMUP_ASYNC`) are also supported for compatibility.

## Buckets and pagination

For bucket-returning fields, the schema registers list fields and page types. `PageInfo` exposes `total_count`, `current_page`, `total_pages`, and optional `page_size` so clients can implement cursor-less pagination quickly.

## Extending the schema

- Override `_map_field_to_graphene_read` to customise how specific Python types map to GraphQL fields (for example, using Relay nodes).
- Register additional scalars or enums by updating `GraphQL.graphql_type_registry` before building the schema.
- Combine auto-generated queries with handcrafted ones by subclassing the generated query root and adding custom fields.
