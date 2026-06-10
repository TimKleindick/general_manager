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

Calculation `@graph_ql_property` methods use run-scoped caching by default. The value is reused within one GraphQL request, calculation graph, bulk operation, or background run, then discarded.

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

## Step 4: Verify invalidation

For dependency-aware properties, update a derivative that contributes to the summary. The dependency tracker captures the relationship and invalidates the cache entry automatically.

```python
DerivativeVolume(id=volume_id).update(quantity=42)
```

## Step 5: Monitor cache usage

Enable Django cache logging or use Redis monitoring tools to ensure cache hits increase and invalidations behave as expected.

## Working-set reuse

For bulk-style calculations, prefer loading related rows once and indexing them in the active run context:

```python
from general_manager.cache import current_calculation_run_context

@graph_ql_property
def volume(self) -> int:
    ctx = current_calculation_run_context()
    if ctx is None:
        rows = DerivativeVolume.filter(
            derivative=self.derivative,
            revision=self.revision,
            search_date=self.search_date,
        )
        return rows.get(volume_date=self.target_date).quantity

    rows_by_date = ctx.index(
        key=("volume_rows", self.derivative.id, self.revision.id, self.search_date),
        loader=lambda: DerivativeVolume.filter(
            derivative=self.derivative,
            revision=self.revision,
            search_date=self.search_date,
        ),
        index_by=lambda row: row.volume_date,
    )
    return rows_by_date[self.target_date].quantity
```
