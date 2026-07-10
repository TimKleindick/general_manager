# Issue 337 Performance Regression Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, default-gated performance regression coverage for database, calculation, and group bucket hot paths without changing production code or the public API.

**Architecture:** Keep all measurement machinery under `tests/perf`. A small support module records stable work units and an explicit budget manifest supplies named integer ceilings; the workload modules combine correctness assertions with those ceilings. Elapsed time and peak allocation are collected only as diagnostics, while query, callback, yield, constructor, and cache-key inspection counts gate CI.

**Tech Stack:** Python 3.12+, pytest, pytest-django, Django `CaptureQueriesContext`, `unittest.mock`, `tracemalloc`, existing GeneralManager test utilities.

---

## File map

- Create `tests/perf/__init__.py`: make performance support imports explicit.
- Create `tests/perf/support.py`: counters, query capture, diagnostic capture,
  observation collection, and budget validation.
- Create `tests/perf/budgets.py`: the only checked-in deterministic ceiling
  manifest.
- Create `tests/perf/conftest.py`: recording-mode fixture and final unused budget
  validation.
- Create `tests/perf/test_perf_support.py`: TDD coverage for the measurement
  infrastructure itself.
- Create `tests/perf/test_calculation_bucket_perf.py`: 5x10 products,
  equivalent plans, and manager-valued inputs.
- Create `tests/perf/test_group_bucket_perf.py`: 10,000-row grouping workloads.
- Create `tests/perf/test_database_bucket_perf.py`: terminal-operation matrix,
  foreign keys, history, and mixed run-cache invalidation.
- Modify `CONTRIBUTING.md`: document execution, diagnostics, and calibration.

The spec intentionally covers three subsystems, but they share one budget and
observation contract and produce one measurement-infrastructure outcome. Keep
one plan and one pull request; make each workload module an independently
reviewed subagent task.

### Task 0: Confirm the isolated branch baseline

**Files:**
- No repository files.

- [ ] **Step 1: Run the existing suite before implementation**

```bash
python -m pytest
```

Expected: the existing suite passes at commit `b3be6f1c`. If it fails, rerun the
same failing node at the original branch point `b9204e89` before attributing the
failure; stop implementation only for a branch-specific regression.

### Task 1: Build and test the deterministic measurement support

**Files:**
- Create: `tests/perf/__init__.py`
- Create: `tests/perf/test_perf_support.py`
- Create: `tests/perf/support.py`
- Create: `tests/perf/budgets.py`
- Create: `tests/perf/conftest.py`

- [ ] **Step 1: Write failing support tests**

Create `tests/perf/test_perf_support.py` with focused tests for counting,
ceilings, recording mode, diagnostics, and manifest validation:

```python
from __future__ import annotations

import pytest

from tests.perf.support import (
    Counter,
    CountingIterable,
    PerfBudgets,
    capture_diagnostics,
)

pytestmark = pytest.mark.perf


def test_counting_iterable_counts_each_yield() -> None:
    counter = Counter()
    values = CountingIterable(range(3), counter)

    assert list(values) == [0, 1, 2]
    assert counter.value == 3


def test_budget_rejects_an_observation_above_its_ceiling() -> None:
    budgets = PerfBudgets({"CASE_QUERIES": 1})

    with pytest.raises(AssertionError, match="CASE_QUERIES.*observed=2.*ceiling=1"):
        budgets.assert_observation("CASE_QUERIES", 2)


def test_record_mode_collects_without_enforcing() -> None:
    budgets = PerfBudgets({"CASE_CALLBACKS": 0}, record=True)

    budgets.assert_observation("CASE_CALLBACKS", 7)

    assert budgets.observations == {"CASE_CALLBACKS": 7}


def test_manifest_validation_rejects_missing_unused_and_non_integer_values() -> None:
    budgets = PerfBudgets(
        {
            "USED": 1,
            "UNUSED": 2,
            "BOOLEAN": True,
            "FLOAT": 1.5,
            "NEGATIVE": -1,
        }
    )
    budgets.assert_observation("USED", 1)

    with pytest.raises(AssertionError) as exc_info:
        budgets.validate_manifest({"USED", "MISSING"})

    message = str(exc_info.value)
    assert "missing=['MISSING']" in message
    assert "unused=['BOOLEAN', 'FLOAT', 'NEGATIVE', 'UNUSED']" in message
    assert "invalid=['BOOLEAN', 'FLOAT', 'NEGATIVE']" in message


def test_capture_diagnostics_returns_result_elapsed_and_peak_bytes() -> None:
    def build_values() -> list[int]:
        return list(range(10))

    observation = capture_diagnostics(build_values)

    assert observation.result == list(range(10))
    assert observation.elapsed_seconds >= 0
    assert observation.peak_bytes > 0
```

