# GraphQL Compound and Manager-Relation Sorting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow generated GraphQL list fields to sort by multiple root or direct-manager keys while retaining one shared `reverse` direction.

**Architecture:** Schema generation will expose `sortBy` as a list of manager-specific enum values and flatten one-hop manager scalar paths with `__`. The resolver will normalize Graphene enum inputs into one ordered tuple passed to `Bucket.sort()`, while in-memory bucket implementations will translate `__` paths to Python attribute traversal. ORM buckets will continue using Django's native `__` ordering syntax.

**Tech Stack:** Python 3.12+, Django ORM, Graphene/GraphQL, pytest/Django TestCase, Ruff, mypy.

## Global Constraints

- Keep the existing global `reverse` Boolean; it applies to every sort key.
- Preserve inline singleton syntax such as `sortBy: name` through GraphQL list coercion.
- Typed GraphQL variables must use a list of the generated enum after this change.
- Expand only one direct manager-relation hop.
- Exclude collection relations, nested manager relations, and related computed GraphQL properties.
- Do not add dependencies.
- Global search sorting is out of scope.

## File map

- `src/general_manager/api/graphql.py`: build manager-aware sort enums and expose list-valued `sortBy` arguments in both generated list-field paths.
- `src/general_manager/api/graphql_resolvers.py`: normalize zero, one, or many Graphene enum values and call `Bucket.sort()` once.
- `src/general_manager/bucket/calculation_bucket.py`: resolve flattened `__` relation paths during in-memory sorting.
- `src/general_manager/bucket/group_bucket.py`: resolve the same paths when sorting grouped manager objects.
- `src/general_manager/bucket/request_bucket.py`: resolve the same paths when sorting materialized request-backed manager objects.
- `tests/unit/test_graphql_helpers.py`: resolver normalization and no-op behavior.
- `tests/unit/test_graph_ql.py`: enum member/value generation and list argument shape.
- `tests/unit/test_calculation_bucket.py`: calculation-bucket compound relation paths.
- `tests/integration/test_graphql_query.py`: ORM-backed end-to-end manager and compound sorting.
- `tests/integration/test_calculation_manager.py`: calculation-backed GraphQL compound sorting.
- `docs/howto/expose_via_graphql.md`: public syntax, semantics, exclusions, and migration note.

---

### Task 1: Normalize GraphQL compound sort input

**Files:**
- Modify: `src/general_manager/api/graphql_resolvers.py:140-225`
- Test: `tests/unit/test_graphql_helpers.py:285-325`

**Interfaces:**
- Consumes: a nullable Graphene enum, a list/tuple of Graphene enums, or already-normalized strings.
- Produces: `apply_sorting(queryset, sort_by, reverse)` calling `queryset.sort(tuple[str, ...], reverse=reverse)` exactly once, or returning `queryset` unchanged when no keys exist.

- [ ] **Step 1: Add failing unit tests for multiple and empty sort inputs**

Add these tests beside the existing `apply_sorting` tests:

```python
def test_apply_sorting_normalizes_multiple_enums_in_order(self) -> None:
    queryset = mock.Mock()
    sorted_queryset = mock.Mock()
    queryset.sort.return_value = sorted_queryset
    sort_by = [
        type("SortBy", (), {"value": "status"})(),
        type("SortBy", (), {"value": "employee__name"})(),
    ]

    result = apply_sorting(queryset, sort_by, reverse=True)

    assert result is sorted_queryset
    queryset.sort.assert_called_once_with(
        ("status", "employee__name"), reverse=True
    )


def test_apply_sorting_is_noop_for_empty_sort_list(self) -> None:
    queryset = mock.Mock()

    result = apply_sorting(queryset, [], reverse=True)

    assert result is queryset
    queryset.sort.assert_not_called()
```

Update `test_apply_sorting_normalizes_enum_and_propagates_reverse` and
`test_apply_query_parameters_still_sorts_direct_calls` to expect `("name",)`
instead of `"name"`.

