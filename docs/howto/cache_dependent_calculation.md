# Cache Dependent Calculations

Use the caching utilities to memoise expensive calculations without sacrificing correctness.

## Step 1: Define a calculation manager

```python
from datetime import date

import graphene

from core.managers import DerivativeVolume, Project
from general_manager.api.graphql import GraphQL
from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input, graph_ql_property

class ProjectSummary(GeneralManager):
    project: Project
    date: date

    class Interface(CalculationInterface):
        project = Input(Project)
        date = Input(date)

    @graph_ql_property
    def total_volume(self) -> int:
        return sum(
            derivative.volume
            for derivative in self.project.derivative_list.filter(date=self.date)
        )
```

## Step 2: Choose a cache scope

`@graph_ql_property` methods use run-scoped caching by default on every manager type. The value is reused within one GraphQL request, calculation graph, bulk operation, or background run, then discarded.

Use dependency-aware caching only when a calculation result is stable enough to reuse across requests:

```python
@graph_ql_property(cache="dependency")
def expensive_summary(self) -> int:
    return self.project.derivative_list.filter(date=self.date).count()
```

Disable caching for cheap or intentionally volatile values:

```python
@graph_ql_property(cache="none")
def cheap_label(self) -> str:
    return f"{self.project.name}: {self.date:%Y-%m-%d}"
```

## Step 3: Verify invalidation

For dependency-aware properties, update a derivative that contributes to the summary. The dependency tracker captures the relationship and invalidates the cache entry automatically.

```python
DerivativeVolume(id=volume_id).update(quantity=42)
```

## Step 4: Monitor cache usage

Enable Django cache logging or use Redis monitoring tools to ensure cache hits increase and invalidations behave as expected.

## Working-set reuse

For bulk-style calculations, build a run-scoped index directly from the source
bucket. The framework derives a stable run-local key from the bucket and key
spec, so repeated lookups in the same `CalculationRunContext` reuse the same
index without application-specific cache keys.

```python
@graph_ql_property
def volume(self) -> int:
    rows = DerivativeVolume.filter(
        derivative=self.derivative,
        revision=self.revision,
        search_date=self.search_date,
    )
    rows_by_date = rows.index_by("volume_date")
    return rows_by_date[self.target_date].quantity
```

Use `index_many("field_name")` when more than one row can share a key:

```python
@graph_ql_property
def daily_quantities(self) -> dict[date, int]:
    rows = DerivativeVolume.filter(
        derivative=self.derivative,
        revision=self.revision,
        search_date=self.search_date,
    )
    rows_by_date = rows.index_many("volume_date")
    return {
        volume_date: sum(row.quantity for row in volume_rows)
        for volume_date, volume_rows in rows_by_date.items()
    }
```

`index_by(...)` raises `DuplicateBucketIndexKeyError` when duplicate keys are
found. Composite keys use tuples of field names, for example
`rows.index_by(("project", "date"))`. `None` is a valid key value; missing fields
raise `MissingBucketIndexKeyError`, unsupported key specs raise
`UnsupportedBucketIndexKeySpecError`, unhashable keys raise
`UnhashableBucketIndexKeyError`, and indexes that exceed their row guardrail
raise `BucketIndexTooLargeError`.

Use `ensure_calculation_run_context` around custom bulk jobs or background tasks
that should share the same run cache but may already execute inside a GraphQL
request context.

Most application code should use the bucket methods directly. Lower-level
helpers can inspect `current_calculation_run_context` when they need to adapt
to an already active run.
