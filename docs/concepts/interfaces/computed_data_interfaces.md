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

`CalculationInterface` is a capability-backed shell rather than a persistence
interface. It collects `Input(...)` descriptors from the interface class during
manager creation, stores them in `input_fields`, and resolves them lazily on each
calculation instance. The lifecycle scans only the concrete interface class's own
attributes and skips dunder names; inherited `Input` descriptors and a manually
assigned `input_fields` mapping are not merged by that lifecycle step. If two
class attributes use the same name, normal Python class creation already leaves
only the last attribute value for the lifecycle to collect. The generated
interface subclass receives a `_parent_class` backlink to the manager during
post-create.

The resolved values cache is instance-local and stores each input's cast result,
including the resolved `GeneralManager` wrapper object for manager-typed inputs.
Repeated attribute access on one calculation reuses values while a new
calculation starts fresh. The cache has no cross-instance, async, or thread-level
invalidation contract. The public `get_data()` method raises
`NotImplementedError("Calculations do not store data.")`. Managers still inherit
`create()`, `update()`, and `delete()` from `GeneralManager`, but calculation
interfaces configure no create, update, or delete capability, so those inherited
mutation paths are unsupported and fail when they require the missing capability.
Query helpers (`all`, `filter`, and `exclude`) build `CalculationBucket`
instances. `all()` enumerates every input-domain combination, `filter(**kwargs)`
keeps combinations matching the same lookup grammar as `CalculationBucket`, and
`exclude(**kwargs)` removes matching combinations. These helpers create buckets
from the generated interface's `_parent_class`, so a malformed calculation
interface can raise `InvalidCalculationInterfaceError`; invalid input domains,
dependency callbacks, filter values, or normalization/parsing failures propagate
as their original `TypeError`, `KeyError`, or `ValueError`.
Use `field` or `field__lookup` keys for calculation filters. Python-level
lookups support `exact`, `lt`, `lte`, `gt`, `gte`, `contains`, `startswith`,
`endswith`, and `in`. Manager-typed inputs also support `<input>_id` aliases and
nested manager lookups such as `project__name__startswith`. Unknown input or
derived-property names raise `UnknownInputFieldError`.

The public metadata helpers are input-oriented. `get_attribute_types()` returns
one metadata row per input with the exact keys `"type"`, `"default"`,
`"is_editable"`, `"is_required"`, and `"is_derived"`. Calculation metadata rows
always use `default=None`, `is_editable=False`, and `is_derived=False`; the
required flag mirrors `Input.required`. `get_attributes()` returns lazy
accessors that resolve dependent inputs first, cache each cast value on that
calculation instance, and track cached manager inputs when they are reused.
If an input key is absent from an instance's identification mapping, the
accessor passes `None` into `Input.cast()`; required-input enforcement happens
in validation flows rather than in the accessor itself. `get_field_type(name)`
returns the declared input type and raises `KeyError` for unknown input names.

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

For manager-typed inputs, filters accept either the manager instance (`project=...`) or its identifier (`project_id=...`). Nested lookups such as `project__name__startswith=...` continue to target fields on the input manager.

Generated GraphQL list fields expose manager-typed calculation inputs through
the same nested relation-filter shape as persisted managers:

```graphql
query ProjectCommercials($projectId: ID!) {
  projectCommercialList(filter: {project: {id: $projectId}}) {
    items {
      project { id }
      targetDate
    }
  }
}
```

The resolver normalizes this filter to the Python lookup
`project__id=<projectId>`.

This inference applies when calculation metadata does not already describe the
field as a relation; explicit relation metadata remains authoritative. For the
declaration and query workflow, see [Filter calculation managers by manager
input](../../howto/expose_via_graphql.md#filter-calculation-managers-by-manager-input),
the [calculation filter recipe](../../examples/graphql_queries.md#filter-a-calculation-by-manager-input),
and the [GraphQL API compatibility note](../../api/graphql.md#manager-typed-calculation-input-filters).

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

`Input.cast()` is the public conversion hook used by interfaces. It returns
`None` unchanged, parses `date` and `datetime` inputs with Python's ISO parsers,
builds manager inputs from either an id or keyword mapping, and parses
`Measurement` strings. `date` inputs convert native `datetime` values to dates;
`datetime` inputs convert native dates to midnight datetimes. Existing manager
instances and already-matching scalar values still pass through normalization.
Conversion errors are not wrapped: invalid ISO dates and measurements raise
`ValueError`, constructor/callback signature problems raise `TypeError`, and
missing declared dependencies raise `KeyError`. `cast()` does not enforce scalar
bounds, possible-value membership, or the validator callback by itself; those
checks are separate interface validation steps. The optional `identification`
mapping supplies current dependency values to dynamic possible-values providers,
normalizers, and validators.

## Cookbook: constrained dependent inputs

Use structured domains when you want GraphQL and calculation enumeration to
understand the shape of an input instead of receiving a plain list:

```python
from datetime import date

from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input, graph_ql_property

class MonthlyProjectSummary(GeneralManager):
    project: Project
    as_of: date
    quantity: int

    class Interface(CalculationInterface):
        project = Input.from_manager_query(Project)
        as_of = Input.monthly_date(
            start=lambda project: project.started_on,
            end=lambda project: project.closed_on or date.today(),
            anchor="end",
            depends_on=["project"],
        )
        quantity = Input(
            int,
            min_value=1,
            max_value=100,
            validator=lambda quantity, project: quantity <= project.capacity,
            depends_on=["project"],
        )

    @graph_ql_property
    def label(self) -> str:
        return f"{self.project.name}: {self.as_of:%Y-%m}"
```

The `project` input enumerates manager instances through `Project.all()`.
`as_of` builds a `DateRangeDomain` once the project dependency is known and
normalizes arbitrary dates in a month to the configured month-end anchor.
`Input.monthly_date()` accepts `anchor="start"`/`"month_start"` and
`anchor="end"`/`"month_end"`; `Input.yearly_date()` accepts the equivalent
year-start and year-end anchors.
`quantity` uses scalar bounds for cheap checks and a validator for the
dependency-aware rule. If you omit `depends_on`, callable parameter names are
used as dependencies; specifying it explicitly is clearer when callbacks accept
optional or keyword-only arguments.
