# As-Of Historical Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an operation-scoped, read-only `as_of` context that applies one historical date to Python and GraphQL reads, rejects mixed snapshots and mutations, and isolates caches by effective date.

**Architecture:** A focused `general_manager.as_of` module owns normalization, `ContextVar` state, compatibility checks, and public errors. ORM interfaces and buckets resolve their effective date through that module; calculation interfaces remain transparent and request interfaces fail closed. GraphQL registers a query-only `@asOf(date: DateTime!)` directive using the schema's existing `DateTime` scalar and enters `as_of` around the existing calculation run context.

**Tech Stack:** Python 3.12+, Django timezone utilities, `contextvars`, Django Simple History, Graphene/GraphQL-core, pytest/Django TestCase, Ruff, mypy.

---

## File map

- Create `src/general_manager/as_of.py`: date parsing, context state, errors, effective-date resolution, interface policy, mutation guard, and cache fingerprint helpers.
- Create `src/general_manager/api/graphql_as_of.py`: GraphQL directive construction, operation extraction, and public GraphQL error conversion.
- Create `tests/unit/test_as_of.py`: focused context, normalization, nesting, concurrency, interface-policy, and guard tests.
- Modify `src/general_manager/interface/base_interface.py`: declare the default unsupported policy and guard direct query/mutation entry points.
- Modify `src/general_manager/interface/orm_interface.py`: declare historical support and resolve ambient dates for construction and trusted hydration.
- Modify `src/general_manager/interface/interfaces/calculation.py`: declare context-transparent support.
- Modify `src/general_manager/manager/general_manager.py`: retain manager effective dates, validate reads, and reject mutations.
- Modify `src/general_manager/manager/meta.py`: validate generated field reads before using cached or interface values.
- Modify `src/general_manager/api/property.py`: validate `GraphQLProperty` reads.
- Modify `src/general_manager/interface/capabilities/orm/support.py`: resolve ambient dates for `filter`, `exclude`, and `all` query construction.
- Modify `src/general_manager/bucket/database_bucket.py`: validate bound dates on derivation and materialization.
- Modify `src/general_manager/utils/json_encoder.py`: include a manager's historical date in serialized cache identity.
- Modify `src/general_manager/utils/make_cache_key.py`: include the active date in generic and optimized cache keys.
- Modify `src/general_manager/cache/run_context.py`: namespace all run-local values by the active historical date.
- Modify `src/general_manager/api/graphql_warmup_registry.py` and `src/general_manager/api/graphql_warmup.py`: retain a recipe's effective date and re-enter it during background warm-up.
- Modify `src/general_manager/bootstrap.py`: reserve and register the built-in `@asOf` directive without creating a duplicate `DateTime` scalar.
- Modify `src/general_manager/api/graphql_view.py`: extract the directive, enter the context, and restore it around each operation.
- Modify `src/general_manager/api/graphql_resolvers.py`: validate manager/bucket snapshot compatibility at GraphQL read boundaries.
- Modify `src/general_manager/api/graphql_errors.py`: preserve stable public codes for historical-context failures reached by resolvers.
- Modify `src/general_manager/public_api_registry.py`, `src/general_manager/_types/api.py`, and `tests/snapshots/public_api_exports.json`: publish the new API and error types.
- Modify `tests/unit/test_make_cache_key.py`, `tests/unit/test_cache_decorator.py`, `tests/unit/test_graph_ql.py`, `tests/unit/test_general_manager.py`, and `tests/unit/test_database_bucket.py`: focused regression coverage.
- Modify `tests/integration/test_database_manager.py`, `tests/integration/test_calculation_manager.py`, and `tests/integration/test_remote_manager_interface.py`: end-to-end historical propagation and fail-closed coverage.
- Modify `docs/api/interface.md`, `docs/api/graphql.md`, and `docs/api/cache.md`: user-facing Python, GraphQL, and caching contracts.

## Task 1: Core `as_of` state, normalization, and public API

**Files:**
- Create: `src/general_manager/as_of.py`
- Create: `tests/unit/test_as_of.py`
- Modify: `src/general_manager/public_api_registry.py`
- Modify: `src/general_manager/_types/api.py`
- Modify: `tests/snapshots/public_api_exports.json`

- [ ] **Step 1: Write failing normalization and lifecycle tests**

Add tests covering all accepted input forms, context cleanup, same-date nesting,
conflicting nesting, and the accessor:

