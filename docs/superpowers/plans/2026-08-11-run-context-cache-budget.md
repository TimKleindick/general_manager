# Run-context Cache Memory Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one opt-in setting that bounds the estimated memory retained by all eviction-safe `CalculationRunContext` entries in each Python process.

**Architecture:** A new private `run_context_lru` module owns configuration validation, bounded recursive size estimation, and a thread-safe process-local weighted LRU coordinator. `CalculationRunContext` remains the owner of cached values and integrates with the coordinator for admission, recency, removal, context cleanup, dependency-hit pinning, and reweighing of internally mutated indexes.

**Tech Stack:** Python 3.12+, Django settings, standard-library `OrderedDict`, `RLock`, `sys.getsizeof`, `weakref`, pytest, Ruff, and mypy; no new dependencies.

## Global Constraints

- The only new setting is `RUN_CONTEXT_CACHE_MAX_BYTES`, resolved with the existing `get_setting()` precedence.
- Missing or `None` means unlimited; `0` disables eviction-safe retention; positive integers set the process-wide estimated byte budget.
- Negative integers, booleans, and non-integers raise `django.core.exceptions.ImproperlyConfigured` when a context is created.
- The budget covers `_values` and non-pending dependency-cache hits across all live contexts in one process.
- Pending dependency publications and their same-run hits stay pinned until flush or discard and retain the existing lease lifecycle.
- Size accounting is conservative and insertion-time approximate, not a hard RSS guarantee.
- Public `CalculationRunContext` signatures, exports, nesting, `ContextVar`, historical namespace, and loader-result behavior remain compatible.
- Do not add dependencies or unrelated refactors.

---

## File Structure

- Create `src/general_manager/cache/run_context_lru.py`: private configuration resolver, object-size estimator, owner protocol, tracked-entry record, and reusable process-local weighted LRU coordinator.
- Create `tests/unit/test_run_context_lru.py`: focused tests for validation, estimation, and coordinator behavior independent of `CalculationRunContext`.
- Modify `src/general_manager/cache/run_context.py`: register contexts and route value/hit access, mutation, cleanup, pinning, and reweighing through the coordinator.
- Modify `tests/unit/test_calculation_run_context.py`: public behavior and lifecycle integration tests.
- Modify `docs/api/cache.md`: precise API/configuration contract.
- Modify `docs/concepts/caching.md`: deployment guidance and example.

### Task 1: Configuration and bounded size estimation

**Files:**
- Create: `src/general_manager/cache/run_context_lru.py`
- Create: `tests/unit/test_run_context_lru.py`

**Interfaces:**
- Consumes: `general_manager.conf.get_setting(key: str, default: object = None) -> object`.
- Produces: `resolve_run_context_cache_max_bytes() -> int | None` and `estimate_cache_entry_size(key: object, value: object, *, stop_after: int | None) -> int`.

- [ ] **Step 1: Write failing configuration tests**

```python
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
import pytest

from general_manager.cache.run_context_lru import (
    resolve_run_context_cache_max_bytes,
)


@pytest.mark.parametrize("configured", [None, 0, 1, 1024])
def test_resolve_run_context_cache_max_bytes_accepts_supported_values(
    configured: int | None,
) -> None:
    with override_settings(
        GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": configured}
    ):
        assert resolve_run_context_cache_max_bytes() == configured


@override_settings(GENERAL_MANAGER={})
def test_resolve_run_context_cache_max_bytes_defaults_to_unlimited() -> None:
    assert resolve_run_context_cache_max_bytes() is None


@pytest.mark.parametrize("configured", [-1, True, False, 1.5, "1024"])
def test_resolve_run_context_cache_max_bytes_rejects_invalid_values(
    configured: object,
) -> None:
    with (
        override_settings(
            GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": configured}
        ),
        pytest.raises(ImproperlyConfigured, match="RUN_CONTEXT_CACHE_MAX_BYTES"),
    ):
        resolve_run_context_cache_max_bytes()
```

- [ ] **Step 2: Run the configuration tests and verify RED**

Run: `python -m pytest tests/unit/test_run_context_lru.py -k resolve -v`

