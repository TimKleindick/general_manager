# Issue 337: Manager and Bucket Performance Regression Coverage

Status: approved

Tracker: [#336](https://github.com/TimKleindick/general_manager/issues/336)

Issue: [#337](https://github.com/TimKleindick/general_manager/issues/337)

## Summary

Add deterministic performance-regression tests for database, calculation, and
group buckets. The tests will gate stable units of work—database queries,
source yields, callbacks, manager construction, and cache-key inspections—while
keeping elapsed time and peak memory diagnostic. All helpers and fixtures stay
under `tests/`; this issue adds no runtime dependency and changes no public API.

This issue establishes measurement infrastructure. It is not expected to make
the framework faster by itself. The pull request and issue comment must state
that direct speedup was not targeted, because later optimization issues will use
these baselines to prove and protect improvements.

## Goals

- Detect regressions in the manager and bucket hot paths named in #337.
- Exercise both sides of the 1,000-row run-cache threshold.
- Make every gated metric deterministic on SQLite and across supported Python
  versions.
- Pair every performance ceiling with a functional assertion.
- Keep the suite part of the default test run and independently runnable with
  `python -m pytest -m perf`.
- Give later optimization pull requests named budgets they can deliberately
  lower after recording an improvement.

## Non-goals

- No production optimization or behavior change.
- No public benchmark API.
- No `pytest-benchmark` or other new dependency.
- No CI pass/fail decision based on wall-clock time or `tracemalloc` output.
- No exhaustive benchmark of every lookup grammar or database backend.

## Test-only architecture

Create these modules:

- `tests/perf/conftest.py`: shared counters, budget assertion helpers, and
  reusable calculation/group test doubles.
- `tests/perf/budgets.py`: named checked-in ceilings. Names encode subsystem,
  case, size, and phase; examples are
  `DB_LIST_1001_WARM_QUERIES` and
  `CALC_DEPENDENT_5X10_COLD_CALLBACKS`.
- `tests/perf/test_database_bucket_perf.py`: database terminal operations,
  relation traversal, history, and mixed run-cache invalidation.
- `tests/perf/test_calculation_bucket_perf.py`: static/dependent products,
  equivalent plans, and manager-valued inputs.
- `tests/perf/test_group_bucket_perf.py`: source-row and output-group scaling.

The budget helper accepts a case name and observed counts, checks each observed
count is at or below its named ceiling, and includes the full observation in a
failure message. A separate helper captures diagnostic elapsed time and peak
allocated bytes and emits them only when `-vv` is used. Neither diagnostic is
asserted.

Fixture creation, schema setup, history seeding, and counter construction occur
before the measurement context. Each measured block follows this order:

1. reset counters;
2. enter query/callback/yield/construction measurement;
3. invoke exactly one named operation (or its explicitly defined two-call
   cold/warm sequence);
4. leave measurement;
5. assert the returned values and resulting persisted/cache state;
6. assert the named ceilings.

Helpers must not import from or be re-exported by `src/general_manager`.

## Budget calibration and change policy

The implementation starts by running every case against the current `main`
implementation and recording its deterministic counts. The checked-in ceiling
for query, callback, yield, constructor, and key-inspection metrics is the
maximum identical count observed across three consecutive local runs. There is
no percentage padding. If a supported Python version has a different stable
count, the ceiling is the maximum of those stable counts and a comment records
the per-version values.

Every budget has a unique constant rather than sharing a generic threshold.
The tests must fail if a budget constant is missing, unused, negative, or uses a
floating-point value. A budget increase requires an inline reason and should be
treated as a performance regression during review. A later optimization may
lower a ceiling only after its before/after verification records the lower
stable count.

## Scenario matrix

### Database bucket terminal operations

A module-scoped fixture bulk-creates exactly 10,000 `auth.User` rows once. Each
case obtains an ordered queryset prefix of 999, 1,000, 1,001, or 10,000 rows;
creating the prefix queryset is outside measurement. A test-local manager and
interface are used, and manager construction is counted at the boundary that
wraps a row or primary key.

All five operations cross-product with all four sizes, for 20 cases:

| Operation | Invocation | Functional assertion |
| --- | --- | --- |
| `first` | `bucket.first()` | first fixture primary key |
| `get` | `bucket.get(id=target_pk)` | target primary key at the middle index |
| `contains` | `target_manager in bucket` | `True` for the last included row |
| `count` | `bucket.count()` | exact selected row count |
| `list` | `list(bucket)` | exact length and first/last primary keys |

Each case runs once in a fresh `CalculationRunContext` (cold) and immediately
repeats the same operation in that context (warm). Query and manager-construction
counts are measured separately for each phase. This makes the expected cache
boundary behavior visible: 999 and 1,000 may reuse materialized results, while
1,001 and 10,000 must retain the current non-materializing behavior unless a
later issue intentionally changes it.

Named ceilings follow
`DB_<OPERATION>_<ROWS>_<COLD|WARM>_<QUERIES|CONSTRUCTORS>`.

### Foreign-key traversal

Use one dynamically registered database-interface parent/child pair, with the
child holding a nullable foreign key to the parent. Seed both shapes once:

- `FK_UNIQUE`: 1,000 parents and 1,000 children, one child per parent.
- `FK_REPEATED`: 10 parents and 1,000 children, 100 consecutive children per
  parent.

For each shape, materialize the child bucket and read the related parent manager
from every child inside one run context. Assert 1,000 results, and assert 1,000
distinct parent IDs for `FK_UNIQUE` versus 10 for `FK_REPEATED`. Gate cold and
warm query counts plus parent-manager construction counts under
`DB_FK_<UNIQUE|REPEATED>_<COLD|WARM>_<QUERIES|CONSTRUCTORS>`.

### History reads and writes

Use one dedicated dynamically registered database-interface manager whose
model has normal history support and whose
`historical_lookup_buffer_seconds` is explicitly `0`. Do not use bulk creation
for measured history behavior.

Setup, outside measurement:

1. create 100 managers through `Manager.create`, producing 100 creation-history
   rows;
2. update all 100 managers through `manager.update` in revision round one, then
   repeat for rounds two and three, producing three additional history rows per
   manager;
3. capture one timestamp after every manager has completed round three;
4. update all 100 managers through `manager.update` in revision round four,
   producing the fourth additional history row per manager.

The read case constructs a historical bucket for all 100 managers at the saved
timestamp, materializes it, and reads one scalar field from every manager. With
the lookup buffer disabled, every lookup must use history even though the
fixture was created immediately before measurement.
Assert 100 managers and the revision-three value for each. Gate queries and
history-lookup callbacks as `DB_HISTORY_READ_100_QUERIES` and
`DB_HISTORY_READ_100_CALLBACKS`.

The write case updates all 100 current managers once through the normal manager
API. Assert the persisted scalar values and exactly 100 additional history rows.
Gate queries and history-save callbacks as `DB_HISTORY_WRITE_100_QUERIES` and
`DB_HISTORY_WRITE_100_CALLBACKS`. The callback is the test-observed history
save hook used by the interface; setup history writes are never included.

### Mixed run-cache invalidation

Populate one `CalculationRunContext` with 500 entries in each of these 11 tuple
key namespaces:

1. `orm_bucket_result`
2. `orm_bucket_row_result`
3. `orm_bucket_manager_result`
4. `orm_bucket_first_row`
5. `orm_model_row_index`
6. `orm_model_relation_prefetch`
7. `orm_relation_manager`
8. `orm_query_bucket`
9. `orm_bucket_exists`
10. `bucket_index`
11. `trusted_orm_manager`

Also add 500 unrelated tuple keys and 500 dependency-cache hits. Wrap a no-op
mutation in `@data_change` so the real pre- and post-mutation cache-clearing path
runs twice. Patch only external dependency-index signal work so this case
isolates run-cache clearing; do not patch the context clear methods.

Instrument `discard_prefix` to count invocations and add the current
`len(context._values)` to a deterministic `key_inspections` work counter before
delegating. Gate both metrics as
`RUN_CACHE_MIXED_500_DISCARD_CALLS` and
`RUN_CACHE_MIXED_500_KEY_INSPECTIONS`. Assert all 5,500 targeted run-cache
entries are gone after each clear phase, the 500 unrelated values remain, and
the 500 dependency hits remain. Assert the wrapped mutation callback executes
exactly once. This precisely distinguishes namespaces that clear from state that
must survive.

### Calculation products

`5x10` means two inputs and 50 output combinations, not five inputs of ten
values and not 100,000 combinations.

- `STATIC_5X10`: input `a` is a counting iterable of integers `0..4`; input `b`
  is a counting iterable of integers `0..9`; there are 50 ordered combinations.
- `DEPENDENT_5X10`: input `a` is `0..4`; input `b` depends on `a` and a counting
  callback returns ten values derived from that `a`; there are 50 unique
  combinations and exactly five logical dependency resolutions before caching
  effects are considered.

For both shapes, call `generate_combinations()` once (cold) and again on the
same bucket (warm). Assert exact combination contents and ordering. Gate source
yields, possible-value callbacks, and manager construction separately under
`CALC_<STATIC|DEPENDENT>_5X10_<COLD|WARM>_<YIELDS|CALLBACKS|CONSTRUCTORS>`.

For `EQUIVALENT_PLANS`, build two separate buckets with the same manager class,
filters, excludes, and input-only sort, inside one run context. Generate both in
sequence. Assert equal but separately owned result lists and 50 combinations.
Record first-plan and second-plan counts separately under
`CALC_EQUIVALENT_5X10_<FIRST|SECOND>_<YIELDS|CALLBACKS|CONSTRUCTORS>`.

For `MANAGER_VALUES`, a counting list bucket supplies 50 manager instances as
one input domain. Test two shapes: 50 unique identifications and 50 values drawn
from 10 repeated identifications. Materialize once cold and once warm, assert 50
combinations and the expected distinct-identification count, and gate source
yields and new manager constructions under
`CALC_MANAGER_VALUES_<UNIQUE|REPEATED>_50_<COLD|WARM>_<YIELDS|CONSTRUCTORS>`.

### Group scaling

Use a counting list bucket and 10,000 lightweight test managers for all cases;
constructing the input managers is outside measurement. Grouping is eager, so
each scenario measures one `GroupBucket` construction:

| Case | Input rows | Group key distribution | Expected groups |
| --- | ---: | --- | ---: |
| `GROUP_10` | 10,000 | `row_index % 10` | 10 |
| `GROUP_1000` | 10,000 | `row_index % 1000` | 1,000 |
| `GROUP_10000` | 10,000 | unique `row_index` | 10,000 |

Assert the exact group count, total rows across all group managers, first and
last group keys, and membership of a representative source manager. Gate source
yields, group-manager constructions, and source-bucket filter calls under
`GROUP_<10|1000|10000>_<YIELDS|CONSTRUCTORS|FILTER_CALLS>`.

## CI cost and stability

The tests retain `@pytest.mark.perf` but are not skipped from the default suite;
the existing Python-version matrix continues to run them. Cost is bounded as
follows:

- The 10,000 database rows, dynamic relation schema, and history schema are
  created once per module rather than once per parameter.
- The 20 database operation cases select prefixes from the same 10,000-row
  fixture.
- The calculation product contains 50 combinations; no case constructs a
  100,000-combination product.
- The 10,000 group source managers are created once and reused for all three
  distributions.
- Diagnostic elapsed-time and memory capture is limited to the 10,000-row
  database `list`, dependent 5x10 calculation, 10,000-group construction, and
  mixed-cache invalidation cases.
- No retry is permitted inside a test. Stability is established during
  calibration through three complete local runs.

The implementation should target less than 30 seconds for all three new perf
modules together on the repository's SQLite test settings. This is a local
design target, not a timing assertion.

## Documentation

Add a short performance-testing section to the contributor documentation that
shows:

```bash
python -m pytest -m perf
python -m pytest tests/perf/test_database_bucket_perf.py -vv
```

It must explain deterministic budgets, diagnostic timing/memory output, the
three-run calibration rule, and the requirement to justify budget increases.

## Verification

Before opening the draft pull request:

1. Run the helper/budget tests first while developing them test-first.
2. Run each new perf module three consecutive times and compare observations.
3. Run `python -m pytest -m perf`.
4. Run the full `python -m pytest` suite.
5. Run `ruff check`, `ruff format --check`, and `mypy` using repository
   conventions.
6. Capture diagnostic output for the four representative large cases and attach
   the observations to the draft pull request.
7. Comment on #337 that no direct speed improvement was targeted; the result is
   deterministic measurement infrastructure for the later #336 optimizations.

## Compatibility and risks

- Production and public APIs are untouched because all helpers are test-local.
- Exact SQLite query counts may differ from another database backend; the suite
  describes the repository CI baseline, while later backend-specific coverage
  can add separate budgets.
- Dynamic-model and history setup is the highest-risk source of test leakage;
  unique model names, module-scoped schema teardown, and existing GeneralManager
  test utilities must be used.
- Eager 10,000-row cases can expose memory pressure, so fixture reuse must not
  retain measured bucket results between tests.
- Introspection of `CalculationRunContext._values` is allowed only in this
  regression test. It does not make the private storage contract public.

## Acceptance criteria

- The exact cases and dimensions above exist and have functional assertions.
- Every deterministic metric uses a unique, calibrated integer ceiling.
- Setup work is excluded from measured counts.
- Three consecutive perf-suite runs produce identical gated observations.
- The perf marker works independently and the tests remain default-gated.
- Contributor documentation explains execution and budget updates.
- No source file under `src/general_manager` changes.
- The draft PR and issue comment explicitly state why direct performance
  improvement was not targeted by #337.
