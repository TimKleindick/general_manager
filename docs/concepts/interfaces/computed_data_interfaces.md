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

Resolved input values are memoized on each calculation instance. This includes
manager inputs: repeated `self.project` access on one calculation returns the
same wrapper, while a new calculation instance resolves a fresh wrapper from the
current database state.

## Computing values

Expose computed attributes with `@graph_ql_property`. The decorator registers the method as a GraphQL field and caches results in the active run context by default for every manager type.

```python
    @graph_ql_property
    def total_volume(self) -> int:
        return sum(
            derivative.volume
            for derivative in self.project.derivative_list.filter(date=self.date)
        )
```

Use `@graph_ql_property(cache="dependency")` when a value should be reused across requests and invalidated when its source managers change. Use `cache="none"` for cheap values.

## Iterating combinations

Call `ProjectSummary.all()` to iterate through all possible input combinations. Filter inputs with keyword arguments:

```python
for summary in ProjectSummary.filter(project=my_project):
    print(summary.date, summary.total_volume)

for summary in ProjectSummary.filter(project_id=my_project.id):
    print(summary.date, summary.total_volume)
```

For manager-typed inputs, filters accept either the manager instance (`project=...`) or its identifier (`project_id=...`). Nested lookups such as `project__name__icontains=...` continue to target fields on the input manager.

Because calculation managers do not persist data, `create`, `update`, and `delete` are unavailable. They still participate in dependency tracking when a property opts into dependency-aware caching.

## Input helpers

- Use `required=False` for optional inputs. Calculation metadata and parsing now treat those fields as nullable and default them to `None` when omitted.
- Use `possible_values` to restrict input choices or provide a callable for dynamic options.
- Callable or bucket-backed `possible_values` are not resolved during ordinary input casting unless a custom normalizer needs them. They are still resolved when enumerating calculation combinations or validating allowed values.
- When `possible_values` is callable and resolution happens inside a managed run, GeneralManager caches the provider result for that run. Providers should be pure for a given set of declared dependency input values. If a provider returns a one-shot iterator or generator, GeneralManager materializes it before caching so later reads in the same run see the same values.
- Use `min_value`, `max_value`, and `validator` for scalar constraints without eagerly enumerating every allowed value.
- Employ `Input.date_range(...)`, `Input.monthly_date(...)`, and `Input.yearly_date(...)` for structured date domains such as month-end or year-start inputs.
- Prefer domain objects such as `DateRangeDomain` and `NumericRangeDomain` when you need structured range metadata rather than an eager list.
- Set `GENERAL_MANAGER["VALIDATE_INPUT_VALUES"] = True` or `GENERAL_MANAGER_VALIDATE_INPUT_VALUES = True` to enforce `possible_values` membership outside `DEBUG`.
- Use `depends_on` (or rely on callable signature introspection) to declare dependencies between inputs.
- Use `Input.from_manager_query(...)` to build manager-backed dependent inputs without repeating query boilerplate.
- Call `Input.cast()` in custom workflows to convert raw data into the expected types when bypassing the interface.