```python
from datetime import date, datetime, timezone as datetime_timezone

import pytest
from django.utils import timezone

from general_manager.api import (
    HistoricalContextConflictError,
    InvalidSearchDateError,
    as_of,
    current_as_of_date,
)


@pytest.mark.parametrize(
    "value",
    [
        "2022-01-01",
        "2022-01-01T12:30:00Z",
        date(2022, 1, 1),
        datetime(2022, 1, 1, 12, 30),
        datetime(2022, 1, 1, 12, 30, tzinfo=datetime_timezone.utc),
    ],
)
def test_as_of_normalizes_supported_inputs(value: object) -> None:
    with as_of(search_date=value):
        normalized = current_as_of_date()
        assert isinstance(normalized, datetime)
        assert timezone.is_aware(normalized)
    assert current_as_of_date() is None


def test_as_of_allows_equal_nested_date_and_restores_after_error() -> None:
    with pytest.raisesRegex(RuntimeError, "boom"):
        with as_of("2022-01-01"):
            outer = current_as_of_date()
            with as_of(search_date=date(2022, 1, 1)):
                assert current_as_of_date() == outer
            raise RuntimeError("boom")
    assert current_as_of_date() is None


def test_as_of_rejects_conflicting_nested_date() -> None:
    with as_of("2022-01-01"):
        with pytest.raises(HistoricalContextConflictError):
            with as_of("2022-01-02"):
                pass


def test_as_of_rejects_invalid_date_without_leaking_state() -> None:
    with pytest.raises(InvalidSearchDateError):
        with as_of("not-a-date"):
            pass
    assert current_as_of_date() is None
```

- [ ] **Step 2: Run the tests to verify the API is missing**

Run:

```bash
python -m pytest tests/unit/test_as_of.py -q
```

Expected: collection fails because the new exports do not exist.

- [ ] **Step 3: Implement the focused context module**

Create `src/general_manager/as_of.py` with these public types and functions:

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, time
from typing import Literal

from django.utils import timezone

type SearchDateInput = str | date | datetime
type AsOfBehavior = Literal["historical", "transparent", "unsupported"]


class InvalidSearchDateError(ValueError):
    """Raised when a historical date cannot be normalized."""


class HistoricalContextConflictError(RuntimeError):
    """Raised when one operation attempts to mix snapshot dates."""


class HistoricalMutationError(RuntimeError):
    """Raised when a GeneralManager mutation runs in historical context."""


class HistoricalReadNotSupportedError(RuntimeError):
    """Raised when an interface cannot honor an active historical date."""


_active_search_date: ContextVar[datetime | None] = ContextVar(
    "general_manager_as_of_search_date",
    default=None,
)


def normalize_search_date(value: SearchDateInput) -> datetime:
    try:
        if isinstance(value, datetime):
            normalized = value
        elif isinstance(value, date):
            normalized = datetime.combine(value, time.min)
        elif isinstance(value, str):
            iso_value = value[:-1] + "+00:00" if value.endswith("Z") else value
            normalized = datetime.fromisoformat(iso_value)
        else:
            raise TypeError
    except (TypeError, ValueError) as error:
        raise InvalidSearchDateError(f"Invalid search date: {value!r}.") from error
    if timezone.is_naive(normalized):
        normalized = timezone.make_aware(normalized)
    return normalized


def current_as_of_date() -> datetime | None:
    return _active_search_date.get()


def resolve_search_date(value: SearchDateInput | None) -> datetime | None:
    explicit = normalize_search_date(value) if value is not None else None
    active = current_as_of_date()
    if explicit is not None and active is not None and explicit != active:
        raise HistoricalContextConflictError(
            f"Conflicting historical dates: {active.isoformat()} and "
            f"{explicit.isoformat()}."
        )
    return explicit if explicit is not None else active


@contextmanager
def as_of(
    search_date: SearchDateInput,
) -> Iterator[None]:
    normalized = normalize_search_date(search_date)
    active = current_as_of_date()
    if active is not None and active != normalized:
        raise HistoricalContextConflictError(
            f"Conflicting historical dates: {active.isoformat()} and "
            f"{normalized.isoformat()}."
        )
    token = _active_search_date.set(normalized)
    try:
        yield
    finally:
        _active_search_date.reset(token)
```

Add concise messages and docstrings to the four errors. Keep normalization and
resolution public within the module but export only the agreed stable surface
from `general_manager.api`.

- [ ] **Step 4: Add public runtime and type-only exports**

Add `as_of`, `current_as_of_date`, and the four error classes to `API_EXPORTS`
in `public_api_registry.py`; mirror their names and imports in `_types/api.py`.
Regenerate or update `tests/snapshots/public_api_exports.json` using the existing
registry tuple shape, for example:

```json
"as_of": ["general_manager.as_of", "as_of"]
```

- [ ] **Step 5: Add async propagation and thread-isolation tests**

Use `asyncio.create_task()` to assert copied task context sees the date and a
fresh `threading.Thread` to assert it sees `None`. Do not assert isolation for
`asyncio.to_thread()`, because that API intentionally copies context variables.

- [ ] **Step 6: Run focused tests and public API checks**

Run:

```bash
python -m pytest tests/unit/test_as_of.py tests/unit/test_public_api_init_modules.py tests/unit/test_generate_public_api_types.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit the core API**

```bash
git add src/general_manager/as_of.py src/general_manager/public_api_registry.py src/general_manager/_types/api.py tests/unit/test_as_of.py tests/snapshots/public_api_exports.json
git commit -m "feat: add as-of execution context"
```

## Task 2: Interface policy and manager snapshot binding