- [ ] **Step 2: Run the tests and verify the import failure**

Run:

```bash
python -m pytest tests/perf/test_perf_support.py -q
```

Expected: collection fails because `tests.perf.support` does not exist.

- [ ] **Step 3: Implement the minimal support module**

Create an empty `tests/perf/__init__.py`, then implement these exact public
test-helper shapes in `tests/perf/support.py`:

```python
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Set as AbstractSet
from dataclasses import dataclass
from time import perf_counter
import tracemalloc
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Counter:
    value: int = 0

    def increment(self, amount: int = 1) -> None:
        self.value += amount

    def reset(self) -> None:
        self.value = 0


class CountingIterable(Generic[T]):
    def __init__(self, values: Iterable[T], counter: Counter) -> None:
        self._values = values
        self.counter = counter

    def __iter__(self) -> Iterator[T]:
        for value in self._values:
            self.counter.increment()
            yield value


@dataclass(frozen=True)
class DiagnosticObservation(Generic[T]):
    result: T
    elapsed_seconds: float
    peak_bytes: int


def capture_diagnostics(callback: Callable[[], T]) -> DiagnosticObservation[T]:
    tracemalloc.start()
    started = perf_counter()
    try:
        result = callback()
        elapsed = perf_counter() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return DiagnosticObservation(result, elapsed, peak_bytes)


class PerfBudgets:
    def __init__(self, ceilings: Mapping[str, object], *, record: bool = False) -> None:
        self._ceilings = dict(ceilings)
        self._record = record
        self.observations: dict[str, int] = {}

    def assert_observation(self, name: str, observed: int) -> None:
        if name not in self._ceilings:
            raise AssertionError(f"missing performance budget: {name}")
        self.observations[name] = observed
        if self._record:
            print(f"PERF_OBSERVATION {name}={observed}")
            return
        ceiling = self._ceilings[name]
        if type(ceiling) is not int or ceiling < 0:
            raise AssertionError(f"invalid performance budget: {name}={ceiling!r}")
        assert observed <= ceiling, (
            f"{name}: observed={observed} exceeded ceiling={ceiling}"
        )

    def validate_manifest(self, expected_names: AbstractSet[str]) -> None:
        actual_names = set(self._ceilings)
        missing = sorted(expected_names - actual_names)
        unused = sorted(actual_names - expected_names)
        invalid = sorted(
            name
            for name, value in self._ceilings.items()
            if type(value) is not int or value < 0
        )
        assert not (missing or unused or invalid), (
            f"missing={missing}; unused={unused}; invalid={invalid}"
        )
```

Create `tests/perf/budgets.py` with `PERF_CEILINGS: dict[str, int] = {}`. This
empty manifest is intentional during red/record development; Tasks 2–5 add all
names and Task 6 replaces each initial zero with its recorded current-baseline
count before any workload commit.

Create `tests/perf/conftest.py`:

```python
from __future__ import annotations

import os

import pytest

from tests.perf.budgets import PERF_CEILINGS
from tests.perf.support import PerfBudgets


@pytest.fixture(scope="session")
def perf_budgets() -> PerfBudgets:
    return PerfBudgets(
        PERF_CEILINGS,
        record=os.environ.get("GENERAL_MANAGER_RECORD_PERF") == "1",
    )
```

- [ ] **Step 4: Run the support tests and static checks**

Run:

```bash
python -m pytest tests/perf/test_perf_support.py -q
ruff check tests/perf/support.py tests/perf/test_perf_support.py tests/perf/conftest.py
mypy tests/perf/support.py tests/perf/test_perf_support.py tests/perf/conftest.py
```

Expected: all tests and checks pass.

- [ ] **Step 5: Commit the support slice**