Expected: collection fails because `general_manager.cache.run_context_lru` does not exist.

- [ ] **Step 3: Implement strict setting validation**

Create the module with these definitions:

```python
"""Process-local memory accounting for calculation run caches."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from general_manager.conf import get_setting

RUN_CONTEXT_CACHE_MAX_BYTES_SETTING = "RUN_CONTEXT_CACHE_MAX_BYTES"
MIN_TRACKED_ENTRY_BYTES = 256


def resolve_run_context_cache_max_bytes() -> int | None:
    configured = get_setting(RUN_CONTEXT_CACHE_MAX_BYTES_SETTING)
    if configured is None:
        return None
    if isinstance(configured, bool) or not isinstance(configured, int):
        raise ImproperlyConfigured(
            "GENERAL_MANAGER[\"RUN_CONTEXT_CACHE_MAX_BYTES\"] must be "
            "None or a non-negative integer number of bytes."
        )
    if configured < 0:
        raise ImproperlyConfigured(
            "GENERAL_MANAGER[\"RUN_CONTEXT_CACHE_MAX_BYTES\"] must be "
            "None or a non-negative integer number of bytes."
        )
    return configured
```

- [ ] **Step 4: Run the configuration tests and verify GREEN**

Run: `python -m pytest tests/unit/test_run_context_lru.py -k resolve -v`

Expected: all configuration cases pass.

- [ ] **Step 5: Write failing estimator tests**

Append tests that require cycle safety, shared-reference deduplication within one entry, a minimum bookkeeping charge, and bounded traversal:

```python
from general_manager.cache.run_context_lru import (
    MIN_TRACKED_ENTRY_BYTES,
    estimate_cache_entry_size,
)


def test_estimate_cache_entry_size_handles_cycles() -> None:
    value: list[object] = []
    value.append(value)

    size = estimate_cache_entry_size("cycle", value, stop_after=None)

    assert size >= MIN_TRACKED_ENTRY_BYTES


def test_estimate_cache_entry_size_counts_shared_object_once_per_entry() -> None:
    shared = [bytearray(1024)]

    shared_size = estimate_cache_entry_size(
        "key", [shared, shared], stop_after=None
    )
    copied_size = estimate_cache_entry_size(
        "key", [[bytearray(1024)], [bytearray(1024)]], stop_after=None
    )

    assert shared_size < copied_size


def test_estimate_cache_entry_size_stops_after_budget() -> None:
    size = estimate_cache_entry_size(
        "key",
        [b"x" * 1024 for _ in range(100)],
        stop_after=512,
    )

    assert size == 513
```

- [ ] **Step 6: Run estimator tests and verify RED**

Run: `python -m pytest tests/unit/test_run_context_lru.py -k estimate -v`

Expected: imports or assertions fail because the estimator is not implemented.

- [ ] **Step 7: Implement the bounded recursive estimator**

Implement an iterative traversal that returns `stop_after + 1` as soon as the estimate crosses a finite ceiling. Use one `seen: set[int]` for the key and value together. Traverse exact built-in mappings, tuples, lists, sets, and frozensets; use `object.__getattribute__(candidate, "__dict__")` inside `try/except` for ordinary instances; inspect slot names from the class MRO without calling application-defined descriptors; and treat modules, classes, functions, methods, and code objects as shallow leaves. Call `sys.getsizeof()` only for exact supported built-in types; use `object.__sizeof__(candidate)` for arbitrary instances so an application-defined `__sizeof__()` cannot run. Catch sizing errors and charge `MIN_TRACKED_ENTRY_BYTES`. Finish with:

```python
return max(MIN_TRACKED_ENTRY_BYTES, measured_bytes)
```

Do not use `gc.get_referents()`: it can walk interpreter and module graphs that the cache entry does not own.

- [ ] **Step 8: Run all new module tests and verify GREEN**

Run: `python -m pytest tests/unit/test_run_context_lru.py -v`

Expected: all resolver and estimator tests pass.

- [ ] **Step 9: Commit the independently usable estimator**

```bash
git add src/general_manager/cache/run_context_lru.py tests/unit/test_run_context_lru.py
git commit -m "feat: estimate run-context cache memory"
```