**Files:**
- Modify: `src/general_manager/interface/base_interface.py`
- Modify: `src/general_manager/interface/orm_interface.py`
- Modify: `src/general_manager/interface/interfaces/calculation.py`
- Modify: `src/general_manager/manager/general_manager.py`
- Modify: `src/general_manager/manager/meta.py`
- Modify: `src/general_manager/api/property.py`
- Modify: `tests/unit/test_as_of.py`
- Modify: `tests/unit/test_general_manager.py`

- [ ] **Step 1: Write failing policy and stale-manager tests**

Define minimal test interfaces for all three policies. Assert:

```python
def test_calculation_interface_is_transparent_inside_as_of() -> None:
    with as_of("2022-01-01"):
        calculation = TransparentCalculation(1)
        assert calculation.identification == {"value": 1}


def test_request_interface_fails_before_loading_inside_as_of() -> None:
    with as_of("2022-01-01"):
        with pytest.raises(HistoricalReadNotSupportedError):
            UnsupportedRequest(id=1)


def test_live_manager_field_cannot_be_consumed_inside_as_of() -> None:
    manager = SimpleDatabaseManager(1)
    with as_of("2022-01-01"):
        with pytest.raises(HistoricalContextConflictError):
            _ = manager.name
```

Also test that a historically bound manager can be read outside a context and
inside the equal context, but not inside a different context.

- [ ] **Step 2: Run the tests and verify policy failures**

Run:

```bash
python -m pytest tests/unit/test_as_of.py tests/unit/test_general_manager.py -q
```

Expected: the new policy and compatibility cases fail.

- [ ] **Step 3: Declare explicit interface behavior**

Add to `InterfaceBase`:

```python
_as_of_behavior: ClassVar[AsOfBehavior] = "unsupported"
```

Set `OrmInterfaceBase._as_of_behavior = "historical"` and
`CalculationInterface._as_of_behavior = "transparent"`. Existing-model and
read-only interfaces inherit historical behavior from `OrmInterfaceBase`;
request-backed interfaces inherit the fail-closed default.

In `general_manager.as_of`, add:

```python
def ensure_as_of_read_supported(interface_cls: type[object]) -> None:
    if current_as_of_date() is None:
        return
    behavior = getattr(interface_cls, "_as_of_behavior", "unsupported")
    if behavior == "unsupported":
        raise HistoricalReadNotSupportedError(
            f"{interface_cls.__name__} does not support historical reads."
        )
```

Call `ensure_as_of_read_supported(cls)` as the first statement of
`InterfaceBase.filter`, `exclude`, and `all`. This makes request-backed class
queries fail before capability or transport work while allowing ORM and
calculation policies to continue.

- [ ] **Step 4: Bind each manager to its effective date**

In `GeneralManager.__init__`, call `ensure_as_of_read_supported(self.Interface)`
before constructing the interface. After construction, store:

```python
self._effective_search_date = getattr(
    self._interface,
    "_search_date",
    current_as_of_date(),
)
```

Set the same field in `_from_trusted_orm_instance()` and `_reload_interface_state()`.
Add:

```python
def _ensure_as_of_compatible(self) -> None:
    active = current_as_of_date()
    if active is not None and self._effective_search_date != active:
        raise HistoricalContextConflictError(
            f"{self.__class__.__name__} is bound to a different snapshot."
        )
```

Call it from `_ensure_manager_state_valid`, the generated descriptor in
`manager/meta.py`, and the getter wrapper installed by `GraphQLProperty` before
returning a cached or computed value. Keep ordinary reads outside a context
valid, including reads from an explicitly historical manager.

- [ ] **Step 5: Run manager and context tests**

Run:

```bash
python -m pytest tests/unit/test_as_of.py tests/unit/test_general_manager.py tests/unit/test_general_manager_meta.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit snapshot binding**

```bash
git add src/general_manager/interface/base_interface.py src/general_manager/interface/orm_interface.py src/general_manager/interface/interfaces/calculation.py src/general_manager/manager/general_manager.py src/general_manager/manager/meta.py src/general_manager/api/property.py tests/unit/test_as_of.py tests/unit/test_general_manager.py tests/unit/test_general_manager_meta.py
git commit -m "feat: bind managers to as-of snapshots"
```

## Task 3: Ambient ORM queries, buckets, and relations

**Files:**
- Modify: `src/general_manager/interface/orm_interface.py`
- Modify: `src/general_manager/interface/capabilities/orm/support.py`
- Modify: `src/general_manager/bucket/database_bucket.py`
- Modify: `src/general_manager/api/graphql_resolvers.py`
- Modify: `tests/unit/test_database_bucket.py`
- Modify: `tests/integration/test_database_manager.py`
- Modify: `tests/integration/test_existing_model_manager.py`
- Modify: `tests/integration/test_readonly_interface_manager.py`

- [ ] **Step 1: Write a failing end-to-end historical query test**

Extend the database integration fixture by creating a row, capturing a history
timestamp, updating the row, and asserting ambient reads return the old value:

```python
def test_as_of_applies_to_constructor_and_query_operations(self) -> None:
    human = self.TestHuman.create(name="Before", ignore_permission=True)
    snapshot = self.TestHuman.Interface._model.history.get(
        id=human.id,
        history_type="+",
    ).history_date
    human.update(name="After", ignore_permission=True)

    with as_of(snapshot):
        self.assertEqual(self.TestHuman(human.id).name, "Before")
        self.assertEqual(self.TestHuman.get(id=human.id).name, "Before")
        self.assertEqual(self.TestHuman.filter(name="Before").get().id, human.id)
        self.assertEqual(self.TestHuman.exclude(name="After").get().id, human.id)
        self.assertIn(human.id, [item.id for item in self.TestHuman.all()])