```bash
git add tests/perf/__init__.py tests/perf/support.py tests/perf/budgets.py tests/perf/conftest.py tests/perf/test_perf_support.py
git commit -m "perf: add deterministic performance budget support"
```

### Task 2: Add calculation bucket workloads test-first

**Files:**
- Create: `tests/perf/test_calculation_bucket_perf.py`
- Modify: `tests/perf/budgets.py`

- [ ] **Step 1: Write the static and dependent 5x10 tests**

Define two test-local `CalculationInterface` classes and managers. Use
`CountingIterable(range(5), a_yields)` and
`CountingIterable(range(10), b_yields)` for the static case. For the dependent
case, define `b_values(a: int)` to increment `callbacks` and return a fresh
`CountingIterable((a * 10 + offset for offset in range(10)), b_yields)`. Use
`Input(int, possible_values=..., depends_on=("a",))` following the constructor
patterns in `tests/unit/test_calculation_bucket.py`.

Add every static/dependent observation name to `PERF_CEILINGS` with the
deliberate initial integer ceiling `0`; recording mode bypasses these ceilings
but still rejects misspelled or missing names.

Each test must:

```python
with CalculationRunContext():
    cold = bucket.generate_combinations()
    cold_counts = (a_yields.value, b_yields.value, callbacks.value)
    a_yields.reset()
    b_yields.reset()
    callbacks.reset()
    warm = bucket.generate_combinations()

assert len(cold) == 50
assert cold[0] == {"a": 0, "b": 0}
assert cold[-1] == {"a": 4, "b": 49}
assert warm is cold
perf_budgets.assert_observation("CALC_DEPENDENT_5X10_COLD_A_YIELDS", cold_counts[0])
perf_budgets.assert_observation("CALC_DEPENDENT_5X10_COLD_B_YIELDS", cold_counts[1])
perf_budgets.assert_observation("CALC_DEPENDENT_5X10_COLD_CALLBACKS", cold_counts[2])
perf_budgets.assert_observation("CALC_DEPENDENT_5X10_WARM_A_YIELDS", a_yields.value)
perf_budgets.assert_observation("CALC_DEPENDENT_5X10_WARM_B_YIELDS", b_yields.value)
perf_budgets.assert_observation("CALC_DEPENDENT_5X10_WARM_CALLBACKS", callbacks.value)
```

The static test uses the corresponding `CALC_STATIC_5X10_*` names and asserts
`{"a": 4, "b": 9}` for its last combination. Patch each manager `__init__`
with an explicit descriptor-preserving counted wrapper during cold and warm
materialization: retain the original function, increment the counter in
`counted_init(self, **kwargs)`, forward to the original with the same arguments,
and restore it through `monkeypatch`. Record the matching `*_CONSTRUCTORS`
names. Do not use raw `patch.object(..., wraps=Class.__init__)`.

Set `pytestmark = pytest.mark.perf` at module scope so these cases remain part
of the default run and are selectable with `-m perf`.

Wrap the dependent case's cold `generate_combinations` callback with
`capture_diagnostics` and print elapsed seconds and peak bytes only when
`request.config.option.verbose >= 2`; continue to assert deterministic counts
outside that diagnostic object.

- [ ] **Step 2: Run in record mode and verify functional behavior**

Run:

```bash
GENERAL_MANAGER_RECORD_PERF=1 python -m pytest tests/perf/test_calculation_bucket_perf.py -q -s
```

Expected: both tests pass functional assertions and print every named
observation.

- [ ] **Step 3: Add equivalent-plan and manager-valued input tests**

Add an equivalent-plan test that creates two dependent buckets with identical
manager, filters, excludes, and input-only sort inside one run context. Assert
50 equal combinations, `first_result is not second_result`, and record each
plan's yields, callbacks, and constructors under the exact
`CALC_EQUIVALENT_5X10_FIRST_*` and `CALC_EQUIVALENT_5X10_SECOND_*` names.
Add those names to `PERF_CEILINGS` at integer ceiling `0` before running the
test.

Add a `CountingListBucket` test double implementing `filter`, `exclude`, and
iteration counters. Parameterize manager-valued inputs with:

```python
(
    "UNIQUE",
    [ValueManager(id=index) for index in range(50)],
    50,
),
(
    "REPEATED",
    [ValueManager(id=index % 10) for index in range(50)],
    10,
),
```

