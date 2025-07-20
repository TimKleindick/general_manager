# CalculationInterface

`CalculationInterface` is used for managers that represent derived or calculated information. These managers do not store data in the database. Instead, they combine input values to produce results.

## Basic usage

Declare the required inputs with the `Input` class. Each input defines its type and optional choices or dependencies. The interface provides methods to generate combinations of inputs using a `CalculationBucket`. Calculations are exposed through methods decorated with `@graphQlProperty`.

```python
from datetime import date

from general_manager.interface.calculationInterface import CalculationInterface
from general_manager.manager import GeneralManager, Input, graphQlProperty

from yourapp.managers import Project

class ProjectSummary(GeneralManager):
    project: Project
    date: date

    class Interface(CalculationInterface):
        project = Input(Project)
        date = Input(date)

    @graphQlProperty
    def volume(self) -> int:
        return sum(
            v.volume
            for v in self.project.derivative_volume_list.filter(date=self.date)
        )
```

`graphQlProperty` turns a method into a read-only attribute and registers it as a resolver for GraphQL queries. The calculation runs lazily when the property is accessed.
Additional options allow properties to be marked as `filterable` or `sortable`. When set, buckets and GraphQL queries can filter or order by these properties.

To iterate over all possible combinations you can call `all()`, or filter by inputs:

```python
from yourapp.managers import ProjectSummary

for summary in ProjectSummary.all():
    print(summary.project, summary.date)

filtered = ProjectSummary.filter(project=my_project)
```

Because calculation managers have no persistent records, `create`, `update` and `deactivate` are not available.
