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

## Step 2: Cache the computation

GraphQlProperty computations are cached by default and exposed via the GraphQL API.

## Step 4: Verify invalidation

Update a derivative that contributes to the summary. The dependency tracker captures the relationship and invalidates the cache entry automatically.

```python
DerivativeVolume(id=volume_id).update(quantity=42)
```

## Step 5: Monitor cache usage

Enable Django cache logging or use Redis monitoring tools to ensure cache hits increase and invalidations behave as expected.