- [ ] **Step 2: Run the resolver tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_graphql_helpers.py -k 'apply_sorting or apply_query_parameters_still_sorts' -q
```

Expected: the new multiple-value test fails because `apply_sorting` reads
`.value` from the list itself, and the singleton expectations fail because the
current helper passes a string.

- [ ] **Step 3: Implement one normalization path**

In `graphql_resolvers.py`, widen the `sort_by` annotation used by
`apply_sorting`, `apply_query_parameters`, and the generated list resolver to a
small alias:

```python
GraphQLSortInput = graphene.Enum | list[graphene.Enum] | tuple[graphene.Enum, ...] | None


def _normalize_sort_keys(sort_by: GraphQLSortInput) -> tuple[str, ...]:
    if not sort_by:
        return ()
    values = sort_by if isinstance(sort_by, (list, tuple)) else (sort_by,)
    return tuple(cast(str, getattr(value, "value", value)) for value in values)


def apply_sorting(
    queryset: Bucket[GeneralManager] | GroupBucket[GeneralManager],
    sort_by: GraphQLSortInput,
    reverse: bool,
) -> Bucket[GeneralManager] | GroupBucket[GeneralManager]:
    sort_keys = _normalize_sort_keys(sort_by)
    if not sort_keys:
        return queryset
    return queryset.sort(sort_keys, reverse=reverse)
```

Update the nearby docstrings to describe ordered tuples and the empty-list
no-op. Do not chain `sort()` calls.

- [ ] **Step 4: Run the focused resolver tests and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Run the whole helper module**

Run:

```bash
python -m pytest tests/unit/test_graphql_helpers.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/general_manager/api/graphql_resolvers.py tests/unit/test_graphql_helpers.py
git commit -m "feat: normalize compound GraphQL sort keys"
```

---

### Task 2: Generate manager-aware list sort enums

**Files:**
- Modify: `src/general_manager/api/graphql.py:830-875`
- Modify: `src/general_manager/api/graphql.py:1035-1055`
- Modify: `src/general_manager/api/graphql.py:1280-1300`
- Test: `tests/unit/test_graph_ql.py`

**Interfaces:**
- Consumes: `Interface.get_attribute_types()` metadata and `resolve_general_manager_type()`.
- Produces: `_sort_by_options(manager_class) -> type[graphene.Enum] | None`, where enum member names map to normalized bucket lookup strings; both generated list paths expose `graphene.List(graphene.NonNull(sort_enum))`.

- [ ] **Step 1: Add failing enum-generation tests**

Add a focused test in `GraphQLTests` using lightweight manager/interface
objects and patch relation resolution so the test changes no global registry:

```python
def test_sort_options_include_direct_manager_and_related_scalars(self) -> None:
    related_computed = SimpleNamespace(sortable=True, graphql_type_hint=str)
    root_computed = SimpleNamespace(sortable=True, graphql_type_hint=str)

    class EmployeeInterface:
        @classmethod
        def get_attribute_types(cls):
            return {
                "id": {"type": int},
                "name": {"type": str},
                "supervisor": {"type": object, "relation_kind": "direct"},
            }

        @classmethod
        def get_graph_ql_properties(cls):
            return {"computed_label": related_computed}

    class Employee:
        Interface = EmployeeInterface

    class ProjectInterface:
        @classmethod
        def get_attribute_types(cls):
            return {
                "title": {"type": str},
                "employee": {"type": Employee, "relation_kind": "direct"},
                "employee_list": {
                    "type": Employee,
                    "relation_kind": "collection",
                },
            }

        @classmethod
        def get_graph_ql_properties(cls):
            return {"score": root_computed}

    class Project:
        Interface = ProjectInterface

    def resolve(field_type, _registry):
        return Employee if field_type in {Employee, object} else None

    with patch(
        "general_manager.api.graphql.resolve_general_manager_type",
        side_effect=resolve,
    ):
        options = GraphQL._sort_by_options(Project)

    assert options is not None
    members = options._meta.enum.__members__
    assert members["title"].value == "title"
    assert members["employee"].value == "employee__id"
    assert members["employee__name"].value == "employee__name"
    assert members["score"].value == "score"
    assert "employee_list" not in members
    assert "employee__supervisor" not in members
    assert "employee__computed_label" not in members
