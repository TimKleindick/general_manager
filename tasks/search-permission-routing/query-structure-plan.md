# Native list query-structure preparation

Follow-up to #583 and #304, starting from `d05ce4e8`. The user explicitly
assumes permission definitions remain fixed after startup. The 1,000-booking
flow still performs 60,030 field-metadata reads and 60,000 additional-rule
analyses across ten pages, despite returning the same results as GM 0.86.4.

## Scope and ownership

- `graphql_query_permissions.py` owns operand traversal, field validation,
  accounting, and runtime permission checks. Prepare reusable structure there,
  using the existing alias and additional-rule owners rather than a second
  authorization implementation.
- `graphql_resolvers.py` opts in only native database lists. Search, request
  sources, calculations, and direct helper calls retain their existing path.
- ORM read/support capabilities already cache field descriptors. Only their
  canonical metadata implementations are eligible; custom handlers and method
  overrides retain the dynamic path.
- The Permission owner attests exact builtin read-rule containers before any
  structural comparison. Executable list/dictionary subclasses, dynamic
  Permission slots and direct settings-backed policies retain the old path.
- Tests belong in `test_graphql_query_structure_plan.py`; existing permission,
  relation, and pagination regressions remain applicable unchanged.

## Contract

One native list authorization may prepare standard schema and declarative
permission structure lazily and reuse it within that call. Definitions include
rule dictionaries and lists and are assumed unchanged during that call.
Changing these definitions from a permission callback is outside this static
contract. The next call reads definitions again; no process-global cache is
introduced. Custom dynamic implementations use the existing path.

The plan contains no actor/object permission result or reused Permission
instance. Every existing runtime field check, callback, related-row gate and
candidate occurrence remains. Filtering and pagination still follow complete
operand authorization, so denied nonmatching/off-page rows retain their error.
Only flat, proven ORM operands are planned; properties, unknown fields,
ambiguous aliases and multi-step relations retain the general path. Actual
manager, interface and Permission identities are checked on every plan use.
Preparation stays lazy to preserve empty results and first-error behavior.

## Execution and acceptance

1. Add behavioral/performance regressions and demonstrate the repeated-metadata
   defect on the previous source. Preserve dynamic callback denial and errors,
   budget precedence, actor isolation, overrides, and request boundaries.
2. Add per-call structural planning and native-list activation. Run focused
   permission/list/search suites and independent scoped review.
3. Repeat the unchanged KH harness against GM 0.86.4, frozen `d05ce4e8`, and
   the new source in balanced order, without concurrent timing workloads.
   Record equality hashes, query counts, call counts, medians and dispersion.
4. Run Ruff/format, strict mypy, pre-commit, docs, full GM tests, full KH backend,
   PostgreSQL contracts and the unchanged original booking/calendar gates.
5. Commit/push into the authorized PR, update its evidence and wait for CI.
   No version bump, merge, release or KH dependency publication.

Focused command (after adding the regression module):

```sh
.venv/bin/python -m pytest tests/integration/test_graphql_query_structure_plan.py tests/integration/test_graphql_list_permission_budget.py tests/integration/test_graphql_field_query_permissions.py tests/integration/test_graphql_projection_prefix_permissions.py tests/integration/test_graphql_pagination_hydration.py tests/unit/test_permission_definition_metadata.py -q --tb=short
```