### Task 2: Process-wide weighted LRU coordinator

**Files:**
- Modify: `src/general_manager/cache/run_context_lru.py`
- Modify: `tests/unit/test_run_context_lru.py`

**Interfaces:**
- Consumes: `estimate_cache_entry_size(...)` from Task 1.
- Produces: `RunContextCacheOwner`, `ProcessRunContextCacheBudget`, and singleton `run_context_cache_budget` with `register()`, `track()`, `touch()`, `remove()`, `refresh()`, and `clear_context()` methods.

- [ ] **Step 1: Write a small real owner used by coordinator tests**

```python
from collections.abc import Hashable, Iterable
from typing import Literal

from general_manager.cache.run_context_lru import ProcessRunContextCacheBudget

Namespace = Literal["values", "dependency_hits"]


class CacheOwner:
    def __init__(self) -> None:
        self.entries: dict[tuple[Namespace, Hashable], object] = {}

    def store(self, namespace: Namespace, key: Hashable, value: object) -> None:
        self.entries[(namespace, key)] = value

    def _iter_run_cache_entries(
        self,
    ) -> Iterable[tuple[Namespace, Hashable, object]]:
        for (namespace, key), value in self.entries.items():
            yield namespace, key, value

    def _evict_run_cache_entry(
        self, namespace: Namespace, key: Hashable
    ) -> None:
        self.entries.pop((namespace, key), None)
```

- [ ] **Step 2: Write failing global LRU tests**

Use equal-length keys and values, calculate one weight with `estimate_cache_entry_size()`, and create a coordinator instance with no module-global state:

```python
def test_budget_evicts_oldest_entry_across_owners() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(first, entry_size * 2)
    budget.register(second, entry_size * 2)

    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    second.store("values", "b", "B")
    budget.track(second, "values", "b", "B")
    second.store("values", "c", "C")
    budget.track(second, "values", "c", "C")

    assert ("values", "a") not in first.entries
    assert ("values", "b") in second.entries
    assert ("values", "c") in second.entries


def test_budget_touch_refreshes_recency() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    budget.register(owner, entry_size * 2)
    for key, value in (("a", "A"), ("b", "B")):
        owner.store("values", key, value)
        budget.track(owner, "values", key, value)

    budget.touch(owner, "values", "a")
    owner.store("values", "c", "C")
    budget.track(owner, "values", "c", "C")

    assert ("values", "a") in owner.entries
    assert ("values", "b") not in owner.entries


def test_budget_does_not_admit_entry_larger_than_limit() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 256)
    owner.store("values", "large", b"x" * 1024)

    budget.track(owner, "values", "large", b"x" * 1024)

    assert ("values", "large") not in owner.entries


def test_budget_replacement_and_remove_update_accounting() -> None:
    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "key", "small")
    budget.track(owner, "values", "key", "small")
    small_size = budget.estimated_bytes

    owner.store("values", "key", b"x" * 1024)
    budget.track(owner, "values", "key", b"x" * 1024)

    assert budget.estimated_bytes > small_size
    budget.remove(owner, "values", "key")
    assert budget.estimated_bytes == 0


def test_budget_clear_context_preserves_other_owner_accounting() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    budget.register(first, 10_000)
    budget.register(second, 10_000)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    second.store("values", "b", "B")
    budget.track(second, "values", "b", "B")
    second_size = estimate_cache_entry_size("b", "B", stop_after=None)

    budget.clear_context(first)

    assert budget.estimated_bytes == second_size
    assert ("values", "b") in second.entries


def test_budget_releases_dead_owner_accounting() -> None:
    import gc
    from weakref import ref

    budget = ProcessRunContextCacheBudget()
    owner = CacheOwner()
    budget.register(owner, 10_000)
    owner.store("values", "a", "A")
    budget.track(owner, "values", "a", "A")
    owner_reference = ref(owner)

    del owner
    gc.collect()

    assert owner_reference() is None
    assert budget.estimated_bytes == 0


def test_budget_rebuilds_live_owners_when_limit_changes() -> None:
    budget = ProcessRunContextCacheBudget()
    first = CacheOwner()
    second = CacheOwner()
    budget.register(first, None)
    first.store("values", "a", "A")
    budget.track(first, "values", "a", "A")
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)

    budget.register(second, entry_size)
    second.store("values", "b", "B")
    budget.track(second, "values", "b", "B")

    assert ("values", "a") not in first.entries
    assert ("values", "b") in second.entries
```