```

- [ ] **Step 2: Add a failing test for list-valued generated arguments**

Exercise `_map_field_to_graphene_read()` for a collection relation and inspect
the generated field argument:

```python
sort_enum = type(
    "RelatedSortByOptions",
    (graphene.Enum,),
    {"name": "name"},
)
with (
    patch.object(GraphQL, "_create_filter_options", return_value=None),
    patch.object(GraphQL, "_sort_by_options", return_value=sort_enum),
):
    field = GraphQL._map_field_to_graphene_read(
        RelatedManager,
        "related_manager_list",
        {"relation_kind": "collection"},
    )

assert isinstance(field.args["sort_by"].type, graphene.List)
assert isinstance(field.args["sort_by"].type.of_type, graphene.NonNull)
assert field.args["sort_by"].type.of_type.of_type is sort_enum
```

Add a top-level-path test around `_add_queries_to_schema()` using
`self.general_manager_class` from `GraphQLTests`:

```python
class ManagerType(graphene.ObjectType):
    name = graphene.String()

sort_enum = type(
    "TestManagerSortByOptions",
    (graphene.Enum,),
    {"name": "name"},
)
GraphQL._query_fields = {}
with (
    patch("general_manager.api.graphql.issubclass", return_value=True),
    patch(
        "general_manager.interface.capabilities.orm.support.is_soft_delete_enabled",
        return_value=False,
    ),
    patch.object(GraphQL, "_create_filter_options", return_value=None),
    patch.object(GraphQL, "_sort_by_options", return_value=sort_enum),
):
    GraphQL._add_queries_to_schema(ManagerType, self.general_manager_class)

argument_type = GraphQL._query_fields["test_manager_list"].args["sort_by"].type
assert isinstance(argument_type, graphene.List)
assert isinstance(argument_type.of_type, graphene.NonNull)
assert argument_type.of_type.of_type is sort_enum
```

Reset `GraphQL._query_fields` in a `finally` block to the value captured before
the call. Together with the relation-list assertion, this proves the two schema
construction paths stay aligned.

- [ ] **Step 3: Run the new tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_graph_ql.py -k 'sort_options or list_valued_sort' -q
```

Expected: manager members are absent and `sort_by` is the enum itself rather
than a Graphene list.

- [ ] **Step 4: Implement bounded relation expansion**

Replace the list accumulator in `_sort_by_options()` with an insertion-ordered
mapping. Use this logic:

```python
sort_options: dict[str, str] = {}
for field_name, field_info in generalManagerClass.Interface.get_attribute_types().items():
    field_type = field_info["type"]
    related_manager = resolve_general_manager_type(
        field_type, GraphQL.manager_registry
    )
    if related_manager is None:
        sort_options[field_name] = field_name
        continue
    if field_info.get("relation_kind") == "collection":
        continue

    sort_options[field_name] = f"{field_name}__id"
    for related_name, related_info in related_manager.Interface.get_attribute_types().items():
        if related_info.get("relation_kind") == "collection":
            continue
        if resolve_general_manager_type(
            related_info["type"], GraphQL.manager_registry
        ) is not None:
            continue
        option_name = f"{field_name}__{related_name}"
        sort_options[option_name] = option_name
```

Keep the existing root `@graph_ql_property(sortable=True)` loop, assigning
`sort_options[prop_name] = prop_name`. Construct the enum with `sort_options`
instead of `{option: option for option in sort_options}`. Remove the unused
property return-type calculation if Ruff identifies it as dead code.

- [ ] **Step 5: Wrap both schema arguments in lists with non-null elements**

