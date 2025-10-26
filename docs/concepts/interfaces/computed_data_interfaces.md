# Computed Data Interfaces

`CalculationInterface` (`general_manager.interface.calculation_interface.CalculationInterface`) models dynamic data that is derived rather than stored. It combines declarative inputs with lazy evaluation to calculate results on demand.

## Declaring inputs

Inputs use the `Input` descriptor to specify the expected type and optional value providers.

```python
from datetime import date

from general_manager.interface.calculation_interface import CalculationInterface
from general_manager.manager import GeneralManager, Input, graph_ql_property

class ProjectSummary(GeneralManager):
    project: Project
    date: date

    class Interface(CalculationInterface):
        project = Input(Project)
        date = Input(date)
```

## Computing values

Expose computed attributes with `@graph_ql_property`. The decorator registers the method as a GraphQL field and caches results per manager instance.

```python
    @graph_ql_property
    def total_volume(self) -> int:
        return sum(
            derivative.volume
            for derivative in self.project.derivative_list.filter(date=self.date)
        )
```

## Iterating combinations

Call `ProjectSummary.all()` to iterate through all possible input combinations. Filter inputs with keyword arguments:

```python
for summary in ProjectSummary.filter(project=my_project):
    print(summary.date, summary.total_volume)
```

Because calculation managers do not persist data, `create`, `update`, and `deactivate` are unavailable. They still participate in dependency tracking, so cached calculations refresh when related managers change.

## Input helpers

- Use `possible_values` to restrict input choices or provide a callable for dynamic options.
- Use `depends_on` (or rely on callable signature introspection) to declare dependencies between inputs.
- Call `Input.cast()` in custom workflows to convert raw data into the expected types when bypassing the interface.
