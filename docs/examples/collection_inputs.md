# Monthly summaries with native selections

A calculation can consume an entire employee, project and customer selection as
one value. This avoids a Cartesian product of separate scalar inputs. Existing
native KPI properties continue to expose the result.

In an installed application, with existing `Employee`, `Project` and `Customer`
managers, declare:

```python
from datetime import date

from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input, graph_ql_property


class WorkforceSummary(GeneralManager):
    month: date
    employees: list[Employee] | None
    projects: list[Project] | None
    customers: list[Customer] | None

    class Interface(CalculationInterface):
        month = Input.monthly_date(
            start=date(2020, 1, 1), end=date(2100, 12, 31), anchor="start"
        )
        employees = Input(list[Employee], required=False)
        projects = Input(list[Project], required=False)
        customers = Input(list[Customer], required=False)

    @graph_ql_property(cache="none")
    def selected_employee_count(self) -> int | None:
        # None means unrestricted; [] is explicitly empty in this application.
        return None if self.employees is None else len(self.employees)
```

Retain the application's normal read permissions and add its existing KPI
methods. The framework preserves `None` and `[]`; it does not decide which
employees, projects or customers are visible to the caller.

```python
summary = WorkforceSummary(
    month="2026-10-01",
    employees_id=["17", "18"],
    projects=[],
    customers=None,
)
assert summary.selected_employee_count == 2
assert WorkforceSummary(month="2026-10-01", employees=[]).selected_employee_count == 0
assert WorkforceSummary(month="2026-10-01").selected_employee_count is None
```

The constructor resolves each ID using the declared manager type. Existing
manager instances and identification mappings are also accepted. For a composite
manager, use complete constructor mappings, for example
`selections=[{"label": "scenario", "values": [2, 1, 2]}]` when the referenced
manager declares `label = Input(str)` and `values = Input(list[int])`.

The generated single-summary query is:

```graphql
query Summary($month: Date!, $employees: [ID!], $projects: [ID!], $customers: [ID!]) {
  workforceSummary(
    month: $month
    employeesId: $employees
    projectsId: $projects
    customersId: $customers
  ) {
    month
    selectedEmployeeCount
    employees { id }
  }
}
```

```json
{
  "month": "2026-10-01",
  "employees": ["17", "18"],
  "projects": [],
  "customers": null
}
```

This requests one `WorkforceSummaryType`. `employeesId` deliberately follows
the existing `_id` constructor alias; it is not renamed `employeesIds`. Lists
retain order and duplicates. For required collections use `Input(list[T])`;
the GraphQL argument then gains outer non-null, but `[]` remains valid.

The owning `employees` field is checked first. If a selected employee fails its
manager-level read permission, GraphQL omits only that member and preserves the
order and duplicates of the permitted members. Field-level permissions still
apply to `id`, `name`, and other selected member fields.

Whole-list validators can enforce application constraints without a new descriptor:

```python
values = Input(list[int], validator=lambda values: len(values) <= 100)
choices = Input(list[int], possible_values=[[], [1, 2]])
```

The second input has two whole-selection choices. `values__in=[[1, 2], []]`
likewise selects alternative complete lists in Python; the original scalar
`employee__in=[1, 2]` still enumerates separate employee inputs. No JSON encoding
or GraphQL `JSONString` is involved.