- [ ] **Step 3: Run coordinator tests and verify RED**

Run: `python -m pytest tests/unit/test_run_context_lru.py -k budget -v`

Expected: import fails because `ProcessRunContextCacheBudget` does not exist.

- [ ] **Step 4: Implement coordinator types and state**

Add:

```python
from collections import OrderedDict
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from threading import RLock
from typing import Literal, Protocol
from weakref import ReferenceType, WeakSet, ref

RunCacheNamespace = Literal["values", "dependency_hits"]
TrackedKey = tuple[int, RunCacheNamespace, Hashable]


class RunContextCacheOwner(Protocol):
    def _iter_run_cache_entries(
        self,
    ) -> Iterable[tuple[RunCacheNamespace, Hashable, object]]: ...

    def _evict_run_cache_entry(
        self, namespace: RunCacheNamespace, key: Hashable
    ) -> None: ...


@dataclass(frozen=True)
class _TrackedEntry:
    owner: ReferenceType[RunContextCacheOwner]
    namespace: RunCacheNamespace
    key: Hashable
    size: int
```

`ProcessRunContextCacheBudget.__init__()` owns an `RLock`, `WeakSet` of owners, `OrderedDict[TrackedKey, _TrackedEntry]`, `_total_bytes`, and `_max_bytes`. Expose `estimated_bytes` as a read-only, lock-protected integer property for diagnostics and deterministic unit assertions.

- [ ] **Step 5: Implement coordinator operations and eviction**

Use `(id(owner), namespace, key)` as the internal key. `register(owner, max_bytes)` adds the owner and, when the process limit changes, rebuilds accounting by walking `_iter_run_cache_entries()` for every live owner. `track()` removes prior accounting, estimates with `stop_after=max_bytes`, adds an admitted entry at the MRU end, then repeatedly pops the LRU entry and calls its live owner's `_evict_run_cache_entry()` until total size is within the budget. A value estimated at `max_bytes + 1` is evicted immediately.

`touch()` uses `OrderedDict.move_to_end()`. `remove()` subtracts one known weight without altering owner storage. `refresh()` is an alias for re-running `track()` on an already stored mutable value. `clear_context()` removes all records whose owner id matches and unregisters the owner. Weak-reference callbacks perform the same accounting cleanup without calling the dead owner.

Create `logger = get_logger("cache.run_context_lru")`. Emit debug events after an LRU eviction and when an entry is skipped because its estimate exceeds the whole budget. Log only namespace, estimated bytes, and configured bytes; do not log opaque caller keys or values.

When `_max_bytes is None`, retain only the weak owner registration and skip per-entry bookkeeping. When the limit changes from unlimited to finite, rebuild from every live owner; when it changes to unlimited, clear weighted bookkeeping without evicting owner values.

Expose one singleton:

```python
run_context_cache_budget = ProcessRunContextCacheBudget()
```

- [ ] **Step 6: Run coordinator tests and verify GREEN**

Run: `python -m pytest tests/unit/test_run_context_lru.py -v`

Expected: all resolver, estimator, and coordinator tests pass without warnings.

- [ ] **Step 7: Run static checks for the new module**

Run: `ruff check src/general_manager/cache/run_context_lru.py tests/unit/test_run_context_lru.py`

Run: `ruff format --check src/general_manager/cache/run_context_lru.py tests/unit/test_run_context_lru.py`

Run: `mypy --strict src/general_manager/cache/run_context_lru.py`

Expected: all three commands succeed.

- [ ] **Step 8: Commit the coordinator**

```bash
git add src/general_manager/cache/run_context_lru.py tests/unit/test_run_context_lru.py
git commit -m "feat: coordinate process-wide run-cache eviction"
```

### Task 3: Integrate general run values

