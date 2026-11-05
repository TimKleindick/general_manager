# Computed Data Interfaces

`CalculationInterface` (`general_manager.interface.CalculationInterface`) models dynamic data that is derived rather than stored. It combines declarative inputs with lazy evaluation to calculate results on demand.

## Declaring inputs

Inputs use the `Input` descriptor to specify the expected type and optional value
providers. This example uses the `Project` manager from the
[quickstart](../../quickstart.md). Define `ProjectSummary` after `Project` in
`projects/managers.py`, or import `Project` before defining the summary in
another startup-loaded module.

```python
from datetime import date

from general_manager.interface import CalculationInterface
from general_manager.manager import GeneralManager, Input, graph_ql_property

class ProjectSummary(GeneralManager):
    project: Project
    date: date

    class Interface(CalculationInterface):
        project = Input.from_manager_query(Project)
        date = Input(
            date,
            possible_values=[date(2024, 1, 1), date(2024, 2, 1)],
        )

    @graph_ql_property
    def label(self) -> str:
        return f"{self.project.name}: {self.date.isoformat()}"
```

After restarting the application, construct a calculation by supplying one
value for every required input:

```python
from datetime import date
from projects.managers import Project, ProjectSummary

my_project = Project.get(name="Apollo")
summary = ProjectSummary(project=my_project, date=date(2024, 1, 1))
assert summary.date == date(2024, 1, 1)
assert summary.label == "Apollo: 2024-01-01"
```

