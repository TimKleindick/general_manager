# Filtering and Sorting Measurements

Measurements are stored as two columns: one for the normalised magnitude and one for the unit. The descriptor converts them back into `Measurement` objects when you load a model. This design makes filtering and sorting straightforward.

## Filtering with managers

When you call `filter()` on a manager or bucket, lookups automatically target the magnitude column:

```python
heavy_projects = Project.filter(total_capex__gte="1_000_000 EUR")
```

The value is converted to the base unit before the filter runs, so you can provide strings or `Measurement` instances in any compatible unit.

## Sorting results

Sorting by measurement fields works because the magnitude column is indexed:

```python
projects = Project.all().sort("total_capex")
```

Use descending order for highest values first:

```python
projects = Project.all().sort(("total_capex", True))
```

## GraphQL filters

When the GraphQL schema is generated, measurement fields expose filter arguments that accept numbers or strings. Combine them with pagination to implement range queries efficiently.

## Aggregations

Grouping and aggregation operations sum compatible units automatically. For example, `group_by("currency")` on a bucket of financial managers produces totals in their original currency. Convert values explicitly in GraphQL resolvers if you need a single reporting currency.
