# Filtering and Sorting Measurements

Measurements are stored as two columns: one for the normalised magnitude and one for the unit. The descriptor converts them back into `Measurement` objects when you load a model. This design makes filtering and sorting straightforward.

These examples assume an ORM-backed `Project` with a `total_capex`
`MeasurementField(base_unit="EUR")`, such as the declaration in the
[factory guide](../../howto/factory_bulk_generate.md). Apply its migrations
before querying.

## Filtering with managers

When you call `filter()` on a manager or bucket, lookups automatically target the magnitude column:

```python
heavy_projects = Project.filter(total_capex__gte="1_000_000 EUR")
```

The value is converted to the base unit before the filter runs, so you can provide strings or `Measurement` instances in any compatible unit.

## Sorting results

Sorting uses the normalized magnitude column, so different compatible units
are ordered by their underlying quantity:

```python
projects = Project.all().sort("total_capex")
```

Use descending order for highest values first:

```python
projects = Project.all().sort("-total_capex")
```

## GraphQL filters

Generated measurement filter inputs use `MeasurementScalar`: supply unit-bearing
strings such as `"1000000 EUR"`, not bare JSON numbers. See the
[GraphQL filtering contract](../graphql/filters_pagination.md) for generated
field names and pagination.

## Aggregations

Grouping sums compatible measurement values. A field backed by EUR accepts EUR
values; it does not perform exchange-rate conversion during grouping. Convert
currencies explicitly with a chosen exchange rate before combining amounts in
a reporting currency. A grouping key such as `currency` must be a real declared
attribute on the application manager.
