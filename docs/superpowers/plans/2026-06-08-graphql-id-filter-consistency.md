# GraphQL ID Filter Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make equality-style GraphQL filters for manager `id` fields use the `ID` scalar while preserving numeric ordered comparisons and backend identifier types.

**Architecture:** Special-case the conventional manager field name `id` in the existing filter-option generator, because that is the single point used by top-level and nested relation filter types. Extend the existing recursive filter normalizer to cast equality-style ID values through `Interface.input_fields["id"]` before passing them to manager backends.

**Tech Stack:** Python 3.12, Django, Graphene, pytest, Ruff, mypy

---

## File Structure

- Modify `tests/integration/test_graphql_relation_filters.py`: add the public regression query and generated schema scalar assertions.
- Modify `tests/unit/test_graph_ql.py`: add focused tests for scalar and list ID normalization.
- Modify `src/general_manager/api/graphql_search.py`: generate `ID` equality filters and normalize their runtime values.

### Task 1: Reproduce the Schema Mismatch

**Files:**
- Test: `tests/integration/test_graphql_relation_filters.py`

- [ ] **Step 1: Add the failing integration regression test**

Add this method to `GraphQLRelationFilterIntegrationTests`:

```python
def test_reuses_id_variable_for_detail_and_relation_filter(self):
    query = """
    query Issue247($id: ID!) {
        changerequest(id: $id) {
            id
        }
        changerequestfeasibilityList(
            filter: {changeRequest: {id: $id}}
        ) {
            items {
                id
                changeRequest { id }
            }
        }
    }
    """

    response = self.query(query, variables={"id": self.primary.id})

    self.assertResponseNoErrors(response)
    payload = response.json()["data"]
    self.assertEqual(payload["changerequest"]["id"], str(self.primary.id))
    self.assertEqual(
        [item["id"] for item in payload["changerequestfeasibilityList"]["items"]],
        [str(self.high_feasibility.id)],
    )
```

- [ ] **Step 2: Run the regression test and verify RED**

Run:

```bash
python -m pytest tests/integration/test_graphql_relation_filters.py::GraphQLRelationFilterIntegrationTests::test_reuses_id_variable_for_detail_and_relation_filter -q
```

Expected: FAIL with HTTP 400 and `Variable '$id' of type 'ID!' used in position expecting type 'Int'.`

- [ ] **Step 3: Add failing generated-schema type assertions**

Add this method to the same class:

```python
def test_id_filter_variants_use_identifier_and_numeric_scalars(self):
    filter_type = GraphQL.graphql_filter_type_registry[
        "ChangeRequestFilterTypeDepth2"
    ]

    self.assertIsInstance(filter_type._meta.fields["id"].type, graphene.ID)
    self.assertIsInstance(filter_type._meta.fields["id__exact"].type, graphene.ID)
    self.assertIsInstance(filter_type._meta.fields["id__in"].type, graphene.List)
    self.assertIs(filter_type._meta.fields["id__in"].type.of_type, graphene.ID)
    self.assertIsInstance(filter_type._meta.fields["id__gt"].type, graphene.Int)
```

Add `import graphene` near the other imports in
`tests/integration/test_graphql_relation_filters.py`.

- [ ] **Step 4: Run the schema assertion and verify RED**

Run:

```bash
python -m pytest tests/integration/test_graphql_relation_filters.py::GraphQLRelationFilterIntegrationTests::test_id_filter_variants_use_identifier_and_numeric_scalars -q
```

Expected: FAIL because `id` and `id__exact` are `Int`, and `id__in` is absent.

- [ ] **Step 5: Commit the failing tests**

```bash
git add tests/integration/test_graphql_relation_filters.py
git commit -m "test: reproduce inconsistent GraphQL ID filters"
```

### Task 2: Generate ID Equality Filter Scalars

**Files:**
- Modify: `src/general_manager/api/graphql_search.py:281-356`
- Test: `tests/integration/test_graphql_relation_filters.py`

- [ ] **Step 1: Implement the minimal ID filter type special case**

In `get_filter_options`, add a branch before the `Measurement` branch:

```python
if safe_issubclass(normalized_type, GeneralManager):
    yield attribute_name, None
elif attribute_name == "id":
    yield attribute_name, graphene.ID()
    yield f"{attribute_name}__exact", graphene.ID()
    yield f"{attribute_name}__in", graphene.List(graphene.ID)
    for option in ("gt", "gte", "lt", "lte"):
        yield (
            f"{attribute_name}__{option}",
            map_field_to_graphene_read(
                normalized_type,
                attribute_name,
                attr_info,
            ),
        )
elif safe_issubclass(normalized_type, Measurement):
    ...
```

Do not alter handling for any field whose name is not exactly `id`.

- [ ] **Step 2: Run the schema assertion and verify GREEN**

Run:

```bash
python -m pytest tests/integration/test_graphql_relation_filters.py::GraphQLRelationFilterIntegrationTests::test_id_filter_variants_use_identifier_and_numeric_scalars -q
```

Expected: PASS.

- [ ] **Step 3: Run the public regression test**

Run:

```bash
python -m pytest tests/integration/test_graphql_relation_filters.py::GraphQLRelationFilterIntegrationTests::test_reuses_id_variable_for_detail_and_relation_filter -q
```