At both current `_sort_by_options()` call sites, use:

```python
attributes["sort_by"] = graphene.Argument(
    graphene.List(graphene.NonNull(sort_by_options))
)
```

The outer list remains nullable for omitted and `null` inputs, while individual
list elements are non-null.

- [ ] **Step 6: Run focused and full GraphQL unit tests**

Run:

```bash
python -m pytest tests/unit/test_graph_ql.py -k 'sort_options or list_valued_sort' -q
python -m pytest tests/unit/test_graph_ql.py tests/unit/test_graphql_helpers.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/general_manager/api/graphql.py tests/unit/test_graph_ql.py
git commit -m "feat: expose manager relation sort options"
```

---

### Task 3: Support flattened paths in in-memory buckets

**Files:**
- Modify: `src/general_manager/bucket/calculation_bucket.py:735-765`
- Modify: `src/general_manager/bucket/group_bucket.py:533-562`
- Modify: `src/general_manager/bucket/request_bucket.py:423-455`
- Test: `tests/unit/test_calculation_bucket.py`
- Test: `tests/unit/test_request_hardening.py`
- Test: `tests/integration/test_graphql_query.py`

**Interfaces:**
- Consumes: compound bucket keys containing ordinary names, existing dotted paths, or flattened GraphQL `__` paths.
- Produces: identical nested attribute traversal for calculation, request-backed, and grouped buckets; existing dotted programmatic paths remain valid.

- [ ] **Step 1: Add a failing calculation-bucket nested-path test**

Build a calculation manager whose possible values contain related manager-like
objects with `id` and `name`, then assert compound precedence:

```python
def test_sort_accepts_flattened_compound_relation_paths(self, _mock_parse):
    class Employee:
        def __init__(self, employee_id: int, name: str) -> None:
            self.id = employee_id
            self.name = name

    employees = [Employee(3, "Bob"), Employee(1, "Alice"), Employee(2, "Alice")]
    fields = {
        "rank": Input(type=int, possible_values=[1]),
        "employee": Input(type=Employee, possible_values=employees),
    }
    bucket = self._make_bucket_with_fields(fields)

    managers = list(bucket.sort(("rank", "employee__name", "employee__id")))

    assert [manager.employee.id for manager in managers] == [1, 2, 3]
```

