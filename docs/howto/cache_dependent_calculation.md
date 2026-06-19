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

Use timeout caching when a value may be reused for a fixed interval:

```python
@graph_ql_property(cache="timeout", timeout=300)
def five_minute_summary(self) -> int:
    return self.project.derivative_list.count()
```

## Step 3: Verify invalidation

For dependency-aware properties, update a derivative that contributes to the summary. The dependency tracker captures the relationship and invalidates the cache entry automatically.

```python
DerivativeVolume(id=volume_id).update(quantity=42)
```

## Step 4: Warm selected properties proactively

Set `warm_up=True` only on properties that are safe and useful to compute
outside a user request. Warm-up is valid for dependency and timeout cache scopes:

```python
@graph_ql_property(cache="dependency", warm_up=True)
def expensive_summary(self) -> int:
    return self.project.derivative_list.filter(date=self.date).count()

@graph_ql_property(cache="timeout", timeout=300, warm_up=True)
def five_minute_summary(self) -> int:
    return self.project.derivative_list.count()
```

When enabled in settings, the framework can enumerate `Manager.all()`, execute
each opted-in property, and record warm-up recipes. Dependency entries can be
re-warmed after invalidation when a recipe exists. Timeout entries can be
refreshed before expiry by the built-in Celery Beat task or by a scheduler that
calls `refresh_due_graphql_warmup_recipes` directly. Schedulers that execute
management commands can run `graphql_warmup_refresh_due` instead.

Warm-up can be expensive because it starts from `.all()`. Keep automatic startup
warm-up disabled unless the deployment has a worker or startup budget for it,
and monitor warning logs for large manager enumerations.

```python
GENERAL_MANAGER = {
    "GRAPHQL_WARMUP_ENABLED": True,
    "GRAPHQL_WARMUP_STARTUP_ENABLED": True,
    "GRAPHQL_WARMUP_STARTUP_MODE": "enqueue",
    "GRAPHQL_WARMUP_BEAT_ENABLED": True,
}
```

Applications that use another scheduler can keep Beat disabled and run:

```bash
python manage.py graphql_warmup
python manage.py graphql_warmup_refresh_due --limit 1000
```

## Step 5: Monitor cache usage

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