Expected: PASS if Graphene and the backend accept the normalized value; otherwise fail at runtime rather than schema validation, proving the remaining normalization requirement.

### Task 3: Preserve Backend Identifier Types

**Files:**
- Modify: `tests/unit/test_graph_ql.py`
- Modify: `src/general_manager/api/graphql_search.py:496-550`

- [ ] **Step 1: Add failing scalar normalization coverage**

Add this test beside the existing relation-filter normalization tests:

```python
def test_normalize_filter_input_casts_id_equality_values(self):
    class IdentifierManager:
        class Interface(InterfaceBase):
            input_fields: ClassVar[dict] = {"id": Input(int)}

            @staticmethod
            def get_attribute_types():
                return {"id": {"type": int}}

    normalized = GraphQL._normalize_filter_input(
        IdentifierManager,
        {"id": "7", "id__exact": "8"},
    )

    self.assertEqual(
        normalized,
        {"filter": {"id": 7, "id__exact": 8}, "exclude": {}},
    )
```

- [ ] **Step 2: Add failing membership normalization coverage**

Add:

```python
def test_normalize_filter_input_casts_each_id_in_value(self):
    class IdentifierManager:
        class Interface(InterfaceBase):
            input_fields: ClassVar[dict] = {"id": Input(int)}

            @staticmethod
            def get_attribute_types():
                return {"id": {"type": int}}

    normalized = GraphQL._normalize_filter_input(
        IdentifierManager,
        {"id__in": ["7", "8"]},
    )

    self.assertEqual(
        normalized,
        {"filter": {"id__in": [7, 8]}, "exclude": {}},
    )
```

- [ ] **Step 3: Run both normalization tests and verify RED**

Run:

```bash
python -m pytest tests/unit/test_graph_ql.py -k "casts_id_equality_values or casts_each_id_in_value" -q
```

Expected: two assertion failures showing string values were returned unchanged.

- [ ] **Step 4: Add a focused ID filter value normalizer**

Add above `normalize_filter_input`:

```python
def normalize_id_filter_value(
    field_type: Type[GeneralManager],
    lookup: str,
    value: Any,
) -> Any:
    """Cast equality-style ID filter values to the manager's identifier type."""
    if lookup not in {"id", "id__exact", "id__in"}:
        return value

    interface = getattr(field_type, "Interface", None)
    input_fields = getattr(interface, "input_fields", {})
    id_input = input_fields.get("id") if isinstance(input_fields, dict) else None
    if id_input is None:
        return value

    if lookup == "id__in":
        if not isinstance(value, (list, tuple)):
            return value
        return [id_input.cast(item) for item in value]
    return id_input.cast(value)
```

In the non-relation path at the beginning of `normalize_filter_input`, change:

```python
if not attr_info or not isinstance(value, dict):
    filters[key] = normalize_id_filter_value(field_type, key, value)
    continue
```

This path also runs recursively for nested relation filters.

- [ ] **Step 5: Run both normalization tests and verify GREEN**

Run:

```bash
python -m pytest tests/unit/test_graph_ql.py -k "casts_id_equality_values or casts_each_id_in_value" -q
```

Expected: `2 passed`.

- [ ] **Step 6: Run the complete relation-filter integration file**

Run:

```bash
python -m pytest tests/integration/test_graphql_relation_filters.py -q
```

Expected: all tests pass, including the issue #247 regression.

- [ ] **Step 7: Commit the implementation**

```bash
git add src/general_manager/api/graphql_search.py tests/unit/test_graph_ql.py tests/integration/test_graphql_relation_filters.py
git commit -m "fix: use GraphQL ID for identifier filters"
```

### Task 4: Documentation and Verification

**Files:**
- Modify: `docs/howto/expose_via_graphql.md`

- [ ] **Step 1: Document identifier filter semantics**

Add this section before the existing `Expose authorization hints` section:

```markdown
## Filter by identifier

Identifier equality filters (`id`, `id_Exact`, and `id_In`) use the GraphQL
`ID` scalar, matching detail-query arguments. Ordered comparisons such as
`id_Gt` retain the identifier's underlying numeric scalar when available.
```

- [ ] **Step 2: Run focused tests**

```bash
python -m pytest tests/integration/test_graphql_relation_filters.py tests/unit/test_graph_ql.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run formatting and lint checks**

```bash
ruff format src/general_manager/api/graphql_search.py tests/unit/test_graph_ql.py tests/integration/test_graphql_relation_filters.py
ruff check src/general_manager/api/graphql_search.py tests/unit/test_graph_ql.py tests/integration/test_graphql_relation_filters.py
```

Expected: both commands exit 0.

- [ ] **Step 4: Run type checking**

```bash
mypy src/general_manager/api/graphql_search.py
```

Expected: exit 0.

- [ ] **Step 5: Run the full test suite**

```bash
python -m pytest
```

Expected: all tests pass.

- [ ] **Step 6: Check the final diff**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only the intended implementation, tests, and documentation are changed.

- [ ] **Step 7: Commit documentation and any formatting changes**

```bash
git add docs/howto/expose_via_graphql.md src/general_manager/api/graphql_search.py tests/unit/test_graph_ql.py tests/integration/test_graphql_relation_filters.py
git commit -m "docs: clarify GraphQL identifier filters"
```
