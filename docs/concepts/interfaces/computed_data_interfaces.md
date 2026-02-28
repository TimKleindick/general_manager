# Computed Data Interfaces

`CalculationInterface` (`general_manager.interface.CalculationInterface`) models dynamic data that is derived rather than stored. It combines declarative inputs with lazy evaluation to calculate results on demand.

## Declaring inputs

Inputs use the `Input` descriptor to specify the expected type and optional value providers.

```python
from datetime import date

from general_manager.interface import CalculationInterface
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

Because calculation managers do not persist data, `create`, `update`, and `delete` are unavailable. They still participate in dependency tracking, so cached calculations refresh when related managers change.

## Input helpers

- Use `required=False` for optional inputs. Calculation metadata and parsing now treat those fields as nullable and default them to `None` when omitted.
- Use `possible_values` to restrict input choices or provide a callable for dynamic options.
- Use `min_value`, `max_value`, and `validator` for scalar constraints without eagerly enumerating every allowed value.
- Use `Input.date_range(...)`, `Input.monthly_date(...)`, and `Input.yearly_date(...)` for structured date domains such as month-end or year-start inputs.
- Use domain objects like `DateRangeDomain` and `NumericRangeDomain` when you want structured range metadata instead of a plain eager list.
- Set `GENERAL_MANAGER["VALIDATE_INPUT_VALUES"] = True` or `GENERAL_MANAGER_VALIDATE_INPUT_VALUES = True` to enforce `possible_values` membership outside `DEBUG`.
- Use `depends_on` (or rely on callable signature introspection) to declare dependencies between inputs.
- Use `Input.from_manager_query(...)` to build manager-backed dependent inputs without repeating query boilerplate.
- Call `Input.cast()` in custom workflows to convert raw data into the expected types when bypassing the interface.