Materialize cold and warm, assert 50 combinations and the expected number of
distinct `value.identification["id"]` values, and record
`CALC_MANAGER_VALUES_<SHAPE>_50_<PHASE>_<YIELDS|CONSTRUCTORS>`.
Add those names to `PERF_CEILINGS` at integer ceiling `0` before running the
test.

- [ ] **Step 4: Verify enforced mode is red**

Run with the deliberate zero ceilings and recording mode disabled:

```bash
python -m pytest tests/perf/test_calculation_bucket_perf.py -q
```

Expected: at least one cold-work assertion fails with an observation greater
than zero, proving enforced mode is active. Do not commit the zero ceilings.

### Task 3: Add group bucket scaling workloads test-first

**Files:**
- Create: `tests/perf/test_group_bucket_perf.py`
- Modify: `tests/perf/budgets.py`

- [ ] **Step 1: Write the three eager-grouping cases**

Create a lightweight manager with a `group_key` attribute and a test interface
whose `get_attributes()` and `get_attribute_types()` expose `group_key`. Create
10,000 source managers once in a module-scoped fixture. Parameterize expected
group counts as `(10, 10)`, `(1000, 1000)`, and `(10000, 10000)` and assign
`manager.group_key = manager.row_index % divisor`, using the row index directly
for 10,000 groups.

Add all nine exact group observation names to `PERF_CEILINGS` at the deliberate
initial integer ceiling `0` before the first record-mode run.

Measure one `GroupBucket(manager_class, ("group_key",), source_bucket)`
construction per case. The counting source bucket increments `yields` in
`__iter__` and `filter_calls` in `filter`, and supplies a no-argument `count()`
returning its length. Count `GroupManager.__init__` with an explicit wrapper
that accepts `self`, increments the counter, and forwards all arguments to the
saved original; install and restore it with `monkeypatch`. After measurement
assert:

```python
assert bucket.count() == expected_groups
assert sum(group._data.count() for group in bucket) == 10_000
assert bucket.first() is not None
assert bucket.first().group_key == 0
assert bucket.last() is not None
assert bucket.last().group_key == expected_groups - 1
assert source_managers[5_000] in bucket
```

Record `GROUP_<EXPECTED>_YIELDS`, `GROUP_<EXPECTED>_CONSTRUCTORS`, and
`GROUP_<EXPECTED>_FILTER_CALLS`.

Set `pytestmark = pytest.mark.perf` at module scope.

- [ ] **Step 2: Verify record mode and diagnostic capture**

Wrap only the 10,000-group constructor callback with `capture_diagnostics` and
print its elapsed seconds and peak bytes when `request.config.option.verbose >=
2`. Run:

```bash
GENERAL_MANAGER_RECORD_PERF=1 python -m pytest tests/perf/test_group_bucket_perf.py -vv -s
```

Expected: three passing cases, all nine deterministic observations, and one
diagnostic line for `GROUP_10000`.

- [ ] **Step 3: Prove enforced mode is red**

Run without `GENERAL_MANAGER_RECORD_PERF=1` and expect the first non-zero
workload count to fail against a deliberate zero ceiling.
Do not commit the zero ceilings.

### Task 4: Add the database terminal-operation matrix test-first

**Files:**
- Create: `tests/perf/test_database_bucket_perf.py`
- Modify: `tests/perf/budgets.py`

- [ ] **Step 1: Define the test-local manager and shared 10,000-row fixture**

Reuse the trusted/untrusted interface patterns from
`tests/unit/test_database_bucket.py`, backed by `django.contrib.auth.models.User`.
The module-scoped fixture must delete prior `perf-db-` users, bulk-create exactly
10,000 users with deterministic names, and return their ordered primary keys.
Fixture database work occurs before any `CaptureQueriesContext`.

Set `pytestmark = pytest.mark.perf` at module scope.

For the 10,000-row `list` cold phase only, wrap the invocation callback with
`capture_diagnostics` and print elapsed seconds and peak bytes when
`request.config.option.verbose >= 2`. Do not add either diagnostic to
`PERF_CEILINGS`.

- [ ] **Step 2: Write the 20 cold/warm terminal cases**