This identifies a calculation; it does not create a database row. The declared
domains also make enumeration finite: existing projects paired with two dates.
For direct Python use, bind required inputs without a domain in the constructor
or a Python bucket filter before enumeration; an unconstrained required input
raises `InvalidPossibleValuesError`. Generated GraphQL lists authorize their
base calculation domain before applying client input filters. A GraphQL filter
therefore does not replace a domain needed to enumerate that base; declare a
finite domain when exposing the calculation as a list. See
[query authorization](../graphql/security.md#query-operands-and-search-projections).

## Computing values

Expose computed attributes with `@graph_ql_property`. The decorator registers the method as a GraphQL field and caches results in the active run context by default for every manager type.

The `label` property above combines a related manager's name with the date
input. More complex properties can query related buckets or combine measurements
using the same declaration pattern.

Use `@graph_ql_property(cache="dependency")` when a value should be reused across requests and invalidated when its source managers change. Use `cache="none"` for cheap values.

`query_annotation` does not execute on a `CalculationInterface` manager.
`CalculationBucket` enumerates input combinations and reads computed properties
through their Python getters for filtering, exclusion, and sorting; it has no
queryset on which to apply an ORM expression or annotation callback. A
database-backed input does not turn the calculation's properties into database
columns. Set `filterable=True` and `sortable=True` for generated GraphQL controls,
and narrow the input domain before expensive property evaluation. Caching can
reuse getter results but does not translate calculations into SQL. See the
[property and query annotation guide](../../howto/expose_via_graphql.md#calculationinterface-does-not-execute-query_annotation)
for an executable-style example and alternatives.

## Iterating combinations

Call `ProjectSummary.all()` to iterate through the declared project/date
combinations. In the shell where `my_project` refers to the quickstart's Apollo
record, filter inputs with keyword arguments:

```python
for summary in ProjectSummary.filter(project=my_project):
    print(summary.date, summary.label)

for summary in ProjectSummary.filter(project_id=my_project.id):
    print(summary.date, summary.label)
```

Each loop prints two rows, one for 1 January and one for 1 February 2024.
The label combines the project's name with that row's date.

For manager-typed inputs, filters accept either the manager instance (`project=...`) or its identifier (`project_id=...`). Nested lookups such as `project__name__startswith=...` continue to target fields on the input manager.

The following GraphQL fragment is a separate `ProjectCommercial` example from
[the GraphQL task guide](../../howto/expose_via_graphql.md#filter-calculation-managers-by-manager-input);
it assumes that manager and its `target_date` input have been declared. It is
not a query for the `ProjectSummary` class above.

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

## Resolution and lifecycle details

The examples above are the normal application path. These details matter when
diagnosing input resolution or implementing a custom calculation interface.

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

## Input helpers

### Typed list inputs

Use `Input(list[T], required=False)` when one calculation consumes a whole
selection. `T` can be `str`, `bool`, `int`, `float`, `Decimal`, `date`,
`datetime`, `Measurement`, or a concrete manager class. `typing.List[T]` is
equivalent. The manager attribute can be annotated `list[T] | None`; express
descriptor nullability with `required=False` rather than an outer union.

```python
class SelectionSummary(GeneralManager):
    employees: list[Employee] | None

    class Interface(CalculationInterface):
        employees = Input(list[Employee], required=False)

summary = SelectionSummary(employees=[alice, bob])
same = SelectionSummary(employees_id=[alice.id, bob.id])
```

Omission and explicit `None` resolve to `None`. `[]` remains an empty list;
it is also valid for a required input. Applications define what unrestricted
and empty selections mean. The framework does not replace `None` with `.all()`.
Lists preserve order and duplicates. Each scalar/member is cast using existing
input rules; manager members accept manager instances, native IDs, or constructor
mappings. Composite managers require structured mappings rather than opaque IDs.

The Python container must be a list. Strings, mappings, tuples, sets and
generators are not implicitly expanded. Null members, nested lists, nullable
element annotations and arbitrary object element types are unsupported.
`Input(list)` retains its legacy behavior; adopt the parameterized declaration
to opt in to typed list behavior.

The `normalizer` and `validator` operate on the entire list, including `[]`,
with declared dependencies. `None` bypasses callbacks. `cast()` performs
conversion/normalization; constructor validation additionally applies validators
and possible-value membership. Use a validator for length/uniqueness constraints;
`min_value` and `max_value` are scalar-only. `possible_values` describes whole
selections, for example `[[], [1, 2]]`, not possible members. Membership retains
the existing `DEBUG`/`VALIDATE_INPUT_VALUES` policy.

Each list is one constructor value and one enumeration candidate. Python exact
filters bind a complete selection; `values__in=[[1, 2], []]` selects alternatives
of complete lists. Scalar `__in` is unchanged. Other collection lookups are
unsupported. Without a domain or explicit selection constraint, a required list
cannot be enumerated; an optional list contributes only `None`. Generated
GraphQL detail arguments support collections; this feature does not add collection
fields to the GraphQL list filter language.

Normalized selections determine identity and cache keys: omission equals `None`,
but `None`, `[]`, `[a, b]`, `[b, a]` and `[a, a]` are distinct. Equivalent manager
instances, IDs and identification mappings share identity. Typed input lists and
public identification containers are defensive copies, so mutating a caller's
list or an exposed list does not change an existing calculation. Resolved manager
wrappers remain instance-local and each member contributes dependencies when read.
Use a new calculation for a different selection.

See the [monthly selection summary cookbook](../../examples/collection_inputs.md)
for a complete native GraphQL query and the [GraphQL API contract](../../api/graphql.md#typed-calculation-list-arguments).

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

This independent pattern assumes your application's `Project` exposes
`started_on`, optional `closed_on`, and integer `capacity` attributes. Those
fields are not part of the minimal quickstart Project.

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
`quantity` uses scalar bounds for cheap checks; bounds do not supply an
enumeration domain. Supply an exact `quantity` when constructing this calculation
in Python or filtering a Python bucket, or add a finite `possible_values` domain
before using `.all()`. For generated GraphQL lists, provide that enumerable
domain even when the client sends a quantity filter: authorization runs first.
The validator supplies the
dependency-aware rule. If you omit `depends_on`, callable parameter names are
used as dependencies; specifying it explicitly is clearer when callbacks accept
optional or keyword-only arguments.
