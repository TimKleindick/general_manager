# Managers and Buckets

Managers model business entities. Buckets represent collections of managers that you can query, slice, aggregate, and merge. Together they deliver a high-level API for interacting with your domain.

## Declaring attributes

Attributes declared as type hints become part of the public interface. They can point to primitive types, measurements, nested managers, or buckets. Methods decorated with `@graph_ql_property` expose computed values that automatically become GraphQL fields.

```python
class Project(GeneralManager):
    name: str
    start_date: date | None
    end_date: date | None
    derivative_list: Bucket[Derivative]

    @graph_ql_property
    def duration(self) -> int | None:
        if not self.start_date or not self.end_date:
            return None
        return (self.end_date - self.start_date).days
```

## Buckets

All collection-returning APIs produce a `Bucket`. Buckets behave like Python iterables while preserving metadata such as applied filters and ordering. You can:

- Call `filter()` and `exclude()` to refine the dataset using Django-style lookups.
- Call `get()` when the lookup must return exactly one manager.
- Pass `search_date=...` to `filter()` or `exclude()` to query historical state at a specific point in time.
- Chain `sort()` calls for deterministic ordering.
- Slice (`bucket[0:10]`) or iterate lazily.
- Merge buckets with the union operator (`bucket_a | bucket_b`).

### Bucket variants

`Bucket` is the common collection contract. Concrete bucket types preserve the source and evaluation semantics of the managers they contain:

- `DatabaseBucket` wraps ORM-backed manager queries and supports database-side filtering, ordering, slicing, grouping, and dependency tracking.
- `RequestBucket` represents request-backed manager collections. It compiles declared request filters into a remote request plan and keeps remote pagination and local fallback behavior explicit.
- `CalculationBucket` evaluates calculation interfaces across compatible input domains and tracks dependencies from the buckets or managers feeding the calculation.
- `GroupBucket` holds grouped bucket results from `group_by(...)`; each group key points to the bucket slice that belongs to that group.

Most application code should depend on the shared bucket behavior returned by manager APIs. Reach for a concrete bucket type only when documenting source-specific behavior, testing evaluation semantics, or extending an interface.

### Grouped data

Use `group_by()` to aggregate managers into `GroupedManager` instances. Grouped managers expose the grouping key and aggregate the remaining attributes according to their type (e.g., summing numbers, merging lists). See the example in [Cookbook: Volume curve](../examples/project_volume_curve.md).

## Identity and equality

Managers compare equal when their identification dictionaries match. Use `manager.identification` to inspect the underlying primary keys. Call `Project.get(name="Apollo")` as a shortcut for `Project.filter(name="Apollo").get()` when you expect one match, or use bucket helpers such as `first()` when zero matches are acceptable.

For ORM foreign keys, GeneralManager exposes raw ID helpers such as
`project.customer_id` alongside relation accessors such as `project.customer`.
Use the raw ID helper when you only need the stored identifier and want to avoid
resolving the related manager.

## Pattern recommendations

- Use descriptive attribute names that align with your GraphQL schema.
- Expose related managers via buckets instead of raw Django querysets so that permission checks remain consistent.

With these guidelines you can design manager hierarchies that remain understandable as your project grows.
