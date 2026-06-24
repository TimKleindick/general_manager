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

For ORM-backed managers, these calls return a `DatabaseBucket`. Builder methods
keep queryset evaluation lazy, while terminal methods such as `count()`,
`first()`, `last()`, `len(bucket)`, scalar indexing, iteration, and `manager in
bucket` evaluate the effective query and record cache dependencies. Use
filterable/sortable `@graph_ql_property` fields the same way as model fields;
GeneralManager evaluates properties without query annotations in Python and
preserves manager wrapping in the returned bucket.

Database buckets may reuse run-scoped row or primary-key snapshots for safe
querysets, but skip reuse for risky queryset shapes such as distinct, combined,
prefetched, deferred, select-for-update, invalid, or oversized results. Cached
`get()` shortcuts apply only to single-key `pk` or `id` lookups. Bucket unions
require matching manager classes and `search_date` values, and `none()` keeps the
same bucket context while returning no rows.

### Bucket variants

`Bucket` is the common collection contract. Concrete bucket types preserve the source and evaluation semantics of the managers they contain:

- `DatabaseBucket` wraps ORM-backed manager queries and supports database-side filtering, ordering, slicing, grouping, and dependency tracking.
- `RequestBucket` represents request-backed manager collections. It compiles declared request filters into a remote request plan and keeps remote pagination and local fallback behavior explicit.
- `CalculationBucket` evaluates calculation interfaces across compatible input domains and tracks dependencies from the buckets or managers feeding the calculation.
- `GroupBucket` holds grouped bucket results from `group_by(...)`; each group key points to the bucket slice that belongs to that group.

Most application code should depend on the shared bucket behavior returned by manager APIs. Reach for a concrete bucket type only when documenting source-specific behavior, testing evaluation semantics, or extending an interface.

### Grouped data

Use `group_by()` to aggregate managers into `GroupedManager` instances. Grouped
managers expose the grouping key and aggregate the remaining attributes
according to their type, such as summing numbers, merging lists, or combining
measurements. Group-by keys must be string attribute names exposed by the
manager interface. Non-string keys raise `InvalidGroupByKeyTypeError`, and
unknown keys raise `UnknownGroupByKeyError`.

A `GroupBucket` is materialized from the source bucket at construction time.
Each distinct tuple of group-by values becomes one group, emitted in
`str(group_by_value)` order. Manager-valued group keys compare by manager class
plus sorted identification items. Mapping group values compare by recursively
frozen key/value pairs sorted by `repr`; lists and tuples compare by frozen
element order, sets compare as frozensets, and other values use their raw
hashable value. Bucket equality ignores group order and compares the set of
groups plus manager class and grouping-key tuple. Pickle reconstruction stores
`(GroupBucket, (manager_class, group_by_keys, basis_data))` and rebuilds groups
from the basis bucket. `filter()` and `exclude()` run on the underlying source
bucket and then rebuild the grouped view. `get()` returns the first matching
group and raises `GroupItemNotFoundError` only when no group matches; use stricter
checks in application code if multiple matching groups would be ambiguous.

Indexing a `GroupBucket` with an integer returns one group. Slicing returns a new
`GroupBucket` backed by the union of the selected groups' source buckets; slicing
to no groups raises `EmptyGroupBucketSliceError`. `sort()` orders groups in
memory by group attributes and can propagate normal `AttributeError` or
`TypeError` from missing or incomparable values. Combining grouped buckets with
`|` requires the same bucket type, manager class, and group-by key tuple; the
union is rebuilt from the combined source buckets. See the example in
[Cookbook: Volume curve](../examples/project_volume_curve.md).

## Identity and equality

Managers compare equal when their identification dictionaries match. Use `manager.identification` to inspect the underlying primary keys. Call `Project.get(name="Apollo")` as a shortcut for `Project.filter(name="Apollo").get()` when you expect one match, or use bucket helpers such as `first()` when zero matches are acceptable.

For ORM foreign keys, GeneralManager exposes raw ID helpers such as
`project.customer_id` alongside relation accessors such as `project.customer`.
Use the raw ID helper when you only need the stored identifier and want to avoid
resolving the related manager.

Manager-valued lookups are accepted by the public query helpers. For example,
`Task.filter(project=project)` and `Task.exclude(project__in=[archived_project])`
forward single-id related managers as their scalar `identification["id"]` value
before the interface handles the query; composite identifiers are forwarded as
copied identification mappings. The scalar path applies only to an
identification mapping shaped exactly as `{"id": value}`; empty mappings,
single-key non-`"id"` mappings, and multi-key mappings are copied as mappings.
This normalization is shallow and only covers top-level lookup values plus
direct list/tuple items; nested containers and non-manager values are left
unchanged, and interface or bucket errors propagate.
`create()` returns a new manager for the
identification returned by the interface; `update()` refreshes and returns the
same manager instance; `delete()` invalidates the manager so later field reads
raise `InvalidManagerStateError`.

## Pattern recommendations

- Use descriptive attribute names that align with your GraphQL schema.
- Expose related managers via buckets instead of raw Django querysets so that permission checks remain consistent.

With these guidelines you can design manager hierarchies that remain understandable as your project grows.