```

Patch `timezone.now()` or set `historical_lookup_buffer_seconds = 0` using the
existing test pattern so the snapshot reliably uses history tables.

- [ ] **Step 2: Write failing explicit/conflict and stale-bucket tests**

Cover an explicit equal date, explicit conflicting date, a live bucket created
before entering `as_of`, and a date-A bucket consumed under date B. Assert
`HistoricalContextConflictError` occurs before queryset evaluation.

- [ ] **Step 3: Run focused ORM tests and verify failure**

Run:

```bash
python -m pytest tests/unit/test_database_bucket.py tests/integration/test_database_manager.py -q
```

Expected: ambient reads remain live and the new assertions fail.

- [ ] **Step 4: Resolve dates in ORM construction and query normalization**

Change `OrmInterfaceBase.normalize_search_date()` to delegate non-`None` values
to the canonical normalizer. In both `__init__` and trusted hydration use:

```python
effective_search_date = resolve_search_date(search_date)
```

In `OrmQueryCapability._normalize_kwargs`, replace the local date/date-time
conversion with `resolve_search_date(payload.pop("search_date", None))`. Catch
`InvalidSearchDateError` at this existing query boundary and raise
`SearchDateInputError` from it, preserving the established explicit ORM query
contract. Inputs passed directly to `as_of` continue raising
`InvalidSearchDateError`.

- [ ] **Step 5: Validate bucket compatibility at every public boundary**

Add to `DatabaseBucket`:

```python
def _ensure_as_of_compatible(self) -> None:
    active = current_as_of_date()
    if active is not None and self._search_date != active:
        raise HistoricalContextConflictError(
            "DatabaseBucket is bound to a different snapshot."
        )
```

Invoke it before queryset access or derived bucket construction in iteration,
length/count, indexing, `first`, `get`, `filter`, `exclude`, sorting, grouping,
union, and relation-prefetch paths. Derived buckets continue copying
`self._search_date`; an explicit reserved `search_date` is passed through
`resolve_search_date` and checked against the parent bucket date.

At GraphQL list/detail resolver entry, validate the parent manager and base
bucket before permission filtering, pagination, or materialization.

- [ ] **Step 6: Add historical relation coverage**

Use the existing `TestHuman.country` and `TestFamily.humans_list` fixtures to
assert forward, reverse, and many-to-many traversal return rows at the same
snapshot. Update a related row after the snapshot and assert the historical
relation exposes the old related value rather than current state.

- [ ] **Step 7: Run ORM, existing-model, and read-only integration tests**

Run:

```bash
python -m pytest tests/unit/test_database_bucket.py tests/integration/test_database_manager.py tests/integration/test_existing_model_manager.py tests/integration/test_readonly_interface_manager.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit ORM propagation**

```bash
git add src/general_manager/interface/orm_interface.py src/general_manager/interface/capabilities/orm/support.py src/general_manager/bucket/database_bucket.py src/general_manager/api/graphql_resolvers.py tests/unit/test_database_bucket.py tests/integration/test_database_manager.py tests/integration/test_existing_model_manager.py tests/integration/test_readonly_interface_manager.py
git commit -m "feat: propagate as-of dates through orm reads"
```

## Task 4: Read-only mutation guard

**Files:**
- Modify: `src/general_manager/as_of.py`
- Modify: `src/general_manager/manager/general_manager.py`
- Modify: `src/general_manager/interface/base_interface.py`
- Modify: `tests/unit/test_general_manager.py`
- Modify: `tests/integration/test_database_manager.py`
- Modify: `tests/integration/test_remote_manager_interface.py`

- [ ] **Step 1: Write failing create, update, and delete guard tests**

Patch permission methods, interface mutation methods, data-change signaling, and
request transports. For each public mutation assert none were called:

```python
with as_of("2022-01-01"):
    with self.assertRaises(HistoricalMutationError):
        self.TestHuman.create(name="Blocked", ignore_permission=True)
    with self.assertRaises(HistoricalMutationError):
        self.test_human1.update(name="Blocked", ignore_permission=True)
    with self.assertRaises(HistoricalMutationError):
        self.test_human1.delete(ignore_permission=True)
```

Repeat one path with `ignore_permission=False` and assert the permission mock has
zero calls. Add direct `Interface.create/update/delete` coverage to exercise the
defense-in-depth guard.

- [ ] **Step 2: Run mutation tests and verify side effects currently occur**

Run:

```bash
python -m pytest tests/unit/test_general_manager.py tests/integration/test_database_manager.py tests/integration/test_remote_manager_interface.py -q
```

Expected: the historical mutation assertions fail.