Parameterize row counts `(999, 1000, 1001, 10000)` and operation names
`("first", "get", "contains", "count", "list")`. For each case, create a
fresh ordered queryset prefix, bucket, and membership target manager before
measurement. Dispatch through a single test-local function:

```python
def invoke(
    operation: str,
    bucket: DatabaseBucket,
    target_pk: int,
    target_manager: PerfUserManager,
) -> object:
    if operation == "first":
        return bucket.first()
    if operation == "get":
        return bucket.get(id=target_pk)
    if operation == "contains":
        return target_manager in bucket
    if operation == "count":
        return bucket.count()
    if operation == "list":
        return list(bucket)
    raise AssertionError(f"unsupported operation: {operation}")
```

Inside one fresh `CalculationRunContext`, capture cold queries and construction
counter, reset the counter, then capture warm queries and construction counter.
Count construction at `DatabaseBucket._build_manager_from_instance` and
`DatabaseBucket._build_manager_from_primary_key` with explicit forwarding
wrappers and sum both counters. Do not patch `PerfUserManager.__init__`, because
changing its identity disables trusted hydration and changes the measured path.
Assert the exact result described in the spec table. Record names with
`DB_<OPERATION>_<ROWS>_<COLD|WARM>_<QUERIES|CONSTRUCTORS>`.

Add all 80 terminal-operation observation names to `PERF_CEILINGS` at the
deliberate initial integer ceiling `0` before the record-mode run.

- [ ] **Step 3: Verify the matrix in record mode**

Run:

```bash
GENERAL_MANAGER_RECORD_PERF=1 python -m pytest tests/perf/test_database_bucket_perf.py::test_database_terminal_matrix -q -s
```

Expected: 20 passing parameter cases and 80 printed observations. Confirm the
query capture excludes fixture setup by checking every case begins after the
queryset prefix exists.

- [ ] **Step 4: Prove enforced mode is red**

Run without recording mode and expect a cold query or construction ceiling
failure against a deliberate zero ceiling. Do not commit the zero ceilings.

### Task 5: Add relation, history, and cache invalidation workloads test-first

**Files:**
- Modify: `tests/perf/test_database_bucket_perf.py`
- Modify: `tests/perf/budgets.py`

- [ ] **Step 1: Add one dynamic database-interface fixture**

Create a `GeneralManagerTransactionTestCase` subclass in the performance module
whose `setUpClass` defines three manager classes: `PerfParent`, `PerfChild`, and
`PerfHistory`. `PerfChild.Interface` owns a nullable foreign key to
`PerfParent`; `PerfHistory.Interface` owns an integer `revision` field and sets
`historical_lookup_buffer_seconds = 0`. Put all three in
`general_manager_classes` so the existing test utility creates and tears down
live and history tables.

Use one test method for the relation/history workloads so the class creates and
seeds its dynamic schema once. Seed the unique and repeated FK shapes with
normal model bulk operations outside measurement. Seed 100 history managers
through `PerfHistory.create`, then perform four update rounds across all 100
managers, capturing `timezone.now()` between rounds three and four.

- [ ] **Step 2: Measure unique and repeated FK traversal**

For each shape, start a fresh run context, materialize 1,000 children, and read
`child.parent` for each. Repeat the traversal warm in the same context. Assert
1,000 parent results and distinct IDs of 1,000 or 10. Capture queries and patch
`PerfParent.Interface.__init__` with an explicit descriptor-preserving wrapper
that accepts `self`, increments the counter, and forwards all arguments to the
saved original. Do not use `select_related`: normal child-bucket rows leave the
relation `fields_cache` empty, so the accessor intentionally exercises
`PerfParent(raw_id)` and the run-scoped unique-versus-repeated raw-FK cache.
Leaving `PerfParent.__init__` untouched preserves trusted-path eligibility
checks elsewhere. Record the eight names matching
`DB_FK_<UNIQUE|REPEATED>_<COLD|WARM>_<QUERIES|CONSTRUCTORS>`.

- [ ] **Step 3: Measure history reads and writes**

For reads, construct `PerfHistory.filter(search_date=round_three_timestamp)`,
materialize 100 managers, and read each `revision`. Assert all values equal 3.
Capture queries and count
`OrmHistoryCapability.get_historical_queryset` with an explicit bound-instance
forwarding wrapper. This is the bucket-level `history.as_of` path; do not count
`get_historical_record`, which is not invoked by historical bucket hydration. Record
`DB_HISTORY_READ_100_QUERIES` and `DB_HISTORY_READ_100_CALLBACKS`.

