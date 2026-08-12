# Empty Grouped GraphQL Pagination

## Problem

Generated GraphQL list resolvers apply grouping before pagination. When filters
produce no records, grouping returns an empty `GroupBucket`. Supplying `page` or
`pageSize` then causes `apply_pagination()` to slice that bucket, but
`GroupBucket.__getitem__()` intentionally raises `EmptyGroupBucketSliceError`
for an empty slice. The exception escapes through GraphQL, making the list field
`null` instead of returning an empty page.

## Public behavior

A generated list query that combines `groupBy` with pagination and has no
matching records resolves successfully. Its `items` field is an empty list and
its pagination metadata continues to report zero total items. Pagination of
non-empty grouped and ungrouped buckets retains its current behavior.

## Design

Keep the fix in `apply_pagination()`, where GraphQL pagination policy is
implemented. After validating pagination arguments and determining that
pagination was requested, detect an already-empty `GroupBucket` and return it
unchanged instead of slicing it. This preserves the grouped bucket shape needed
by GraphQL serialization while avoiding the invalid empty slice.

Do not change `GroupBucket.__getitem__()`: its empty-slice error remains part of
the bucket-level contract. Do not catch `EmptyGroupBucketSliceError` broadly,
because slices of non-empty grouped buckets that select no groups are outside
this issue and should retain their current behavior.

## Error handling and compatibility

Negative `page` and `pageSize` values must still raise
`InvalidPaginationValueError`, including when the bucket is empty. Default page
and page-size behavior remains unchanged for non-empty buckets. No schema,
storage, dependency, or public API changes are required.

## Verification

Add a unit regression test that creates an empty grouped bucket, paginates it,
and asserts that the same empty `GroupBucket` is returned without an exception.
Add a GraphQL integration test that filters a generated list to zero records,
passes both `groupBy` and `pageSize`, and asserts that the response has no errors,
contains `items: []`, and reports `totalCount: 0`.

Run each regression test before implementation to confirm the expected failure,
then rerun the focused unit and integration tests after the fix. Finish with
Ruff, mypy, and the broader test suite if the focused checks pass.
