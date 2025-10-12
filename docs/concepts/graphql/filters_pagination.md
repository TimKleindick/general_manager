# Filtering and Pagination

The GraphQL layer reuses bucket semantics to expose flexible filtering and pagination without writing custom resolvers for every manager.

## Permission-aware filtering

Before executing a query, the resolver calls `getReadPermissionFilter()`. Permission classes translate their rules into Django-style filter/exclude dictionaries. The GraphQL layer applies these constraints to ensure clients only see authorised records.

## Query arguments

Each list field accepts `filter`, `exclude`, `order_by`, `page`, and `page_size` arguments. Filters support Django lookups (`name__icontains`, `total_capex__gte`, etc.) and automatic casting of measurements and dates. Bucket chaining happens server-side, so complex filters remain efficient.

## Pagination model

Pagination is page-based. Responses include a `page_info` object with:

- `total_count`
- `current_page`
- `total_pages`
- `page_size`

Clients can request `page_size=0` to retrieve only metadata when they need to calculate page counts without fetching data.

## Sorting

Use the `order_by` argument with field names or `-field` for descending order. Buckets validate the requested fields; invalid names trigger `ValidationError` with descriptive messages.

## Extending filters

Register custom filter input types by populating `GraphQL.graphql_filter_type_registry`. For example, you can add an enum to control domain-specific filters or expose nested filters for related managers.