**Files:**
- Modify: `src/general_manager/cache/run_context.py:5-184,308-323,637-663`
- Modify: `tests/unit/test_calculation_run_context.py:1-203,384-455`

**Interfaces:**
- Consumes: `resolve_run_context_cache_max_bytes()` and singleton `run_context_cache_budget` from Tasks 1-2.
- Produces: private `CalculationRunContext` owner methods `_iter_run_cache_entries()`, `_evict_run_cache_entry()`, `_store_run_value()`, `_remove_run_value()`, and `_refresh_run_value()`.

- [ ] **Step 1: Write failing public integration tests**

Import `override_settings` and `estimate_cache_entry_size`. Add tests for disabled retention, oversized loader results, recency, global cross-context eviction, and invalid construction:

```python
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from general_manager.cache.run_context_lru import (
    estimate_cache_entry_size,
    run_context_cache_budget,
)


def test_zero_run_context_budget_disables_value_retention() -> None:
    calls = 0

    def loader() -> str:
        nonlocal calls
        calls += 1
        return "loaded"

    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        CalculationRunContext() as context,
    ):
        assert context.get_or_set("key", loader) == "loaded"
        assert context.get_or_set("key", loader) == "loaded"

    assert calls == 2


def test_run_context_budget_evicts_lru_value_across_contexts() -> None:
    entry_size = estimate_cache_entry_size("a", "A", stop_after=None)
    with override_settings(
        GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": entry_size * 2}
    ):
        with CalculationRunContext() as first, CalculationRunContext() as second:
            first.set("a", "A")
            second.set("b", "B")
            assert first.get("a") == "A"
            second.set("c", "C")

            assert first.get("a") == "A"
            assert second.get("b") is None
            assert second.get("c") == "C"


def test_run_context_does_not_retain_value_larger_than_budget() -> None:
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 256}),
        CalculationRunContext() as context,
    ):
        value = context.get_or_set("large", lambda: b"x" * 1024)

        assert value == b"x" * 1024
        assert context.get("large") is None


def test_outermost_run_context_exit_releases_process_accounting() -> None:
    with override_settings(
        GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 10_000}
    ):
        context = CalculationRunContext()
        with context:
            context.set("key", "value")
            assert run_context_cache_budget.estimated_bytes > 0

            with context:
                assert run_context_cache_budget.estimated_bytes > 0

            assert run_context_cache_budget.estimated_bytes > 0

        assert run_context_cache_budget.estimated_bytes == 0


@override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": True})
def test_run_context_rejects_invalid_memory_budget() -> None:
    with pytest.raises(ImproperlyConfigured, match="RUN_CONTEXT_CACHE_MAX_BYTES"):
        CalculationRunContext()


def test_run_context_budget_keeps_historical_namespaces_independent() -> None:
    with as_of("2022-01-01"):
        first_fingerprint = as_of_cache_fingerprint()
    with as_of("2022-01-02"):
        second_fingerprint = as_of_cache_fingerprint()
    assert first_fingerprint is not None
    assert second_fingerprint is not None
    first_size = estimate_cache_entry_size(
        ("as_of", first_fingerprint, "key"), "A", stop_after=None
    )
    second_size = estimate_cache_entry_size(
        ("as_of", second_fingerprint, "key"), "B", stop_after=None
    )

    with (
        override_settings(
            GENERAL_MANAGER={
                "RUN_CONTEXT_CACHE_MAX_BYTES": first_size + second_size
            }
        ),
        CalculationRunContext() as context,
    ):
        with as_of("2022-01-01"):
            context.set("key", "A")
        with as_of("2022-01-02"):
            context.set("key", "B")
        with as_of("2022-01-01"):
            assert context.get("key") == "A"
        with as_of("2022-01-03"):
            context.set("key", "C")

        with as_of("2022-01-01"):
            assert context.get("key") == "A"
        with as_of("2022-01-02"):
            assert context.get("key") is None
```

- [ ] **Step 2: Run integration tests and verify RED**

Run: `python -m pytest tests/unit/test_calculation_run_context.py -k 'budget or larger_than' -v`

