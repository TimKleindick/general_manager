# Managers and Buckets

Managers model business entities. Buckets represent collections of managers that you can query, slice, aggregate, and merge. Together they deliver a high-level API for interacting with your domain.

## Declaring attributes

Attributes declared as type hints become part of the public interface. They can point to primitive types, measurements, nested managers, or buckets. Methods decorated with `@graphQlProperty` expose computed values that automatically become GraphQL fields.

```python
class Project(GeneralManager):
    name: str
    start_date: date | None
    derivative_list: Bucket[Derivative]

    @graphQlProperty
    def duration(self) -> int | None:
        if not self.start_date or not self.end_date:
            return None
        return (self.end_date - self.start_date).days
```

## Buckets

All collection-returning APIs produce a `Bucket`. Buckets behave like Python iterables while preserving metadata such as applied filters and ordering. You can:

- Call `filter()` and `exclude()` to refine the dataset using Django-style lookups.
- Chain `sort()` calls for deterministic ordering.
- Slice (`bucket[0:10]`) or iterate lazily.
- Merge buckets with the union operator (`bucket_a | bucket_b`).

### Grouped data

Use `group_by()` to aggregate managers into `GroupedManager` instances. Grouped managers expose the grouping key and aggregate the remaining attributes according to their type (e.g., summing numbers, merging lists). See the example in [Cookbook: Volume curve](../examples/project_volume_curve.md).

## Identity and equality

Managers compare equal when their identification dictionaries match. Use `manager.identification` to inspect the underlying primary keys. Buckets support `get()` and `first()` helpers to retrieve specific instances.

## Pattern recommendations

- Use descriptive attribute names that align with your GraphQL schema.
- Expose related managers via buckets instead of raw Django querysets so that permission checks remain consistent.

With these guidelines you can design manager hierarchies that remain understandable as your project grows.