Place this under the existing `TestGenerateCombinations` patch so
`_make_bucket_with_fields()` is available.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python -m pytest tests/unit/test_calculation_bucket.py -k flattened_compound_relation_paths -q
```

Expected: FAIL with `AttributeError` for `employee__name`.

- [ ] **Step 3: Normalize GraphQL paths for `attrgetter`**

In the manager-access sort branch, replace the getter construction with:

```python
getters = [attrgetter(key.replace("__", ".")) for key in sort_key]
```

Do not normalize stored `sort_key`; cache signatures and public dotted-path
compatibility must retain the caller's exact key tuple.

- [ ] **Step 4: Run the calculation test and verify GREEN**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Add a failing grouped relation-path integration test**

Before grouped coverage, add a request-bucket unit test in
`tests/unit/test_request_hardening.py`. Materialize two request-backed manager
objects whose direct `project` managers have different `id` and `name` values,
then assert:

```python
sorted_bucket = bucket.sort(("project__name", "project__id"))
assert [item.project.id for item in sorted_bucket] == [1, 2]
```

Run:

```bash
python -m pytest tests/unit/test_request_hardening.py -k compound_relation_sort -q
```

Expected: FAIL with `RequestBucketSortAttributeError` for
`project__name`.

- [ ] **Step 6: Use nested getters in `RequestBucket.sort()`**

Import `attrgetter` from `operator`, normalize each key with
`key.replace("__", ".")`, and preserve the existing
`RequestBucketSortAttributeError(instance, original_key)` wrapper when nested
resolution raises `AttributeError`. Run the request-bucket command from Step 5;
expected: PASS.

- [ ] **Step 7: Add a failing grouped relation-path integration test**

In `TestGraphQLQueryPagination`, create deterministic commercials/projects and
query a grouped project list with `sortBy: [commercials__name, name]`. Assert
the returned project names follow related-commercial name first and project
name second. Use `groupBy: ["name"]` so `apply_sorting()` exercises
`GroupBucket.sort()` rather than `DatabaseBucket.sort()`.

```python
query = """
query {
  projectList(
    groupBy: ["name"]
    sortBy: [commercials__name, name]
  ) {
    items { name commercials { name } }
  }
}
"""
```

Expected order for fixtures `(Zulu, Beta)`, `(Alpha, Zed)`, `(Alpha, Able)` is
`["Able", "Zed", "Beta"]`.

- [ ] **Step 8: Run the grouped test and verify RED**

Run:

```bash
python -m pytest tests/integration/test_graphql_query.py -k grouped_compound_relation_sort -q
```

Expected: FAIL because `GroupBucket.sort()` calls `getattr()` with the literal
`commercials__name`.

- [ ] **Step 9: Use nested getters in `GroupBucket.sort()`**

Import `attrgetter` from `operator`, construct getters once, and use them for
both directions:

```python
getters = [attrgetter(item.replace("__", ".")) for item in key]
sorted_data = sorted(
    self._data,
    key=lambda entry: tuple(getter(entry) for getter in getters),
    reverse=reverse,
)
```

- [ ] **Step 10: Run bucket and grouped tests**

Run:

```bash
python -m pytest tests/unit/test_calculation_bucket.py -k 'sort or flattened' -q
python -m pytest tests/unit/test_request_hardening.py -k 'sort or compound_relation_sort' -q
python -m pytest tests/integration/test_graphql_query.py -k 'grouped' -q
```

Expected: PASS, including existing dotted-path calculation coverage and grouped
pagination coverage.

- [ ] **Step 11: Commit**

```bash
git add src/general_manager/bucket/calculation_bucket.py src/general_manager/bucket/group_bucket.py src/general_manager/bucket/request_bucket.py tests/unit/test_calculation_bucket.py tests/unit/test_request_hardening.py tests/integration/test_graphql_query.py
git commit -m "feat: sort in-memory buckets by relation paths"
```

---

### Task 4: Prove generated GraphQL behavior end to end

**Files:**
- Test: `tests/integration/test_graphql_query.py`
- Test: `tests/integration/test_calculation_manager.py`
- Test: `tests/integration/test_graphql_request_sorting.py`
- Modify: `docs/howto/expose_via_graphql.md:140-200`

**Interfaces:**
- Consumes: list-valued generated `sortBy`, enum mappings from Task 2, resolver tuple normalization from Task 1, and bucket path support from Task 3.
- Produces: regression coverage and public documentation for inline singleton, compound root keys, manager identifiers, related scalar keys, shared reverse, empty/null input, relation-list parity, and variable migration.

- [ ] **Step 1: Add ORM GraphQL sorting scenarios**

In `TestGraphQLQueryPagination`, create three named `Commercials` managers and
three projects with explicit relation values:

```python
alpha = self.commercials.create(
    creator_id=None,
    name="Alpha",
    capex="1 USD",
    opex="1 USD",
)
zulu = self.commercials.create(
    creator_id=None,
    name="Zulu",
    capex="1 USD",
    opex="1 USD",
)
self.project.create(
    creator_id=None, name="Beta", description=None, commercials=zulu
)
self.project.create(
    creator_id=None, name="Zed", description=None, commercials=alpha
)
self.project.create(
    creator_id=None, name="Able", description=None, commercials=alpha
)
```

Add separate tests that assert:

```graphql
projectList(sortBy: commercials) { items { name } }
```

returns related commercial IDs in nondecreasing order, proving the manager enum
member maps to the foreign identifier without imposing an order on rows tied to
the same manager;

```graphql
projectList(sortBy: [commercials__name, name]) { items { name } }
```

returns `["Able", "Zed", "Beta"]`, using the related name as primary key and
project name as tie-breaker; and

```graphql
projectList(sortBy: [commercials__name, name], reverse: true) {
  items { name }
}
```

returns `["Beta", "Zed", "Able"]`, reversing both keys. Keep the existing
`sortBy: name` test unchanged as the singleton coercion regression.

Add variable coverage with:

```graphql
query SortedProjects($sort: [ProjectSortByOptions!]) {
  projectList(sortBy: $sort) { items { name } }
}
```

and variables `{"sort": ["commercials__name", "name"]}`. Use schema
introspection if Graphene emits a manager-specific enum name different from
`ProjectSortByOptions`; assert the actual stable generated name in the final
test.

- [ ] **Step 2: Add null, empty, exclusion, and relation-list assertions**

Execute three otherwise identical `projectList` queries using `sortBy: null`,
`sortBy: []`, and no `sortBy` argument. Assert their returned ID lists are
equal. Introspect `ProjectSortByOptions` and assert these exact conditions:

```python
project_enum_values = {
    value["name"] for value in response.json()["data"]["projectOptions"]["enumValues"]
}
commercials_enum_values = {
    value["name"]
    for value in response.json()["data"]["commercialsOptions"]["enumValues"]
}
assert "commercials" in project_enum_values
assert "commercials__name" in project_enum_values
assert not any(value.startswith("commercials__") and value.count("__") > 1 for value in project_enum_values)
assert "project_list" not in commercials_enum_values
```

Use an introspection query against the generated collection field already
covered by `test_map_field_to_graphene_resolves_manager_relations` and assert
its `sortBy` argument has `kind == "LIST"`, a non-null element type, and an
element enum name equal to the related manager's top-level sort enum. If the Project fixture exposes no
collection relation, add this assertion to the unit relation-list test from
Task 2 instead of introducing another integration-only manager.

- [ ] **Step 3: Run ORM integration tests**

Run:

```bash
python -m pytest tests/integration/test_graphql_query.py -q
```

Expected: PASS.

- [ ] **Step 4: Add request-backed GraphQL compound sorting coverage**

Add request-backed generated-list integration coverage in
`tests/integration/test_graphql_request_sorting.py`. Execute the real GraphQL
schema and request transport for:

- compound root keys with shared `reverse` behavior;
- a direct related scalar combined with a root key; and
- inline singleton coercion for a relation key.

Assert the returned ordering and that each query executes exactly one list
transport request.

- [ ] **Step 5: Add calculation GraphQL compound sorting coverage**

In `CustomMutationTest`, seed employees so two calculated-tax values tie and
execute the generated calculation list query with:

```graphql
taxCalculationList(sortBy: [calculated_tax, employee__name]) {
  items { calculatedTax { value } employee { name } }
}
```

Assert calculated tax is the primary order and employee name breaks the tie.
Repeat with `reverse: true` and assert both key directions reverse. This must
exercise the GraphQL resolver rather than calling `.sort()` directly.

- [ ] **Step 6: Run calculation integration tests**

Run:

```bash
python -m pytest tests/integration/test_calculation_manager.py -k 'graphql and sort' -q
```

Name the new tests
`test_graphql_compound_relation_sorting` and
`test_graphql_compound_relation_sorting_reverse`, then run those two node IDs
directly if the `-k` selection also collects unrelated tests. Expected: PASS.

- [ ] **Step 7: Update public documentation and changelog**

In `docs/howto/expose_via_graphql.md`, update the list-query contract and
examples to state:

- `sortBy` accepts an ordered list and inline singleton values;
- `reverse` applies to every key;
- a direct manager field sorts by identifier;
- `manager__field` sorts by a directly related scalar;
- collection and multi-hop relation sorting are not exposed; and
- typed variables must migrate from `ManagerSortByOptions` to
  `[ManagerSortByOptions!]` (with outer nullability matching client needs).

Do not edit released changelog sections; semantic-release owns versioned
changelog generation.

- [ ] **Step 8: Run documentation and relevant integration coverage**

Run:

```bash
python -m pytest tests/docs/test_public_api_docs_coverage.py tests/integration/test_graphql_query.py tests/integration/test_calculation_manager.py tests/integration/test_graphql_request_sorting.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add tests/integration/test_graphql_query.py tests/integration/test_calculation_manager.py tests/integration/test_graphql_request_sorting.py docs/howto/expose_via_graphql.md
git commit -m "docs: explain compound GraphQL sorting"
```

---

### Task 5: Validate the complete change

**Files:**
- Verify only; fix failures in the files owned by Tasks 1-4.

**Interfaces:**
- Consumes: all prior task deliverables.
- Produces: repository-level evidence that the feature is formatted, typed, and regression-safe.

- [ ] **Step 1: Run focused GraphQL and bucket tests**

```bash
python -m pytest tests/unit/test_graphql_helpers.py tests/unit/test_graph_ql.py tests/unit/test_calculation_bucket.py tests/unit/test_database_bucket.py tests/unit/test_request_hardening.py tests/integration/test_graphql_query.py tests/integration/test_calculation_manager.py tests/integration/test_graphql_request_sorting.py -q
```

Expected: PASS.

- [ ] **Step 2: Run formatting and lint checks**

```bash
ruff format src/general_manager/api/graphql.py src/general_manager/api/graphql_resolvers.py src/general_manager/bucket/calculation_bucket.py src/general_manager/bucket/group_bucket.py src/general_manager/bucket/request_bucket.py tests/unit/test_graphql_helpers.py tests/unit/test_graph_ql.py tests/unit/test_calculation_bucket.py tests/unit/test_request_hardening.py tests/integration/test_graphql_query.py tests/integration/test_calculation_manager.py tests/integration/test_graphql_request_sorting.py
ruff format --check src/general_manager/api/graphql.py src/general_manager/api/graphql_resolvers.py src/general_manager/bucket/calculation_bucket.py src/general_manager/bucket/group_bucket.py src/general_manager/bucket/request_bucket.py tests/unit/test_graphql_helpers.py tests/unit/test_graph_ql.py tests/unit/test_calculation_bucket.py tests/unit/test_request_hardening.py tests/integration/test_graphql_query.py tests/integration/test_calculation_manager.py tests/integration/test_graphql_request_sorting.py
ruff check src/general_manager/api/graphql.py src/general_manager/api/graphql_resolvers.py src/general_manager/bucket/calculation_bucket.py src/general_manager/bucket/group_bucket.py src/general_manager/bucket/request_bucket.py tests/unit/test_graphql_helpers.py tests/unit/test_graph_ql.py tests/unit/test_calculation_bucket.py tests/unit/test_request_hardening.py tests/integration/test_graphql_query.py tests/integration/test_calculation_manager.py tests/integration/test_graphql_request_sorting.py
```

Expected: both commands exit 0 with no findings.

- [ ] **Step 3: Run strict type checking**

```bash
mypy --strict
```

Expected: exit 0. If the repository's configured invocation differs, use the
same `mypy` command run by `.pre-commit-config.yaml` and record it in the handoff.

- [ ] **Step 4: Run the full suite**

```bash
python -m pytest
```

Expected: PASS.

- [ ] **Step 5: Review the final diff**

```bash
git status --short
git diff --check HEAD~4..HEAD
git diff --stat HEAD~4..HEAD
```

Confirm only the planned source, test, and documentation files are changed; no
generated files, local configuration, released changelog sections, or unrelated
cleanup is included.

- [ ] **Step 6: Commit any verification-only corrections**

If validation required source changes, first add a failing regression test,
then commit the focused correction:

Stage only the source and regression-test files changed for that correction,
then commit them with `git commit -m "fix: address compound sorting validation"`.

If no corrections were needed, do not create an empty commit.