Expected: zero-budget values are retained, oversized values remain cached, and invalid configuration is accepted.

- [ ] **Step 3: Register each context and implement owner callbacks**

After initializing all context dictionaries in `__init__()`, resolve the setting and call:

```python
run_context_cache_budget.register(
    self,
    resolve_run_context_cache_max_bytes(),
)
```

Implement `_iter_run_cache_entries()` to yield every `_values` item as `("values", key, value)` and every dependency hit whose key is not pending as `("dependency_hits", key, hit)`. Implement `_evict_run_cache_entry()` with direct `dict.pop(key, None)` and no coordinator callback. This callback must never remove `_dependency_cache_pending_publications`.

- [ ] **Step 4: Route `_values` through storage helpers**

Implement:

```python
def _store_run_value(self, scoped_key: Hashable, value: object) -> None:
    self._values[scoped_key] = value
    run_context_cache_budget.track(self, "values", scoped_key, value)

def _remove_run_value(self, scoped_key: Hashable) -> None:
    if scoped_key in self._values:
        del self._values[scoped_key]
        run_context_cache_budget.remove(self, "values", scoped_key)

def _refresh_run_value(self, scoped_key: Hashable) -> None:
    try:
        value = self._values[scoped_key]
    except KeyError:
        return
    run_context_cache_budget.refresh(self, "values", scoped_key, value)
```

Change `get_or_set()` to preserve its single mapping lookup on hits, call `touch()` before returning a hit, and call `_store_run_value()` after a successful loader. Change `get()`, `has()`, and `__contains__()` so successful reads touch recency without confusing a stored `None` with a miss. Change `set()` to scope once and call `_store_run_value()`.

Change `discard_prefix()` and all direct value clearing paths to call `_remove_run_value()`. On outermost `__exit__()`, call `run_context_cache_budget.clear_context(self)` in the existing cleanup `finally` before clearing dictionaries.

- [ ] **Step 5: Run focused integration tests and verify GREEN**

Run: `python -m pytest tests/unit/test_calculation_run_context.py -k 'budget or larger_than or public_storage or historical or discard_prefix or reentering' -v`

Expected: all selected tests pass, including the existing single-lookup and nesting regressions.

- [ ] **Step 6: Run the full run-context unit module**

Run: `python -m pytest tests/unit/test_calculation_run_context.py -v`

Expected: all tests pass.

- [ ] **Step 7: Commit general-value integration**

```bash
git add src/general_manager/cache/run_context.py tests/unit/test_calculation_run_context.py
git commit -m "feat: bound calculation run values by memory"
```

### Task 4: Integrate dependency hits and mutable ORM indexes

**Files:**
- Modify: `src/general_manager/cache/run_context.py:186-306,337-389`
- Modify: `tests/unit/test_calculation_run_context.py:204-383,456-560`

**Interfaces:**
- Consumes: the coordinator and owner callbacks integrated in Task 3.
- Produces: private hit helpers `_store_dependency_cache_hit()`, `_remove_dependency_cache_hit()`, `_refresh_dependency_cache_hit()`, plus correct pin/unpin and ORM index reweighing.

- [ ] **Step 1: Write failing dependency-hit eviction tests**