- [ ] **Step 3: Implement and place the shared guard first**

Add:

```python
def reject_historical_mutation() -> None:
    active = current_as_of_date()
    if active is not None:
        raise HistoricalMutationError(
            f"Mutations are not allowed in an as_of context ({active.isoformat()})."
        )
```

Call it as the first statement of `GeneralManager.create`, `update`, and
`delete`, before invalid-state and permission checks. Call it at the beginning
of `InterfaceBase.create`, `update`, and `delete` so custom or direct framework
calls are also blocked. Confirm the `@data_change` wrapper emits nothing when
the wrapped mutation raises before persistence.

- [ ] **Step 4: Run mutation and notification regression tests**

Run:

```bash
python -m pytest tests/unit/test_general_manager.py tests/unit/test_notification_batching.py tests/integration/test_database_manager.py tests/integration/test_remote_manager_interface.py -q
```

Expected: all tests pass and no mutation mock is called.

- [ ] **Step 5: Commit mutation protection**

```bash
git add src/general_manager/as_of.py src/general_manager/manager/general_manager.py src/general_manager/interface/base_interface.py tests/unit/test_general_manager.py tests/integration/test_database_manager.py tests/integration/test_remote_manager_interface.py
git commit -m "feat: reject mutations in as-of contexts"
```

## Task 5: Persistent and run-scoped cache isolation

**Files:**
- Modify: `src/general_manager/utils/json_encoder.py`
- Modify: `src/general_manager/utils/make_cache_key.py`
- Modify: `src/general_manager/cache/run_context.py`
- Modify: `src/general_manager/api/graphql_warmup_registry.py`
- Modify: `src/general_manager/api/graphql_warmup.py`
- Modify: `tests/unit/test_make_cache_key.py`
- Modify: `tests/unit/test_cache_decorator.py`
- Modify: `tests/unit/test_calculation_run_context.py`
- Modify: `tests/unit/test_graphql_warmup.py`
- Modify: `tests/unit/test_graphql_warmup_registry.py`
- Modify: `tests/integration/test_caching.py`

- [ ] **Step 1: Write failing cache-key isolation tests**

Assert one call has three distinct identities for current, date A, and date B;
equal normalized inputs share a key:

```python
current_key = make_cache_key(sample_function, (1,), {})
with as_of("2022-01-01"):
    date_a_key = make_cache_key(sample_function, (1,), {})
with as_of(datetime(2022, 1, 1)):
    normalized_date_a_key = make_cache_key(sample_function, (1,), {})
with as_of("2022-01-02"):
    date_b_key = make_cache_key(sample_function, (1,), {})

assert current_key != date_a_key
assert date_a_key == normalized_date_a_key
assert date_a_key != date_b_key
```

Add the same assertions for the optimized single-manager path and an explicitly
historical manager used outside a context.

- [ ] **Step 2: Write a failing sequential run-context test**

Within one `CalculationRunContext`, call a `@cached(cache="run")` function under
date A and then date B. Assert the function executes twice and returns each
date's result. Re-enter date A and assert its first result is reused.

- [ ] **Step 3: Run cache tests and verify collisions**

Run:

```bash
python -m pytest tests/unit/test_make_cache_key.py tests/unit/test_cache_decorator.py tests/unit/test_calculation_run_context.py -q
```

Expected: date-scoped keys collide before implementation.

- [ ] **Step 4: Add the canonical fingerprint to cache serialization**

Add:

```python
def as_of_cache_fingerprint() -> str | None:
    active = current_as_of_date()
    return None if active is None else active.isoformat()
```

In every `make_cache_key` payload add `"as_of"` only when the fingerprint is
not `None`. Extend the optimized `_single_manager_arg_cache_key_from_repr`
arguments and serialized bytes with the active fingerprint.

For manager serialization, keep the exact current string when
`_effective_search_date is None`; append a deterministic
`@as_of(<isoformat>)` suffix for historical managers. Make the optimized and
generic paths produce identical hashes, retaining the existing equivalence
tests.

- [ ] **Step 5: Namespace all CalculationRunContext values**

Add a private method:

```python
@staticmethod
def _scoped_key(key: Hashable) -> Hashable:
    fingerprint = as_of_cache_fingerprint()
    if fingerprint is None:
        return key
    return ("as_of", fingerprint, key)
```

Apply it consistently in `get`, `set`, `get_or_set`, `has`, deletion, prefix
discard, index, and group helpers. Prefix operations must scope the prefix
before matching so invalidation under one date does not remove another date's
entries. Dependency-cache hit keys are already derived from `make_cache_key`;
do not double-transform their string keys outside the normal run-value map.

- [ ] **Step 6: Preserve historical context in GraphQL warm-up recipes**

Bump `RECIPE_VERSION` in `graphql_warmup_registry.py` and add this frozen field
to `GraphQLWarmUpRecipe`:

```python
search_date: datetime | None = None
```

Populate it from a manager's `_effective_search_date` in `_recipe_for`. In
`warm_up_graphql_recipe`, wrap manager reconstruction, timeout refresh, and
dependency property evaluation in:

```python
execution_context = (
    nullcontext()
    if recipe.search_date is None
    else as_of(search_date=recipe.search_date)
)
with execution_context:
    warmed = _execute_graphql_warmup_recipe(recipe)
```

Extract the current manager import, reconstruction, property lookup, timeout
refresh, dependency evaluation, and recipe re-registration body into the
private `_execute_graphql_warmup_recipe(recipe) -> bool` helper without changing
its branches. The helper returns the existing `warmed` result; the outer
function retains lock acquisition, exception logging, and lock release.

Add registry round-trip tests for current and historical recipes. Add executor
tests proving a historical recipe reconstructs a historical manager, generates
the original cache key, and leaves `current_as_of_date()` as `None` afterward.
Version-1 cached recipes must be ignored through the existing version gate.

- [ ] **Step 7: Add an integration calculation cache test**

Create a cached property whose result reads a historically changing manager.
Assert current, date A, and date B return the correct values using both timeout
and dependency cache modes, then inspect call counts to prove no cross-date hit.

- [ ] **Step 8: Run cache tests**

Run:

```bash
python -m pytest tests/unit/test_make_cache_key.py tests/unit/test_cache_decorator.py tests/unit/test_calculation_run_context.py tests/unit/test_graphql_warmup.py tests/unit/test_graphql_warmup_registry.py tests/integration/test_caching.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit cache isolation**

```bash
git add src/general_manager/utils/json_encoder.py src/general_manager/utils/make_cache_key.py src/general_manager/cache/run_context.py src/general_manager/api/graphql_warmup_registry.py src/general_manager/api/graphql_warmup.py tests/unit/test_make_cache_key.py tests/unit/test_cache_decorator.py tests/unit/test_calculation_run_context.py tests/unit/test_graphql_warmup.py tests/unit/test_graphql_warmup_registry.py tests/integration/test_caching.py
git commit -m "fix: isolate caches by as-of snapshot"
```

## Task 6: GraphQL `@asOf` directive and schema registration

**Files:**
- Create: `src/general_manager/api/graphql_as_of.py`
- Modify: `src/general_manager/bootstrap.py`
- Modify: `tests/unit/test_graph_ql.py`

- [ ] **Step 1: Write failing directive schema tests**

Extend `GraphQLDirectiveRegistrationTests` to assert:

```python
schema = self._build_bootstrap_schema()
directive = schema.graphql_schema.get_directive("asOf")
self.assertIsNotNone(directive)
self.assertEqual(str(directive.args["date"].type), "DateTime!")
self.assertEqual(directive.locations, (DirectiveLocation.QUERY,))
self.assertIs(
    directive.args["date"].type.of_type,
    schema.graphql_schema.get_type("DateTime"),
)
```

Add a custom `GRAPHQL_DIRECTIVES` setting named `asOf` and assert bootstrap
raises `DuplicateGraphQLDirectiveError`. Retain the existing custom-directive
merge and subscription-root tests.

- [ ] **Step 2: Run directive registration tests and verify absence**

Run:

```bash
python -m pytest tests/unit/test_graph_ql.py::GraphQLDirectiveRegistrationTests -q
```

Expected: `schema.graphql_schema.get_directive("asOf")` returns `None`.

- [ ] **Step 3: Build the directive from the schema's DateTime scalar**

In `graphql_as_of.py`, implement:

```python
def build_as_of_directive(date_time_type: GraphQLInputType) -> GraphQLDirective:
    return GraphQLDirective(
        name="asOf",
        description="Execute this query against one historical snapshot.",
        locations=(DirectiveLocation.QUERY,),
        args={
            "date": GraphQLArgument(
                GraphQLNonNull(date_time_type),
                description="Historical snapshot date.",
            )
        },
    )