For writes, record history row count, patch
`OrmMutationCapability.save_with_history` with an explicit wrapper accepting
`self`, forwarding every positional and keyword argument to the saved original,
and incrementing once per call. Update all 100 managers to revision 5 through
`manager.update`. Assert all
live values equal 5 and history row count increased by exactly 100. Record
`DB_HISTORY_WRITE_100_QUERIES` and `DB_HISTORY_WRITE_100_CALLBACKS`.

- [ ] **Step 4: Measure the exact mixed-cache invalidation workload**

Populate 500 tuple keys under each of the 11 prefixes listed in the approved
spec, 500 `("unrelated", index)` keys, and 500 dependency hits. Wrap
`context.discard_prefix` so every call increments `discard_calls` and adds the
pre-call `len(context._values)` to `key_inspections`, then invoke a no-op method
decorated by `@data_change`.

Wrap `clear_trusted_orm_managers` as the final clear method in each phase and
store a targeted-prefix snapshot after delegation. The wrapper must capture two
snapshots: one after the pre-mutation clear and one after the post-mutation
clear.

Patch only dependency-index signal receivers and dependency publish work. Assert
the no-op body ran once, both snapshots contain no targeted key, all 500
unrelated keys remain, and all 500 dependency hits remain while still inside the
run context. Record
`RUN_CACHE_MIXED_500_DISCARD_CALLS` and
`RUN_CACHE_MIXED_500_KEY_INSPECTIONS`. Capture diagnostic time and peak bytes
without asserting either.

- [ ] **Step 5: Add initial ceilings and run all database cases in record mode**

Add the 14 relation/history/cache names at integer ceiling `0`, then run:

```bash
GENERAL_MANAGER_RECORD_PERF=1 python -m pytest tests/perf/test_database_bucket_perf.py -vv -s
```

Expected: all functional assertions pass and 94 deterministic observations are
printed for the database module. Fix functional or isolation failures before
calibration; do not weaken assertions.

### Task 6: Calibrate and lock all deterministic budgets

**Files:**
- Modify: `tests/perf/budgets.py`
- Modify: `tests/perf/conftest.py`
- Modify: `tests/perf/test_perf_support.py`

- [ ] **Step 1: Record three complete observation sets**

Run exactly:

```bash
GENERAL_MANAGER_RECORD_PERF=1 python -m pytest tests/perf -q -s > /tmp/gm-perf-337-run-1.txt
GENERAL_MANAGER_RECORD_PERF=1 python -m pytest tests/perf -q -s > /tmp/gm-perf-337-run-2.txt
GENERAL_MANAGER_RECORD_PERF=1 python -m pytest tests/perf -q -s > /tmp/gm-perf-337-run-3.txt
diff -u /tmp/gm-perf-337-run-1.txt /tmp/gm-perf-337-run-2.txt
diff -u /tmp/gm-perf-337-run-2.txt /tmp/gm-perf-337-run-3.txt
```

Expected: the `PERF_OBSERVATION` lines are identical. Pytest duration text may
differ; compare filtered observation lines if necessary with
`rg '^PERF_OBSERVATION '`. Any deterministic-count difference must be fixed at
the measurement boundary before continuing.

- [ ] **Step 2: Replace every zero with the exact observed count**

For each printed `PERF_OBSERVATION NAME=VALUE`, set the matching
`PERF_CEILINGS["NAME"]` integer to `VALUE`. Keep one explicit mapping entry per
name, sorted by database, calculation, group, and support sections. Do not add
padding and do not keep a zero unless the recorded observation is exactly zero.

- [ ] **Step 3: Validate complete budget use at session finish**

Track expected names whenever `assert_observation` is called. Add collection
tracking in `tests/perf/conftest.py` and invoke
`perf_budgets.validate_manifest(set(perf_budgets.observations))` at session
teardown only when all three new workload modules were collected. Narrow module
or single-test commands skip the global unused-budget check but still reject a
missing budget at observation time. Extend `test_perf_support.py` to prove a
complete manifest passes and that a true integer zero is valid.