```python
def test_dependency_cache_hits_share_lru_recency() -> None:
    first = DependencyCacheHit(value="A", dependencies=frozenset())
    second = DependencyCacheHit(value="B", dependencies=frozenset())
    third = DependencyCacheHit(value="C", dependencies=frozenset())
    entry_size = estimate_cache_entry_size("cache-a", first, stop_after=None)
    with (
        override_settings(
            GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": entry_size * 2}
        ),
        CalculationRunContext() as context,
    ):
        context.set_dependency_cache_hits({"cache-a": first, "cache-b": second})
        assert context.get_dependency_cache_hit("cache-a") == first
        context.set_dependency_cache_hits({"cache-c": third})

        assert context.get_dependency_cache_hit("cache-a") == first
        assert context.get_dependency_cache_hit("cache-b") is None
        assert context.get_dependency_cache_hit("cache-c") == third


def test_pending_dependency_publication_hit_is_pinned_until_flush() -> None:
    entry = make_pending_publication("cache-a")
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        mock.patch(
            "general_manager.cache.dependency_publish.publish_dependency_cache_entries"
        ),
        mock.patch("general_manager.cache.dependency_publish.release_compute_lease"),
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(entry)
        assert context.get_dependency_cache_hit("cache-a") is not None

        context.flush_dependency_cache_publications()

        assert context.get_dependency_cache_hit("cache-a") is None


def test_pending_dependency_publication_hit_becomes_evictable_after_discard() -> None:
    entry = make_pending_publication("cache-a")
    with (
        override_settings(GENERAL_MANAGER={"RUN_CONTEXT_CACHE_MAX_BYTES": 0}),
        mock.patch(
            "general_manager.cache.dependency_publish.release_compute_lease"
        ) as release_lease,
        CalculationRunContext() as context,
    ):
        context.buffer_dependency_cache_publication(entry)
        assert context.get_dependency_cache_hit("cache-a") is not None

        context.discard_dependency_cache_publications()

        assert context.get_dependency_cache_hit("cache-a") is None
        release_lease.assert_called_once_with(entry.lease)
```

- [ ] **Step 2: Run dependency tests and verify RED**

Run: `python -m pytest tests/unit/test_calculation_run_context.py -k 'dependency_cache and (budget or pinned)' -v`

Expected: prefetched hits do not evict and flushed/discarded hits remain under a zero budget.

- [ ] **Step 3: Route dependency hits through accounting helpers**

Implement the hit helpers explicitly:

```python
def _store_dependency_cache_hit(
    self, key: str, hit: DependencyCacheHit
) -> None:
    self._dependency_cache_hits[key] = hit
    if key in self._dependency_cache_pending_publications:
        run_context_cache_budget.remove(self, "dependency_hits", key)
    else:
        run_context_cache_budget.track(self, "dependency_hits", key, hit)

def _remove_dependency_cache_hit(self, key: str) -> None:
    if key in self._dependency_cache_hits:
        del self._dependency_cache_hits[key]
        run_context_cache_budget.remove(self, "dependency_hits", key)

def _refresh_dependency_cache_hit(self, key: str) -> None:
    if key in self._dependency_cache_pending_publications:
        return
    try:
        hit = self._dependency_cache_hits[key]
    except KeyError:
        return
    run_context_cache_budget.refresh(self, "dependency_hits", key, hit)
```

`get_dependency_cache_hit()` touches successful non-pending hits. `set_dependency_cache_hits()` stores each hit individually rather than using `dict.update()`.

In `buffer_dependency_cache_publication()`, remove existing hit accounting before adding the pending entry and replacement hit. In both `flush_dependency_cache_publications()` and `discard_dependency_cache_publications()`, use an outer `finally` that calls `_refresh_dependency_cache_hit()` for every key removed from the pending map, even if publishing or lease release raises. Under a zero budget this immediately evicts the now-unpinned hits. `discard_dependency_cache_state()` and outermost context cleanup remove coordinator accounting before clearing hits.

- [ ] **Step 4: Run dependency tests and verify GREEN**

Run: `python -m pytest tests/unit/test_calculation_run_context.py -k dependency_cache -v`

Expected: new eviction/pinning tests and all existing publication lifecycle tests pass.

- [ ] **Step 5: Write a failing mutable-index accounting test**

```python
def test_run_context_reweighs_mutated_orm_index() -> None:
    class Row:
        _meta = SimpleNamespace(concrete_model=None)

        def __init__(self) -> None:
            self.pk = 7
            self._state = SimpleNamespace(db="default")
            self.payload = b"x" * 4096

    Row._meta = SimpleNamespace(concrete_model=Row)
    row = Row()
    empty_index_size = estimate_cache_entry_size(
        ("orm_model_row_index", Row), {}, stop_after=None
    )

    with (
        override_settings(
            GENERAL_MANAGER={
                "RUN_CONTEXT_CACHE_MAX_BYTES": empty_index_size
            }
        ),
        CalculationRunContext() as context,
    ):
        context.set_orm_bucket_rows(("query", "rows"), (row,))

        assert context.get_orm_model_row(Row, 7, "default") is None
```