```

Do not instantiate a second `GraphQLScalarType(name="DateTime")`; Graphene
would reject the schema for duplicate named types.

- [ ] **Step 4: Attach the directive without duplicating DateTime**

In bootstrap, always reserve `asOf` when validating settings-provided custom
directives. Ensure the initial Graphene schema includes `graphene.DateTime` via
its `types` argument, then obtain:

```python
date_time_type = schema.graphql_schema.get_type("DateTime")
schema_kwargs = schema.graphql_schema.to_kwargs()
schema_kwargs["directives"] = (
    *schema.graphql_schema.directives,
    build_as_of_directive(cast(GraphQLInputType, date_time_type)),
)
schema.graphql_schema = GraphQLSchema(**schema_kwargs)
```

Encapsulate reconstruction in a small helper and assert `date_time_type` is a
GraphQL input type. Preserve all custom directives, schema description,
extensions, AST nodes, and root types from `to_kwargs()`.

- [ ] **Step 5: Run schema and bootstrap tests**

Run:

```bash
python -m pytest tests/unit/test_graph_ql.py::GraphQLDirectiveRegistrationTests -q
```

Expected: all tests pass with one shared `DateTime` type.

- [ ] **Step 6: Commit directive registration**

```bash
git add src/general_manager/api/graphql_as_of.py src/general_manager/bootstrap.py tests/unit/test_graph_ql.py
git commit -m "feat: register graphql as-of directive"
```

## Task 7: GraphQL operation extraction and execution context

**Files:**
- Modify: `src/general_manager/api/graphql_as_of.py`
- Modify: `src/general_manager/api/graphql_view.py`
- Modify: `src/general_manager/api/graphql_errors.py`
- Modify: `tests/unit/test_graph_ql.py`

- [ ] **Step 1: Write failing literal, variable, and operation-selection tests**

Test a helper named `extract_as_of_search_date` directly:

```python
query = """
    query Current { ping }
    query Historical($date: DateTime!) @asOf(date: $date) { ping }
"""
assert extract_as_of_search_date(
    query=query,
    variables={"date": "2022-01-01T00:00:00Z"},
    operation_name="Historical",
) == datetime(2022, 1, 1, tzinfo=datetime_timezone.utc)
assert extract_as_of_search_date(
    query=query,
    variables={"date": "2022-01-01T00:00:00Z"},
    operation_name="Current",
) is None
```

Add cases for literal values, absent directives, missing date argument,
unresolved variables, duplicate `@asOf`, invalid dates, mutation use, and
subscription use. Public failures must be `GraphQLError` instances with stable
codes such as `BAD_USER_INPUT`, `HISTORICAL_CONTEXT_CONFLICT`, or
`GRAPHQL_VALIDATION_FAILED`.

- [ ] **Step 2: Write a failing view activation test**

Patch `as_of` and `ensure_calculation_run_context` with recording context
managers. Assert the order is:

```text
as_of enter -> calculation enter -> execute -> calculation exit -> as_of exit
```

Also assert a plain query never enters `as_of` and an extraction error never
calls `execute_graphql_request`.

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```bash
python -m pytest tests/unit/test_graph_ql.py -k "AsOf or as_of" -q
```

Expected: helper imports and activation assertions fail.

- [ ] **Step 4: Implement operation-aware extraction**

Use GraphQL-core `parse()` and `get_operation_ast()` to select exactly the
operation Graphene will execute. Find a single `asOf` directive and its single
`date` argument. Resolve literals and variables with `value_from_ast_untyped`,
then call `normalize_search_date`.

Return `None` for a valid selected operation without the directive. Convert
missing/duplicate arguments, unresolved variables, invalid dates, and non-query
locations into `PublicGraphQLError` with the stable codes above. Let ordinary
GraphQL syntax errors remain GraphQL syntax errors.

- [ ] **Step 5: Enter `as_of` around GraphQL execution**

In `GeneralManagerGraphQLView.get_response`, after
`get_graphql_params()` and before execution:

```python
search_date = extract_as_of_search_date(
    query=query,
    variables=variables,
    operation_name=operation_name,
)
execution_context = nullcontext() if search_date is None else as_of(search_date)
with execution_context, ensure_calculation_run_context():
    execution_result = self.execute_graphql_request(
        request,
        data,
        query,
        variables,
        operation_name,
        show_graphiql,
    )
```

Catch only public extraction `GraphQLError`s and shape them as an
`ExecutionResult(data=None, errors=[error])`, matching existing response and
metrics behavior. Do not catch resolver exceptions that Graphene normally owns.

- [ ] **Step 6: Add resolver error mapping**

Add `historical_graphql_error(error)` in `graphql_errors.py`, mapping the four
historical exception classes to these codes:

```python
_HISTORICAL_ERROR_CODES = {
    InvalidSearchDateError: "BAD_USER_INPUT",
    HistoricalContextConflictError: "HISTORICAL_CONTEXT_CONFLICT",
    HistoricalMutationError: "HISTORICAL_MUTATION_FORBIDDEN",
    HistoricalReadNotSupportedError: "HISTORICAL_READ_NOT_SUPPORTED",
}
```

Override `GeneralManagerGraphQLView.format_error`. If a GraphQL error's
`original_error` is one of these types, replace it with a `PublicGraphQLError`
using the mapped code and the historical exception's concise message, while
preserving GraphQL `path` and source locations in the returned formatted
mapping. Delegate all other errors unchanged to `super().format_error`.
Continue logging the full original exception server-side without exposing its
cause or traceback.

- [ ] **Step 7: Add HTTP batch-isolation and cleanup tests**

Execute two batch items with different dates and record the active date inside
each resolver. Assert each sees only its requested value and
`current_as_of_date()` is `None` after success and after a resolver error.

- [ ] **Step 8: Run GraphQL tests**

Run:

```bash
python -m pytest tests/unit/test_graph_ql.py tests/integration/test_graphql_dependency_cache_prefetch.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit GraphQL execution support**

```bash
git add src/general_manager/api/graphql_as_of.py src/general_manager/api/graphql_view.py src/general_manager/api/graphql_errors.py tests/unit/test_graph_ql.py
git commit -m "feat: execute graphql queries as of a date"
```

## Task 8: Calculation propagation and unsupported-interface integration

