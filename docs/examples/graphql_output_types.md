# Return Typed Output Objects Through GraphQL

Use `GraphQLType` when a GraphQL response needs a nested object shape but the
value does not need a manager identity, lifecycle, or independent operation.
This recipe defines a frozen output value, returns it from a
`@graph_ql_property`, and queries the generated nested object.

## Define the output value

Concrete `GraphQLType` subclasses become frozen dataclass-style values. Their
annotated fields become GraphQL fields; `field(default_factory=...)` supplies a
fresh default for each Python instance.

```python title="myapp/graphql_types.py"
from __future__ import annotations

from dataclasses import field

from general_manager import GraphQLType


class ProjectHour(GraphQLType):
    task_id: int
    hours: float
    note: str | None = None
    tags: list[str] = field(default_factory=list)
```

The generated GraphQL object is `ProjectHourType`. Required annotations become
non-null fields, `T | None` becomes nullable, and collection element
nullability follows the element annotation. `GraphQLType` declarations do not
create root queries, mutations, subscriptions, filters, or input objects.

Import the declaration module during application startup, before GeneralManager
builds the schema:

```python title="myapp/apps.py"
from django.apps import AppConfig


class MyAppConfig(AppConfig):
    name = "myapp"

    def ready(self) -> None:
        from . import graphql_types  # noqa: F401
        from . import managers  # noqa: F401
```

## Return the value from a GraphQL property

The output type is available only through a field that references it. A
calculation manager is useful here because its generated detail field accepts
the declared input and exposes the property result:

```python title="myapp/managers.py"
from __future__ import annotations

from general_manager import GeneralManager, graph_ql_property
from general_manager.interface import CalculationInterface
from general_manager.manager import Input

from .graphql_types import ProjectHour


class ProjectHoursSummary(GeneralManager):
    class Interface(CalculationInterface):
        project_id = Input(int, possible_values=(42,))

    @graph_ql_property(cache="none")
    def hours(self) -> list[ProjectHour]:
        return [
            ProjectHour(
                task_id=7,
                hours=8.5,
                note="Ready for review",
                tags=["billable", "forecast"],
            )
        ]
```

The owning manager's property is the authorization boundary. Nested manager
fields keep their manager-level read checks, but scalar and nested
`GraphQLType` fields do not receive an independent permission check. Omit or
pre-authorize sensitive values before constructing the output object.

## Query the generated object

The calculation manager's generated detail field uses camel-case GraphQL names.
The output object can be selected like any other GraphQL object:

```graphql
query ProjectHours {
  projectHoursSummary(projectId: 42) {
    hours {
      taskId
      hours
      note
      tags
    }
  }
}
```

The response has the nested shape declared by `ProjectHour`:

```json
{
  "data": {
    "projectHoursSummary": {
      "hours": [
        {
          "taskId": 7,
          "hours": 8.5,
          "note": "Ready for review",
          "tags": ["billable", "forecast"]
        }
      ]
    }
  }
}
```

The output mapper accepts scalar annotations, `Measurement`, registered
managers, registered output types, and `list[T]`, `tuple[T, ...]`, or `set[T]`.
Bare collections, fixed-length tuples, `Any`, `Annotated[...]`, unresolved
references, and unions with more than one non-null member fail schema
generation with a field-specific error. The output-type contract is available
from GeneralManager 0.75.0.