- [ ] **Step 6: Run the mutable-index test and verify RED**

Run: `python -m pytest tests/unit/test_calculation_run_context.py -k reweighs_mutated_orm_index -v`

Expected: the grown in-place dictionary remains cached because its original empty weight was never refreshed.

- [ ] **Step 7: Reweigh internally mutated model indexes**

In `_index_orm_model_rows()`, compute `scoped_key = self._scoped_key(cache_key)` and collect each scoped model-index key that remains in `_values` after mutation. After all row mutations, call `_refresh_run_value(scoped_key)` once for each collected key. Avoid refreshing a dictionary that was rejected or evicted when first created. Keep arbitrary caller-owned mutable values insertion-time-only as documented.

- [ ] **Step 8: Run run-context and ORM-adjacent regression tests**

Run: `python -m pytest tests/unit/test_calculation_run_context.py tests/unit/test_database_bucket.py tests/unit/test_orm_capabilities_comprehensive.py -q`

Expected: all selected modules pass.

- [ ] **Step 9: Commit hit pinning and mutation accounting**

```bash
git add src/general_manager/cache/run_context.py tests/unit/test_calculation_run_context.py
git commit -m "feat: account for run-cache hits and mutable indexes"
```

### Task 5: User documentation and final verification

**Files:**
- Modify: `docs/api/cache.md:476-520`
- Modify: `docs/concepts/caching.md:180-213`

**Interfaces:**
- Consumes: the implemented `RUN_CONTEXT_CACHE_MAX_BYTES` behavior.
- Produces: documented configuration and operational expectations; no new code interface.

- [ ] **Step 1: Update the API reference**

Add a subsection near the `CalculationRunContext` reference containing the exact setting example:

```python
GENERAL_MANAGER = {
    "RUN_CONTEXT_CACHE_MAX_BYTES": 256 * 1024 * 1024,
}
```

State that the setting is optional, process-local, shared across concurrent live contexts, estimated at insertion time, and LRU-based. Document `None`, `0`, positive integers, invalid values, oversized-value bypass, and pinned pending publications. Explicitly say it does not cap total RSS.

- [ ] **Step 2: Update the caching concept guide**

After the run-context lifecycle discussion, add production guidance that multiplies the configured value by worker count when estimating fleet memory. Explain that mutable caller-owned values can grow beyond their insertion estimate and that applications needing a hard worker ceiling must also use deployment-level memory limits/recycling.

- [ ] **Step 3: Check documentation and formatting**

Run: `git diff --check`

Run: `pre-commit run --all-files`

Expected: all hooks pass. If a hook reformats files, inspect and stage only the files in this plan.

- [ ] **Step 4: Run focused tests and static checks**

Run: `python -m pytest tests/unit/test_run_context_lru.py tests/unit/test_calculation_run_context.py -q`

Run: `ruff check src/general_manager/cache/run_context_lru.py src/general_manager/cache/run_context.py tests/unit/test_run_context_lru.py tests/unit/test_calculation_run_context.py`

Run: `ruff format --check src/general_manager/cache/run_context_lru.py src/general_manager/cache/run_context.py tests/unit/test_run_context_lru.py tests/unit/test_calculation_run_context.py`

Run: `mypy --strict src/general_manager/cache/run_context_lru.py src/general_manager/cache/run_context.py`

Expected: every command succeeds without warnings or errors.

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest`

Expected: the full suite passes.

- [ ] **Step 6: Review the final diff against the design**

Run: `git status --short`

Run: `git diff --stat ea03c1ad`

Run: `git diff ea03c1ad -- src/general_manager/cache/run_context_lru.py src/general_manager/cache/run_context.py tests/unit/test_run_context_lru.py tests/unit/test_calculation_run_context.py docs/api/cache.md docs/concepts/caching.md`

Confirm that only the planned implementation, tests, and documentation changed; no public exports, dependencies, version numbers, or unrelated files were modified.

- [ ] **Step 7: Commit documentation**

```bash
git add docs/api/cache.md docs/concepts/caching.md
git commit -m "docs: explain run-context memory budget"
```