**Files:**
- Modify: `tests/integration/test_calculation_manager.py`
- Modify: `tests/integration/test_remote_manager_interface.py`
- Modify: `tests/unit/test_calculation_bucket.py`
- Modify: `src/general_manager/bucket/calculation_bucket.py`
- Modify: `src/general_manager/interface/interfaces/request.py`
- Modify: `src/general_manager/interface/capabilities/request/__init__.py`

- [ ] **Step 1: Write a transitive calculation integration test**

Define a calculation manager whose input or property reads an ORM manager that
changes after the snapshot. Add a second calculation property that calls the
first calculation indirectly. Under `as_of(snapshot)`, assert both direct and
transitive results use the historical row without either calculation accepting
a `search_date` parameter.

- [ ] **Step 2: Write calculation bucket coverage**

Under one `as_of` scope, exercise calculation `all`, `filter`, `exclude`, input
combination generation, and manager-typed possible values. Assert every ORM
manager materialized by the calculation bucket carries the active effective
date. Create a calculation bucket outside the scope, consume it inside the
scope, and assert `HistoricalContextConflictError`.

- [ ] **Step 3: Write fail-closed request coverage**

Patch the configured request transport and assert constructor, `get`, `filter`,
`exclude`, `all`, and named query-operation paths each raise
`HistoricalReadNotSupportedError` with zero transport calls.

- [ ] **Step 4: Bind calculation buckets and guard request-specific entry points**

In `CalculationBucket.__init__`, retain:

```python
self._effective_search_date = current_as_of_date()
```

Add `_ensure_as_of_compatible()` with the same active-versus-bound comparison
used by `DatabaseBucket`, and invoke it before combination generation,
iteration, filtering, exclusion, grouping, indexing, and manager construction.
Derived calculation buckets copy the bound value instead of reading a new one.

In `RequestInterface.query_operation` and request query capability public
methods (`for_operation`, `filter`, `exclude`, and `all`), call
`ensure_as_of_read_supported(interface_cls)` before resolving a plan, payload,
or transport. These checks cover request-specific APIs that do not pass through
`InterfaceBase.filter/exclude/all`.

- [ ] **Step 5: Run calculation and request integration tests**

Run:

```bash
python -m pytest tests/integration/test_calculation_manager.py tests/unit/test_calculation_bucket.py tests/integration/test_remote_manager_interface.py -q
```

Expected: all tests pass, stale calculation buckets raise at their public
boundary, and request operations make zero transport calls. Do not add
`search_date` parameters to calculation APIs.

- [ ] **Step 6: Commit calculation and unsupported-interface coverage**

```bash
git add src/general_manager/bucket/calculation_bucket.py src/general_manager/interface/interfaces/request.py src/general_manager/interface/capabilities/request/__init__.py tests/integration/test_calculation_manager.py tests/unit/test_calculation_bucket.py tests/integration/test_remote_manager_interface.py
git commit -m "feat: propagate as-of through calculation queries"
```

## Task 9: Documentation and final verification

**Files:**
- Modify: `docs/api/interface.md`
- Modify: `docs/api/graphql.md`
- Modify: `docs/api/cache.md`
- Modify: `docs/quickstart.md`

- [ ] **Step 1: Document the Python contract**

Add an `as_of` section to `docs/api/interface.md` with positional and keyword
examples, accepted types, timezone normalization, equal nesting, conflict
errors, fail-closed interfaces, and the strict mutation rule:

```python
from general_manager.api import as_of

with as_of(search_date="2022-01-01"):
    project = Project(1)
    projects = Project.filter(status="active")
```

- [ ] **Step 2: Document GraphQL usage**

Add literal and variable `@asOf(date:)` examples to `docs/api/graphql.md`.
State that it is query-only, applies to every resolver in the selected
operation, rejects unsupported interfaces, and isolates batch items.

- [ ] **Step 3: Document cache semantics and migration impact**

In `docs/api/cache.md`, explain that effective historical dates namespace run,
timeout, and dependency cache identities. Note the possible one-time cold cache
after key-version deployment and the correctness-first tradeoff for granular
timestamps.

- [ ] **Step 4: Run documentation and formatting checks**

Run:

```bash
ruff format --check src tests
ruff check src tests
python -m pytest tests/docs -q
```

Expected: all commands exit 0.

- [ ] **Step 5: Run type checking**

Run:

```bash
mypy src
```

Expected: exit 0 with no type errors.

- [ ] **Step 6: Run the complete test suite**

Run:

```bash
python -m pytest
```

Expected: all tests pass.

- [ ] **Step 7: Run pre-commit on the complete diff**

Run:

```bash
pre-commit run --all-files
```

Expected: every hook passes. If a formatter changes files, inspect the diff and
rerun the affected focused tests plus pre-commit.

- [ ] **Step 8: Commit documentation**

```bash
git add docs/api/interface.md docs/api/graphql.md docs/api/cache.md docs/quickstart.md
git commit -m "docs: explain as-of historical queries"
```

- [ ] **Step 9: Verify the final branch state**

Run:

```bash
git status --short
git log --oneline --decorate -10
git diff 78c7b1bb..HEAD --check
```

Expected: no unintended unstaged files, focused conventional commits, and no
whitespace errors relative to the branch's `0.65.0` base commit.