Use this collection/teardown shape:

```python
from collections.abc import Iterator
from pathlib import Path

WORKLOAD_MODULES = frozenset(
    {
        "test_database_bucket_perf.py",
        "test_calculation_bucket_perf.py",
        "test_group_bucket_perf.py",
    }
)
collected_workload_modules: set[str] = set()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    collected_workload_modules.update(
        Path(str(item.path)).name
        for item in items
        if Path(str(item.path)).name in WORKLOAD_MODULES
    )


@pytest.fixture(scope="session", autouse=True)
def validate_perf_manifest(perf_budgets: PerfBudgets) -> Iterator[None]:
    yield
    if collected_workload_modules == WORKLOAD_MODULES:
        perf_budgets.validate_manifest(set(perf_budgets.observations))
```

- [ ] **Step 4: Run enforced mode three times**

```bash
python -m pytest tests/perf -q
python -m pytest tests/perf -q
python -m pytest tests/perf -q
```

Expected: all three runs pass with identical deterministic counts.

- [ ] **Step 5: Commit all workload coverage**

```bash
git add tests/perf
git commit -m "perf: cover manager and bucket hot paths"
```

### Task 7: Document the performance workflow

**Files:**
- Modify: `CONTRIBUTING.md`

- [ ] **Step 1: Add a Performance regression tests section**

Document these exact commands:

```bash
python -m pytest -m perf
python -m pytest tests/perf/test_database_bucket_perf.py -vv
GENERAL_MANAGER_RECORD_PERF=1 python -m pytest tests/perf -q -s
```

State that deterministic ceilings are CI gates, elapsed/peak allocation values
are diagnostics, calibration requires three identical observation runs, budget
increases require an inline explanation, and optimization PRs should lower
budgets only after recording a stable before/after improvement.

- [ ] **Step 2: Verify documentation and marker behavior**

Run:

```bash
python -m pytest -m perf -q
git diff --check
```

Expected: the performance selection passes and the diff has no whitespace
errors.

- [ ] **Step 3: Commit the documentation**

```bash
git add CONTRIBUTING.md
git commit -m "perf: document performance regression workflow"
```

### Task 8: Final verification, issue note, and draft pull request

**Files:**
- No new repository files unless verification reveals a defect in the planned
  test-only implementation.

- [ ] **Step 1: Run narrow and broad verification**

Run in this order:

```bash
python -m pytest tests/perf -q --durations=10
python -m pytest -m perf -q
ruff check tests/perf
ruff format --check tests/perf
mypy tests/perf
python -m pytest
pre-commit run --all-files
git diff --exit-code b9204e89 -- src/general_manager
```

Expected: every command exits zero, the three new perf modules report less than
the design target of 30 seconds locally, and the source diff is empty. The time
target is reported rather than asserted. If full-suite or pre-commit failures
are pre-existing, prove that against the original commit before reporting them;
do not claim success for an unverified state.

- [ ] **Step 2: Capture the required performance evidence**

Run the four representative diagnostic cases with `-vv -s`: database list at
10,000 rows, dependent 5x10 calculation, 10,000 groups, and mixed-cache
invalidation. Record their deterministic counts plus diagnostic elapsed and
peak-byte values in the draft PR body. State explicitly that issue #337 adds
measurement infrastructure and does not target a direct speedup.

- [ ] **Step 3: Request two-stage subagent review**

First request a spec-compliance review against the approved design and this
plan. After any corrections, request a code-quality review focused on fixture
isolation, measurement boundaries, determinism, and default CI cost. Resolve all
blocking feedback and rerun affected checks.

- [ ] **Step 4: Comment on issue #337**

Post this substance to #337:

```text
This issue intentionally does not target a direct runtime speedup. It adds deterministic query, callback, yield, construction, and cache-invalidation baselines so the remaining #336 optimization issues can prove before/after gains and prevent regressions. Wall-clock and allocation observations remain diagnostic to keep CI stable.
```

- [ ] **Step 5: Push and create a draft PR**

Push `codex/perf-337-benchmark-coverage` and create a draft PR targeting `main`.
Use a `perf:` title, link `#337`, summarize the deterministic case matrix, list
all verification commands and results, attach the four representative
observations, and state that `src/general_manager` and the public API are
unchanged.
