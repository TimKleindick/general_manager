# Task 1 report: normalized query-parameter plan

## Status

DONE_WITH_CONCERNS

## Implementation

- Added frozen, slotted `QueryParameterPlan` with `filters`, `excludes`, and
  `normalized_excludes` fields.
- Added `build_query_parameter_plan()` to parse inputs and invoke the supplied
  normalizer once per non-empty input. Raw GraphQL exclude input is checked for
  nested `none` relation filters before normalization.
- Added `apply_query_parameter_plan()` with the required operation order:
  filter, explicit exclude, normalized exclude, then sort.
- Updated `apply_query_parameters()` to build and apply a plan, preserving its
  public signature and existing sorting/exception behavior.

## Files changed

- `src/general_manager/api/graphql_resolvers.py`
- `tests/unit/test_graphql_helpers.py`

## TDD evidence

### RED

Ran:

```text
python -m pytest tests/unit/test_graphql_helpers.py -k "query_parameter_plan" -q
```

The test module failed during collection with the expected ImportError because
`apply_query_parameter_plan` was not yet defined.

### GREEN

After the implementation:

```text
python -m pytest tests/unit/test_graphql_helpers.py -k "query_parameter_plan" -q
2 passed, 49 deselected in 0.06s

python -m pytest tests/unit/test_graphql_helpers.py -q
51 passed, 10 subtests passed in 0.08s
```

Additional checks:

```text
ruff check src/general_manager/api/graphql_resolvers.py tests/unit/test_graphql_helpers.py
All checks passed!

ruff format --check src/general_manager/api/graphql_resolvers.py tests/unit/test_graphql_helpers.py
2 files already formatted
```

## Self-review

- The plan is immutable at the dataclass level (`frozen=True`, `slots=True`).
- Normalizer invocation count and accumulated normalized excludes are covered.
- Application order and sorting are covered with a recording bucket.
- Existing helper tests remain green.
- No changes were made to the PR #456 bucket subset implementation.

## Concerns

- A targeted `mypy` invocation is not clean: the repository currently reports
  140 errors across the existing test utility and helper test typing. The new
  recording-bucket test also gets an expected structural typing complaint when
  checked in isolation because its lightweight test double is not a concrete
  `Bucket` subclass. This does not affect runtime tests; resolving the broader
  typing debt is outside Task 1.
