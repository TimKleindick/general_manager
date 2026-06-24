"""Dependency index management for cached GeneralManager query results."""

from __future__ import annotations

import json
import random
import time
from contextvars import ContextVar
from datetime import date, datetime
from typing import TYPE_CHECKING, Callable, Iterable, Literal, Tuple, Type, cast

from django.core.cache import cache
from django.dispatch import receiver

from general_manager.cache.dependency_matching import (
    current_value_for_path as resolve_current_value_for_path,
    lookup_spec_from_key,
    matches_lookup_value,
    normalize_dependency_value,
    parse_dependency_identifier as _parse_dependency_identifier,
    serialize_normalized_value,
)
from general_manager.cache.dependency_shards import (
    ReverseDependencyMembership,
    all_records_cache_keys,
    candidate_cache_keys_for_lookup,
    record_cache_dependencies,
    record_many_cache_dependencies,
    remove_cache_key_from_shards,
    request_query_cache_keys,
    reverse_membership_key,
    reverse_memberships,
    tracked_lookup_names,
)
from general_manager.cache.signals import post_data_change, pre_data_change
from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.manager.general_manager import GeneralManager

type general_manager_name = str  # e.g. "Project", "Derivative", "User"
type attribute = str  # e.g. "field", "name", "id"
type lookup = str  # e.g. "field__gt", "field__in", "field__contains", "field"
type cache_keys = set[str]  # e.g. "cache_key_1", "cache_key_2"
type identifier = str  # e.g. "{'id': 1}"", "{'project': Project(**{'id': 1})}", ...
type dependency_index = dict[
    Literal["filter", "exclude", "request_query", "all"],
    dict[
        general_manager_name,
        dict[attribute, dict[lookup, cache_keys]] | dict[identifier, cache_keys],
    ],
]
type lookup_dependency_map = dict[lookup, cache_keys]
type manager_dependency_section = dict[attribute, lookup_dependency_map]
type request_query_manager_section = dict[identifier, cache_keys]

type filter_type = Literal[
    "filter", "exclude", "identification", "request_query", "all"
]
type Dependency = Tuple[general_manager_name, filter_type, str]

logger = get_logger("cache.dependency_index")
_pending_graphql_rewarm_cache_keys: ContextVar[frozenset[str]] = ContextVar(
    "general_manager_pending_graphql_rewarm_cache_keys",
    default=frozenset(),
)


class DependencyLockTimeoutError(TimeoutError):
    """Raised when the dependency index lock cannot be acquired within the timeout."""

    def __init__(self, operation: str) -> None:
        """
        Error raised when acquiring the dependency index lock times out.

        Parameters:
            operation (str): Name or description of the operation during which lock acquisition timed out.
        """
        super().__init__(
            f"Timed out acquiring dependency index lock during {operation}."
        )


# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
INDEX_KEY = "dependency_index"  # Cache key storing the complete dependency index
LOCK_KEY = "dependency_index_lock"  # Cache key used for the dependency lock
DEPENDENCY_GENERATION_KEY = "dependency_index_generation"
DATA_CHANGE_LOCK_KEY = "dependency_index_data_change_lock"
DATA_CHANGE_COUNT_KEY = "dependency_index_data_change_count"
LOCK_TIMEOUT = 5  # Lock TTL in seconds
UNDEFINED = object()  # Sentinel for undefined values
ACTIONS: tuple[Literal["filter"], Literal["exclude"]] = ("filter", "exclude")
REQUEST_QUERY_ACTION: Literal["request_query"] = "request_query"
ALL_RECORDS_LOOKUP = "__all__"
ALL_RECORDS_VALUE = "__all__"


# -----------------------------------------------------------------------------
# LOCKING HELPERS
# -----------------------------------------------------------------------------
_BACKOFF_INITIAL = 0.02  # 20ms initial sleep
_BACKOFF_MAX = 0.5  # 500ms maximum sleep between retries


def acquire_lock(timeout: int = LOCK_TIMEOUT) -> bool:
    """
    Attempt to acquire the cache-backed lock guarding dependency writes.

    Parameters:
        timeout (int): Expiration time for the lock entry in seconds.

    Returns:
        bool: True if the lock was acquired; otherwise, False.
    """
    return cache.add(LOCK_KEY, "1", timeout)


def release_lock() -> None:
    """
    Release the cache-backed lock guarding dependency writes.

    Returns:
        None
    """
    cache.delete(LOCK_KEY)


def get_dependency_generation() -> int:
    """Return the current dependency-cache mutation generation."""
    generation = cache.get(DEPENDENCY_GENERATION_KEY, 0)
    return int(generation or 0)


def _set_dependency_generation(generation: int) -> int:
    cache.set(DEPENDENCY_GENERATION_KEY, generation, None)
    return generation


def _get_dependency_data_change_count() -> int:
    count = cache.get(DATA_CHANGE_COUNT_KEY, 0)
    return max(int(count or 0), 0)


def _set_dependency_data_change_count(count: int) -> int:
    cache.set(DATA_CHANGE_COUNT_KEY, count, None)
    return count


def _discard_active_context_dependency_cache_state() -> None:
    from general_manager.cache.run_context import current_calculation_run_context

    context = current_calculation_run_context()
    if context is not None:
        context.discard_dependency_cache_state()


def begin_dependency_data_change() -> int:
    """
    Mark a data change as active and bump the dependency generation.

    The generation bump happens before the underlying mutation, so computations
    that started before the mutation cannot publish dependency-scoped values.
    """
    acquire_lock_with_retry("begin_dependency_data_change")
    try:
        generation = _set_dependency_generation(get_dependency_generation() + 1)
        _set_dependency_data_change_count(_get_dependency_data_change_count() + 1)
        cache.set(DATA_CHANGE_LOCK_KEY, "1", None)
    finally:
        release_lock()
    try:
        _discard_active_context_dependency_cache_state()
    except Exception:
        try:
            end_dependency_data_change()
        except Exception:
            logger.exception("dependency data-change cleanup rollback failed")
        raise
    return generation


def end_dependency_data_change() -> None:
    """Release the publish barrier for a completed data change."""
    acquire_lock_with_retry("end_dependency_data_change")
    try:
        count = _set_dependency_data_change_count(
            max(_get_dependency_data_change_count() - 1, 0)
        )
        if count == 0:
            cache.delete(DATA_CHANGE_LOCK_KEY)
        else:
            cache.set(DATA_CHANGE_LOCK_KEY, "1", None)
    finally:
        release_lock()


def is_dependency_data_change_active() -> bool:
    """Return whether dependency-scoped cache publishing should pause."""
    return cache.get(DATA_CHANGE_LOCK_KEY) is not None


def record_invalidated_cache_keys_for_graphql_rewarm(
    cache_keys: Iterable[str],
) -> None:
    """Remember invalidated cache keys until the current data-change barrier ends."""
    existing = _pending_graphql_rewarm_cache_keys.get()
    _pending_graphql_rewarm_cache_keys.set(
        existing | frozenset(dict.fromkeys(cache_keys))
    )


def drain_invalidated_cache_keys_for_graphql_rewarm() -> tuple[str, ...]:
    """Return and clear invalidated cache keys pending GraphQL recipe re-warm."""
    cache_keys = tuple(sorted(_pending_graphql_rewarm_cache_keys.get()))
    _pending_graphql_rewarm_cache_keys.set(frozenset())
    return cache_keys


def acquire_lock_with_retry(operation: str) -> None:
    """
    Acquire the dependency index lock, retrying with exponential backoff.

    Parameters:
        operation (str): Name of the operation, used in the timeout error message.

    Raises:
        DependencyLockTimeoutError: If the lock cannot be acquired within LOCK_TIMEOUT.
    """
    if acquire_lock():
        return
    start = time.time()
    delay = _BACKOFF_INITIAL
    while True:
        remaining = LOCK_TIMEOUT - (time.time() - start)
        if remaining <= 0:
            raise DependencyLockTimeoutError(operation)
        time.sleep(random.uniform(0, min(delay, remaining)))  # noqa: S311 - jitter, not crypto
        if acquire_lock():
            return
        if time.time() - start > LOCK_TIMEOUT:
            raise DependencyLockTimeoutError(operation)
        delay = min(delay * 2, _BACKOFF_MAX)


# -----------------------------------------------------------------------------
# INDEX ACCESS
# -----------------------------------------------------------------------------
def get_full_index() -> dependency_index:
    """
    Fetch the dependency index from cache, initialising it on first access.

    Returns:
        dependency_index: Mapping of tracked `filter`, `exclude`, `all`, and
        `request_query` dependencies keyed by manager name.
    """
    cached_index = cache.get(INDEX_KEY, None)
    if cached_index is None:
        idx: dependency_index = {
            "filter": {},
            "exclude": {},
            "request_query": {},
            "all": {},
        }
        for reverse in reverse_memberships():
            dependencies: set[Dependency] = set(reverse.simple_dependencies)
            dependencies.update(reverse.composite_dependencies)
            _record_dependencies_locked(idx, reverse.cache_key, dependencies)
        return idx
    idx = cast(dependency_index, cached_index)
    changed = False
    for key in cast(
        tuple[Literal["filter", "exclude", "request_query", "all"], ...],
        ("filter", "exclude", "request_query", "all"),
    ):
        if key not in idx:
            idx[key] = {}
            changed = True
    if changed:
        cache.set(INDEX_KEY, idx, None)
    return idx


def set_full_index(idx: dependency_index) -> None:
    """
    Persist the dependency index to cache.

    Parameters:
        idx (dependency_index): Updated index that should replace the cached value.

    Returns:
        None
    """
    cache.set(INDEX_KEY, idx, None)


def _normalize_dependency_identifier(value: object) -> object:
    """Return a JSON-serializable representation for dependency tracking."""
    return normalize_dependency_value(value)


def serialize_dependency_identifier(value: object) -> str:
    """
    Serialize dependency payloads into the canonical dependency identifier format.

    Parameters:
        value: Dependency payload captured from manager identification, filter
            parameters, exclusion parameters, or request-query metadata.

    Returns:
        A deterministic JSON string. Normalization checks value categories in
        this order: mappings, lists/tuples, sets, datetimes, dates, JSON scalar
        values, mapping-shaped `__getstate__()`, then `repr(...)` fallback.
        Mapping keys are coerced with Python's exact `str(key)` result and
        ordered by that string form; sets are sorted by each member's string
        form; lists and tuples keep their order; dates and datetimes serialize
        with `isoformat()` including any timezone offset; scalars and `None`
        keep their JSON meaning; mapping-shaped `__getstate__()` payloads are
        stored under `{"__state__": ...}`; unsupported objects fall back to
        `{"__repr__": repr(value)}`. The parser returns these normalized
        JSON-compatible structures and does not rehydrate dates, datetimes, or
        unsupported objects. The current string format is the direct
        `json.dumps(..., sort_keys=True)` output of that normalized structure.
        Dependency payloads should avoid mapping keys or set members that
        collide after `str(...)`. If mapping keys normalize to the same string,
        normalization keeps the last item after sorting by `str(key)`; equal sort
        keys keep the input mapping's iteration order. Set members with
        identical string forms have intentionally unspecified ordering, so their
        byte-for-byte serialized output is not a public guarantee. Non-finite
        floats follow Python's default `json.dumps()` spelling (`NaN`,
        `Infinity`, or `-Infinity`), which is accepted by Python's parser but is
        not portable strict JSON; dependency identifiers containing these values
        are Python-cache metadata rather than strict JSON interchange values. If
        `__getstate__()` exists but returns a non-mapping, the value uses the
        same `repr(...)` fallback as other unsupported objects.
    """
    return serialize_normalized_value(value)


def parse_dependency_identifier(identifier: str) -> object | None:
    """
    Parse a serialized dependency identifier back into JSON-compatible data.

    Parameters:
        identifier: JSON string previously produced by
            `serialize_dependency_identifier()`.

    Returns:
        The decoded JSON-compatible value, such as a mapping, sequence, scalar,
        or `None` when `identifier` is malformed JSON. A valid serialized JSON
        `null` payload also decodes to `None`.
    """
    return _parse_dependency_identifier(identifier)


# -----------------------------------------------------------------------------
# DEPENDENCY RECORDING
# -----------------------------------------------------------------------------
def _record_dependencies_locked(
    idx: dependency_index,
    cache_key: str,
    dependencies: Iterable[Dependency],
) -> None:
    """Mutate an already-loaded dependency index for one cache key."""
    for model_name, action, identifier in set(dependencies):
        if action in ("filter", "exclude"):
            action_key = cast(Literal["filter", "exclude"], action)
            params = parse_dependency_identifier(identifier)
            if not isinstance(params, dict):
                continue
            action_section = cast(
                dict[general_manager_name, manager_dependency_section],
                idx[action_key],
            )
            section = action_section.setdefault(model_name, {})
            if not params:
                lookup_map = section.setdefault(ALL_RECORDS_LOOKUP, {})
                lookup_map.setdefault(ALL_RECORDS_VALUE, set()).add(cache_key)
                continue
            if len(params) > 1:
                cache_dependencies = section.setdefault("__cache_dependencies__", {})
                cache_dependencies.setdefault(cache_key, set()).add(identifier)
            for lookup, val in params.items():
                lookup_map = section.setdefault(lookup, {})
                val_key = json.dumps(
                    _normalize_dependency_identifier(val), sort_keys=True
                )
                lookup_map.setdefault(val_key, set()).add(cache_key)

        elif action == "request_query":
            request_index = cast(
                dict[str, dict[str, set[str]]],
                idx.setdefault("request_query", {}),
            )
            request_section = request_index.setdefault(model_name, {})
            request_section.setdefault(identifier, set()).add(cache_key)

        elif action == "all":
            all_index = cast(
                dict[str, set[str]],
                idx.setdefault("all", {}),
            )
            all_index.setdefault(model_name, set()).add(cache_key)

        else:
            filter_section = cast(
                dict[general_manager_name, manager_dependency_section],
                idx["filter"],
            )
            section = filter_section.setdefault(model_name, {})
            lookup_map = section.setdefault("identification", {})
            lookup_map.setdefault(identifier, set()).add(cache_key)


def record_dependencies(
    cache_key: str,
    dependencies: Iterable[Dependency],
) -> None:
    """
    Register dependency-index metadata for one cache key.

    Each dependency tuple is `(manager_name, action, identifier)`, where
    `manager_name` is the GeneralManager class name that owns the tracked read,
    `action` is one of `"filter"`, `"exclude"`, `"identification"`,
    `"request_query"`, or `"all"`, and `identifier` is the serialized payload
    for that action. Repeated calls for the same cache key replace that key's
    previous sharded dependency metadata; duplicate tuples in one call collapse.
    An empty dependency iterable is a no-op and does not clear existing metadata
    for `cache_key`; call `remove_cache_key_from_index()` for explicit cleanup.
    A non-empty iterable whose dependencies all normalize to no shards still
    replaces prior metadata with an empty reverse entry.

    Parameters:
        cache_key: Cache key to associate with the declared dependencies.
        dependencies: Dependency tuples to store. For `"filter"` and
            `"exclude"`, `identifier` must be a serialized mapping of lookup
            names to expected values; malformed or non-mapping identifiers are
            ignored rather than stored. `{}` tracks all records for the manager.
            For `"identification"`, `identifier` is the serialized manager
            identification payload. For `"request_query"` and `"all"`, the
            identifier is stored as reverse metadata only; `"all"` invalidation
            is keyed by the manager name and does not inspect the identifier, so
            all `"all"` dependencies for the changed manager invalidate together.
            Multiple `"all"` tuples with different identifiers for the same
            cache key are retained as separate reverse metadata entries until a
            later non-empty `record_dependencies()` call replaces that key's
            metadata; the retained identifiers preserve the original dependency
            tuple and have no invalidation meaning.

    Invalidation:
        `"request_query"` dependencies invalidate on any change for the same
        manager name. `"identification"` dependencies invalidate when the changed
        manager's current `identification` serializes exactly to the stored
        identifier. `"all"` dependencies invalidate on any change for the same
        manager name.

    Notes:
        Filter and exclude invalidation compare the serialized expected value
        with the changed manager's before/after attribute values. Equality and
        membership dependencies coerce JSON scalars, ISO dates/datetimes,
        booleans, `{"__state__": ...}` mappings, and `{"__repr__": ...}`
        markers back toward the runtime value being compared. Range operators
        use the runtime value's ordering after coercion. String lookup operators
        (`contains`, `startswith`, `endswith`, and `regex`) compare against
        `str(runtime_value)`. Supported lookup suffixes are `gt`, `gte`, `lt`,
        `lte`, `in`, `contains`, `startswith`, `endswith`, and `regex`; any
        other suffix is treated as part of the nested attribute path and uses
        equality matching. There is no escaping syntax for a final attribute
        segment literally named like a supported suffix; `field__in` is always
        the `in` lookup, not equality on `field.in`. Missing attributes resolve
        to `None`. Filter dependencies invalidate when either the old or new
        value matches because the changed object may enter or leave the cached
        result. Exclude dependencies invalidate when match status changes because
        that changes whether the object is excluded from the cached result. ISO
        strings are not self-describing: an ISO-looking stored string compares as
        a date/datetime when the runtime value is a date/datetime, and as a
        string when the runtime value is a string. Date runtime values only
        accept strings parsed by `date.fromisoformat()`; datetime-shaped strings
        do not match date runtime values. Date values do not match datetime
        runtime values. Datetime strings are parsed with
        `datetime.fromisoformat()` after replacing a trailing `Z` with `+00:00`
        and replacing the first space separator with `T`;
        timezone-aware parsed values have timezone information removed when the
        runtime value is naive, and naive parsed values receive the runtime
        value's timezone when it is aware. Boolean runtime values accept
        booleans, any integer via Python truthiness, and the strings `true`,
        `1`, `yes`, `y`, `t`, `false`, `0`, `no`, `n`, and `f`
        case-insensitively after trimming whitespace. Other runtime values
        attempt `type(runtime_value)(stored_value)` coercion, so numeric strings
        and non-finite floats follow that runtime type's constructor behavior.
        `{"__state__": ...}` mappings are compared by constructing the runtime
        value's type from keyword state, with a positional `(magnitude, unit)`
        fallback; constructor failures do not match. `{"__repr__": ...}` markers
        compare only with `repr(runtime_value)`. Lookup paths are `__`-separated
        attribute names resolved with `getattr()`; there is no escaping syntax,
        dict/list traversal, or callable invocation beyond normal property
        access. `contains` means `stored_pattern in str(runtime_value)`, `in`
        means the runtime value matches at least one item in the stored list,
        and `regex` uses `re.search(stored_pattern, str(runtime_value))` without
        flags; invalid regex patterns do not match. `startswith` and `endswith`
        call `str(runtime_value).startswith(stored_pattern)` and
        `str(runtime_value).endswith(stored_pattern)`. For string operators,
        non-string expected values are coerced with `str(expected_value)` after
        JSON parsing. For `in`, a stored expected value that is not a JSON list
        is a non-match. For range operators, failed coercion is a non-match;
        ordering exceptions from the runtime value propagate. Only documented
        parse, constructor, and regex compilation failures are converted to
        non-matches. Exceptions raised by attribute properties,
        `str(runtime_value)`, `repr(runtime_value)`, or comparison operators
        propagate to the invalidation caller.
        An empty filter or exclude mapping (`{}`) is not evaluated as a normal
        composite predicate; it records an all-records dependency for that
        manager and invalidates on any change for that manager. Multi-lookup
        identifiers are composite dependencies within their single
        action: a `"filter"` identifier matches only when every lookup in that
        identifier matches, and an `"exclude"` identifier also computes match
        status by requiring every lookup in that identifier to match. The action
        then controls invalidation: filters invalidate when old or new composite
        status is true, while excludes invalidate when old and new composite
        status differ. Malformed expected values inside an otherwise valid
        mapping are treated as non-matches for that lookup, not as stored
        dependency errors. Constructor coercion calls the runtime value's type;
        constructors with side effects should not be used for dependency values.

    Raises:
        DependencyLockTimeoutError: If a lock cannot be acquired within the configured timeout while updating the index.
    """
    acquire_lock_with_retry("record_dependencies")
    try:
        record_cache_dependencies(cache_key, dependencies)
    finally:
        release_lock()


def record_many_dependencies(
    entries: Iterable[tuple[str, Iterable[Dependency]]],
) -> None:
    """
    Register dependency metadata for many cache keys while holding one index lock.
    """
    normalized: dict[str, set[Dependency]] = {}
    for cache_key, dependencies in entries:
        dep_set = set(dependencies)
        if dep_set:
            normalized.setdefault(cache_key, set()).update(dep_set)
    if not normalized:
        return

    acquire_lock_with_retry("record_many_dependencies")
    try:
        record_many_cache_dependencies(normalized.items())
    finally:
        release_lock()


# -----------------------------------------------------------------------------
# INDEX CLEANUP
# -----------------------------------------------------------------------------
def remove_cache_key_from_index(cache_key: str) -> None:
    """
    Remove a cache key from dependency-index metadata without deleting the value.

    Acquires the dependency lock to update and persist the index or sharded
    index metadata. This is index-only cleanup; use `invalidate_cache_key()` or
    `invalidate_and_remove_cache_keys()` when the cached value should also be
    deleted.

    Parameters:
        cache_key: Cache key to expunge from all recorded dependency mappings.

    Raises:
        DependencyLockTimeoutError: If the dependency lock cannot be acquired within LOCK_TIMEOUT.
    """
    acquire_lock_with_retry("remove_cache_key_from_index")
    try:
        if cache.get(INDEX_KEY, None) is not None and not reverse_memberships():
            idx = get_full_index()
            _remove_cache_keys_from_index_locked(idx, (cache_key,))
            set_full_index(idx)
            return
        remove_cache_key_from_shards(cache_key)
    finally:
        release_lock()


# -----------------------------------------------------------------------------
# CACHE INVALIDATION
# -----------------------------------------------------------------------------
def invalidate_cache_key(cache_key: str) -> None:
    """
    Delete the cached value associated with the provided key.

    This function only calls the configured cache backend's `delete()` for the
    value key. It does not remove dependency-index metadata; call
    `remove_cache_key_from_index()` separately, or use
    `invalidate_and_remove_cache_keys()`, when both steps are required.

    Parameters:
        cache_key: Key referencing the cached value.

    Returns:
        None
    """
    cache.delete(cache_key)


def _remove_cache_keys_from_index_locked(
    idx: dependency_index,
    cache_keys: tuple[str, ...],
) -> None:
    """Remove cache keys from all dependency-index sections while the lock is held."""
    all_section = cast(dict[str, set[str]], idx.get("all", {}))
    for mname, key_set in list(all_section.items()):
        for cache_key in cache_keys:
            if cache_key in key_set:
                key_set.remove(cache_key)
        if not key_set:
            del all_section[mname]
    for action in ACTIONS:
        action_section = cast(
            dict[general_manager_name, manager_dependency_section],
            idx[action],
        )
        for mname, model_section in list(action_section.items()):
            cache_dependencies = model_section.get("__cache_dependencies__", {})
            for lookup, lookup_map in list(model_section.items()):
                if lookup == "__cache_dependencies__":
                    continue
                for val_key, key_set in list(lookup_map.items()):
                    for cache_key in cache_keys:
                        if cache_key in key_set:
                            key_set.remove(cache_key)
                    if not key_set:
                        del lookup_map[val_key]
                if not lookup_map:
                    del model_section[lookup]
            if cache_dependencies:
                for cache_key in cache_keys:
                    cache_dependencies.pop(cache_key, None)
                if not cache_dependencies:
                    model_section.pop("__cache_dependencies__", None)
            if not model_section:
                del action_section[mname]
    request_query_section = cast(
        dict[str, dict[str, set[str]]],
        idx.get("request_query", {}),
    )
    for mname, query_section in list(request_query_section.items()):
        for identifier, key_set in list(query_section.items()):
            for cache_key in cache_keys:
                if cache_key in key_set:
                    key_set.remove(cache_key)
            if not key_set:
                del query_section[identifier]
        if not query_section:
            del request_query_section[mname]


def invalidate_and_remove_cache_keys(cache_keys: Iterable[str]) -> None:
    """
    Delete cache keys and remove their dependency-index entries under one lock.

    Parameters:
        cache_keys (Iterable[str]): Cache keys to invalidate and remove.
    """
    keys = tuple(dict.fromkeys(cache_keys))
    if not keys:
        return
    acquire_lock_with_retry("invalidate_and_remove_cache_keys")
    try:
        if cache.get(INDEX_KEY, None) is not None and not reverse_memberships():
            idx = get_full_index()
            for cache_key in keys:
                cache.delete(cache_key)
            _remove_cache_keys_from_index_locked(idx, keys)
            set_full_index(idx)
            return
        for cache_key in keys:
            cache.delete(cache_key)
            remove_cache_key_from_shards(cache_key)
    finally:
        release_lock()


def _invalidate_request_query_dependencies_locked(
    idx: dependency_index,
    manager_name: str,
) -> tuple[str, ...]:
    request_queries = cast(
        request_query_manager_section,
        idx.get("request_query", {}).get(manager_name, {}),
    )
    cache_keys = tuple(
        dict.fromkeys(
            cache_key for key_set in request_queries.values() for cache_key in key_set
        )
    )
    for cache_key in cache_keys:
        cache.delete(cache_key)
    _remove_cache_keys_from_index_locked(idx, cache_keys)
    return cache_keys


def invalidate_request_query_dependencies(manager_name: str) -> tuple[str, ...]:
    """
    Invalidate all request-query cache keys tracked for a manager atomically.

    Returns:
        tuple[str, ...]: The cache keys that were invalidated.
    """
    acquire_lock_with_retry("invalidate_request_query_dependencies")
    try:
        if cache.get(INDEX_KEY, None) is not None and not reverse_memberships():
            idx = get_full_index()
            invalidated_keys = _invalidate_request_query_dependencies_locked(
                idx,
                manager_name,
            )
            if invalidated_keys:
                set_full_index(idx)
            return invalidated_keys
        invalidated_keys = tuple(dict.fromkeys(request_query_cache_keys(manager_name)))
        for cache_key in invalidated_keys:
            cache.delete(cache_key)
            remove_cache_key_from_shards(cache_key)
        return invalidated_keys
    finally:
        release_lock()


@receiver(pre_data_change)
def capture_old_values(
    sender: Type[GeneralManager],
    instance: GeneralManager | None,
    **kwargs: object,
) -> None:
    """
    Record the current values of fields referenced by tracked filters on the given manager instance before it changes.

    Parameters:
        instance (GeneralManager | None): Manager instance about to change; if provided, this function sets instance._old_values to a mapping of lookup keys to their current values for use by post-change invalidation logic.
    """
    if instance is None:
        return
    manager_name = sender.__name__
    lookups = tracked_lookup_names(manager_name)
    if not lookups and not reverse_memberships():
        idx = get_full_index()
        for action in ACTIONS:
            model_section = idx[action].get(manager_name)
            if isinstance(model_section, dict):
                for lookup in model_section.keys():
                    if not isinstance(lookup, str):
                        continue
                    if lookup.startswith("__sort__"):
                        lookups.add(lookup.removeprefix("__sort__"))
                        continue
                    if lookup.startswith("__"):
                        continue
                    lookups.add(lookup)
            elif isinstance(model_section, list):
                lookups |= set(model_section)
    if lookups and instance.identification:
        # save old values for later comparison
        vals: dict[str, object] = {}
        for lookup in lookups:
            attr_path = lookup.split("__")
            current: object = instance
            for i, attr in enumerate(attr_path):
                if getattr(current, attr, UNDEFINED) is UNDEFINED:
                    lookup = "__".join(attr_path[:i])
                    break
                current = getattr(current, attr, None)
            vals[lookup] = current
        instance._old_values = vals


def _generic_cache_invalidation_locked(
    idx: dependency_index,
    manager_name: str,
    instance: GeneralManager,
    old_relevant_values: dict[str, object],
) -> set[str]:
    """
    Invalidate cache entries affected by a change while the dependency lock is held.

    Uses the dependency index to compare previously captured values against the instance's current values for tracked lookups, evaluates both simple and composite dependency conditions for "filter" and "exclude" actions, and for any dependency that warrants invalidation it deletes the corresponding cache entry and removes its references from the index.

    Parameters:
        idx (dependency_index): Dependency index loaded by the public receiver.
        manager_name (str): Name of the manager class that emitted the signal.
        instance (GeneralManager): The manager instance that was changed.
        old_relevant_values (dict[str, object]): Mapping of lookup paths (joined by "__") to their values as captured before the change; used to compare old vs. new values for invalidation decisions.
    """
    invalidated_request_query_keys = _invalidate_request_query_dependencies_locked(
        idx, manager_name
    )
    invalidated_cache_keys = set(invalidated_request_query_keys)
    for cache_key in invalidated_request_query_keys:
        logger.info(
            "invalidating request query cache key",
            context={
                "manager": manager_name,
                "key": cache_key,
                "action": REQUEST_QUERY_ACTION,
            },
        )
    all_cache_keys = tuple(
        cast(dict[str, set[str]], idx.get("all", {})).get(manager_name, set())
    )
    for cache_key in all_cache_keys:
        logger.info(
            "invalidating cache key",
            context={
                "manager": manager_name,
                "key": cache_key,
                "action": "all",
            },
        )
        cache.delete(cache_key)
        _remove_cache_keys_from_index_locked(idx, (cache_key,))
        invalidated_cache_keys.add(cache_key)

    def _json_loads_val_key(val_key: object) -> object:
        if isinstance(val_key, str):
            try:
                return json.loads(val_key)
            except (json.JSONDecodeError, ValueError):
                return val_key  # treat as opaque string
        return val_key

    def _repr_marker(raw: object) -> str | None:
        if isinstance(raw, dict) and set(raw.keys()) == {"__repr__"}:
            marker = raw.get("__repr__")
            return marker if isinstance(marker, str) else None
        return None

    def _coerce_to_type(sample: object, raw: object) -> object | None:
        """
        Coerces a raw value to match the type and semantics of a sample value.

        Attempts to convert `raw` into the same type as `sample`. Handles:
        - datetimes: parses ISO-like strings, preserves or aligns timezone info with `sample`,
        - dates: parses ISO date strings,
        - booleans: recognizes common textual and numeric boolean representations,
        - other types: attempts to call the sample's type on `raw`.

        Parameters:
            sample: A value whose type and semantics should be used as the target.
            raw: The input value to coerce.

        Returns:
            The coerced value of the same type as `sample`, or `None` if `raw` cannot be sensibly converted.
        """
        if sample is None:
            return None

        if isinstance(sample, datetime):
            if isinstance(raw, datetime):
                parsed = raw
            elif isinstance(raw, str):
                candidate = raw.replace("Z", "+00:00")
                candidate = candidate.replace(" ", "T", 1)
                try:
                    parsed = datetime.fromisoformat(candidate)
                except ValueError:
                    return None
            else:
                return None

            if sample.tzinfo and parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=sample.tzinfo)
            elif not sample.tzinfo and parsed.tzinfo is not None:
                parsed = parsed.replace(tzinfo=None)
            return parsed

        if isinstance(sample, date) and not isinstance(sample, datetime):
            if isinstance(raw, date) and not isinstance(raw, datetime):
                return raw
            if isinstance(raw, str):
                try:
                    return date.fromisoformat(raw)
                except ValueError:
                    return None
            return None

        # Booleans: avoid bool("False") == True
        if isinstance(sample, bool):
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int,)):
                return bool(raw)
            if isinstance(raw, str):
                s = raw.strip().lower()
                if s in {"true", "1", "yes", "y", "t"}:
                    return True
                if s in {"false", "0", "no", "n", "f"}:
                    return False
            return None
        try:
            constructor = cast(Callable[[object], object], type(sample))
            return constructor(raw)
        except (TypeError, ValueError):
            if isinstance(raw, type(sample)):
                return raw
            return None

    def matches(op: str, value: object, val_key: object) -> bool:
        """Evaluate whether a runtime value matches a stored lookup value."""
        return matches_lookup_value(op, value, val_key)

    def current_value_for_path(path: list[str]) -> object | None:
        """
        Fetches the current value from the captured `instance` by following a sequence of attribute names.

        Parameters:
            path (list[str]): Ordered attribute names to traverse on the instance (e.g., ["user", "profile", "email"]).

        Returns:
            The value found at the end of the attribute path, or `None` if any attribute along the path is missing.
        """
        current: object = instance
        for attr in path:
            current = getattr(current, attr, UNDEFINED)
            if current is UNDEFINED:
                return None
        return current

    def evaluate_composite(
        cache_key: str,
        lookup_key: str,
        action: Literal["filter", "exclude"],
        model_section: dict[str, dict[str, set[str]]],
    ) -> bool | None:
        """
        Determine whether a composite dependency (multiple lookup params grouped under a single identifier)
        for a given cache key and lookup should cause cache invalidation.

        Parameters:
            cache_key (str): The cache key being evaluated.
            lookup_key (str): The specific lookup (operator and attribute path joined by `"__"`) that prompted evaluation.
            action (Literal["filter", "exclude"]): The dependency action context; "filter" treats a match as cause for invalidation,
                "exclude" treats a change in match membership as cause for invalidation.
            model_section (dict[str, dict[str, set[str]]]): The index section for the model containing lookup maps and an
                optional "__cache_dependencies__" mapping from cache keys to sets of identifier strings (each identifier
                encodes multiple lookup parameters).

        Returns:
            bool | None: `True` if the composite dependency indicates the cache entry should be invalidated,
            `False` if it indicates no invalidation is required, or `None` if there are no composite identifiers
            registered for `cache_key`.
        """
        cache_dependencies = model_section.get("__cache_dependencies__", {})
        identifiers = cache_dependencies.get(cache_key) if cache_dependencies else None
        if not identifiers:
            return None

        for identifier in identifiers:
            params = parse_dependency_identifier(identifier)
            if not isinstance(params, dict):
                continue
            if lookup_key not in params:
                continue
            old_all = True
            new_all = True
            for param_lookup, expected in params.items():
                parts_param = param_lookup.split("__")
                if parts_param[-1] in (
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "in",
                    "contains",
                    "startswith",
                    "endswith",
                    "regex",
                ):
                    op_param = parts_param[-1]
                    attr_path_param = parts_param[:-1]
                else:
                    op_param = "eq"
                    attr_path_param = parts_param
                expected_key = json.dumps(
                    _normalize_dependency_identifier(expected), sort_keys=True
                )
                old_val_param = old_relevant_values.get("__".join(attr_path_param))
                new_val_param = current_value_for_path(attr_path_param)
                if not matches(op_param, old_val_param, expected_key):
                    old_all = False
                if not matches(op_param, new_val_param, expected_key):
                    new_all = False
                if not old_all and not new_all and action == "filter":
                    break
            if action == "filter":
                if old_all or new_all:
                    return True
            else:  # exclude
                if old_all != new_all:
                    return True
        return False

    def bucket_membership_matches(
        params: dict[str, object],
        *,
        use_old_values: bool,
    ) -> bool:
        """
        Check whether the changed row belongs to a bucket described by filters/excludes.
        """
        filters = params.get("filters", {})
        excludes = params.get("excludes", {})
        if not isinstance(filters, dict) or not isinstance(excludes, dict):
            return False

        def value_for_lookup(attr_path: list[str]) -> object | None:
            if use_old_values:
                return old_relevant_values.get("__".join(attr_path))
            return current_value_for_path(attr_path)

        for lookup, expected in filters.items():
            parts = lookup.split("__")
            if parts[-1] in (
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "contains",
                "startswith",
                "endswith",
                "regex",
            ):
                op = parts[-1]
                attr_path = parts[:-1]
            else:
                op = "eq"
                attr_path = parts
            expected_key = json.dumps(
                _normalize_dependency_identifier(expected), sort_keys=True
            )
            if not matches(op, value_for_lookup(attr_path), expected_key):
                return False

        for lookup, expected in excludes.items():
            parts = lookup.split("__")
            if parts[-1] in (
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "contains",
                "startswith",
                "endswith",
                "regex",
            ):
                op = parts[-1]
                attr_path = parts[:-1]
            else:
                op = "eq"
                attr_path = parts
            expected_key = json.dumps(
                _normalize_dependency_identifier(expected), sort_keys=True
            )
            if matches(op, value_for_lookup(attr_path), expected_key):
                return False

        return True

    for action in ACTIONS:
        action_section = cast(
            dict[general_manager_name, manager_dependency_section],
            idx[action],
        )
        model_section = action_section.get(manager_name)
        if not isinstance(model_section, dict):
            continue
        for lookup, lookup_map in list(model_section.items()):
            if lookup.startswith("__"):
                if lookup == ALL_RECORDS_LOOKUP:
                    for cache_keys in list(lookup_map.values()):
                        for ck in list(cache_keys):
                            logger.info(
                                "invalidating cache key",
                                context={
                                    "manager": manager_name,
                                    "key": ck,
                                    "lookup": lookup,
                                    "action": action,
                                    "value": ALL_RECORDS_VALUE,
                                },
                            )
                            cache.delete(ck)
                            _remove_cache_keys_from_index_locked(idx, (ck,))
                            invalidated_cache_keys.add(ck)
                elif lookup.startswith("__sort__"):
                    sort_lookup = lookup.removeprefix("__sort__")
                    attr_path = sort_lookup.split("__")
                    old_sort_value = old_relevant_values.get(sort_lookup)
                    new_sort_value = current_value_for_path(attr_path)
                    if old_sort_value == new_sort_value:
                        continue
                    for val_key, cache_keys in list(lookup_map.items()):
                        payload = _json_loads_val_key(val_key)
                        if not isinstance(payload, dict):
                            continue
                        old_in_bucket = bucket_membership_matches(
                            payload,
                            use_old_values=True,
                        )
                        new_in_bucket = bucket_membership_matches(
                            payload,
                            use_old_values=False,
                        )
                        if not (old_in_bucket or new_in_bucket):
                            continue
                        for ck in list(cache_keys):
                            logger.info(
                                "invalidating cache key",
                                context={
                                    "manager": manager_name,
                                    "key": ck,
                                    "lookup": lookup,
                                    "action": action,
                                    "value": val_key,
                                },
                            )
                            cache.delete(ck)
                            _remove_cache_keys_from_index_locked(idx, (ck,))
                            invalidated_cache_keys.add(ck)
                continue
            # 1) get operator and attribute path
            parts = lookup.split("__")
            if parts[-1] in (
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "contains",
                "startswith",
                "endswith",
                "regex",
            ):
                op = parts[-1]
                attr_path = parts[:-1]
            else:
                op = "eq"
                attr_path = parts

            # 2) get old & new value
            old_val = old_relevant_values.get("__".join(attr_path))

            current: object = instance
            for attr in attr_path:
                current = getattr(current, attr, None)
                if current is None:
                    break
            new_val = current

            # 3) check against all cache_keys
            for val_key, cache_keys in list(lookup_map.items()):
                old_match = matches(op, old_val, val_key)
                new_match = matches(op, new_val, val_key)

                if action == "filter":
                    # Filter: invalidate if new match or old match
                    for ck in list(cache_keys):
                        composite_decision = evaluate_composite(
                            ck, lookup, action, model_section
                        )
                        should_invalidate = (
                            composite_decision
                            if composite_decision is not None
                            else (new_match or old_match)
                        )
                        if should_invalidate:
                            logger.info(
                                "invalidating cache key",
                                context={
                                    "manager": manager_name,
                                    "key": ck,
                                    "lookup": lookup,
                                    "action": action,
                                    "value": val_key,
                                },
                            )
                            cache.delete(ck)
                            _remove_cache_keys_from_index_locked(idx, (ck,))
                            invalidated_cache_keys.add(ck)

                else:  # action == 'exclude'
                    # Excludes: invalidate only if matches changed
                    for ck in list(cache_keys):
                        composite_decision = evaluate_composite(
                            ck, lookup, action, model_section
                        )
                        should_invalidate = (
                            composite_decision
                            if composite_decision is not None
                            else (old_match != new_match)
                        )
                        if should_invalidate:
                            logger.info(
                                "invalidating cache key",
                                context={
                                    "manager": manager_name,
                                    "key": ck,
                                    "lookup": lookup,
                                    "action": action,
                                    "value": val_key,
                                },
                            )
                            cache.delete(ck)
                            _remove_cache_keys_from_index_locked(idx, (ck,))
                            invalidated_cache_keys.add(ck)

    return invalidated_cache_keys


def _generic_cache_invalidation_from_shards(
    manager_name: str,
    instance: GeneralManager,
    old_relevant_values: dict[str, object],
) -> set[str]:
    def value_for_lookup(lookup: str, *, use_old_values: bool) -> object | None:
        spec = lookup_spec_from_key(lookup)
        lookup_name = "__".join(spec.attr_path)
        if use_old_values:
            return old_relevant_values.get(lookup_name)
        return resolve_current_value_for_path(instance, spec.attr_path)

    def params_match(params: dict[str, object], *, use_old_values: bool) -> bool:
        for lookup, expected in params.items():
            spec = lookup_spec_from_key(str(lookup))
            expected_key = serialize_dependency_identifier(expected)
            if not matches_lookup_value(
                spec.operator,
                value_for_lookup(spec.lookup, use_old_values=use_old_values),
                expected_key,
            ):
                return False
        return True

    def dependency_matches(
        action: str,
        identifier: str,
        *,
        changed_lookup: str | None,
    ) -> bool:
        if action in {"all", "request_query"}:
            return True
        if action == "identification":
            identification = getattr(instance, "identification", None)
            return bool(
                identification
                and serialize_dependency_identifier(identification) == identifier
            )
        if action not in ACTIONS:
            return False
        params = parse_dependency_identifier(identifier)
        if not isinstance(params, dict):
            return False
        if not params:
            return True

        sort_payloads = [
            payload
            for lookup, payload in params.items()
            if str(lookup).startswith("__sort__")
            and (
                changed_lookup is None
                or str(lookup).removeprefix("__sort__") == changed_lookup
            )
        ]
        if sort_payloads:
            for payload in sort_payloads:
                if not isinstance(payload, dict):
                    continue
                filters = payload.get("filters", {})
                excludes = payload.get("excludes", {})
                if not isinstance(filters, dict) or not isinstance(excludes, dict):
                    continue
                old_in_bucket = params_match(filters, use_old_values=True) and not (
                    params_match(excludes, use_old_values=True) if excludes else False
                )
                new_in_bucket = params_match(filters, use_old_values=False) and not (
                    params_match(excludes, use_old_values=False) if excludes else False
                )
                if old_in_bucket or new_in_bucket:
                    return True
            return False

        old_match = params_match(params, use_old_values=True)
        new_match = params_match(params, use_old_values=False)
        if action == "filter":
            return old_match or new_match
        return old_match != new_match

    def candidate_should_invalidate(
        cache_key: str,
        action: str,
        changed_lookup: str | None,
    ) -> bool:
        reverse = cache.get(reverse_membership_key(cache_key))
        if not isinstance(reverse, ReverseDependencyMembership):
            return True
        for manager, dependency_action, identifier in reverse.simple_dependencies:
            if manager == manager_name and dependency_matches(
                dependency_action,
                identifier,
                changed_lookup=changed_lookup,
            ):
                return True
        for manager, dependency_action, identifier in reverse.composite_dependencies:
            if (
                manager == manager_name
                and dependency_action == action
                and dependency_matches(
                    dependency_action,
                    identifier,
                    changed_lookup=changed_lookup,
                )
            ):
                return True
        return False

    invalidation_candidates: set[str] = set()
    invalidated_cache_keys: set[str] = set()

    invalidation_candidates.update(request_query_cache_keys(manager_name))
    invalidation_candidates.update(all_records_cache_keys(manager_name))
    identification = getattr(instance, "identification", None)
    if identification:
        invalidation_candidates.update(
            candidate_cache_keys_for_lookup(
                manager_name,
                "filter",
                "identification",
                old_value=serialize_dependency_identifier(identification),
                new_value=serialize_dependency_identifier(identification),
            )
        )

    for lookup in tracked_lookup_names(manager_name):
        old_value = old_relevant_values.get(lookup)
        new_value = resolve_current_value_for_path(instance, tuple(lookup.split("__")))
        for action in ACTIONS:
            for cache_key in candidate_cache_keys_for_lookup(
                manager_name,
                action,
                lookup,
                old_value=old_value,
                new_value=new_value,
            ):
                if candidate_should_invalidate(cache_key, action, lookup):
                    invalidation_candidates.add(cache_key)

    for cache_key in tuple(dict.fromkeys(invalidation_candidates)):
        logger.info(
            "invalidating cache key",
            context={
                "manager": manager_name,
                "key": cache_key,
            },
        )
        cache.delete(cache_key)
        remove_cache_key_from_shards(cache_key)
        invalidated_cache_keys.add(cache_key)

    return invalidated_cache_keys


@receiver(post_data_change)
def generic_cache_invalidation(
    sender: type[GeneralManager],
    instance: GeneralManager,
    old_relevant_values: dict[str, object],
    **kwargs: object,
) -> None:
    """
    Invalidate cache entries whose recorded dependencies are affected by changes to a GeneralManager instance.

    Uses the dependency index to compare previously captured values against the instance's current values for tracked lookups, evaluates both simple and composite dependency conditions for "filter" and "exclude" actions, and for any dependency that warrants invalidation it deletes the corresponding cache entry and removes its references from the index.

    Parameters:
        sender (type[GeneralManager]): Manager class that emitted the signal.
        instance (GeneralManager): The manager instance that was changed.
        old_relevant_values (dict[str, object]): Mapping of lookup paths (joined by "__") to their values as captured before the change; used to compare old vs. new values for invalidation decisions.
    """
    manager_name = sender.__name__
    invalidated_cache_keys: set[str] = set()
    acquire_lock_with_retry("generic_cache_invalidation")
    try:
        if not reverse_memberships():
            idx = get_full_index()
            sections: tuple[Literal["filter", "exclude", "all", "request_query"], ...]
            sections = ("filter", "exclude", "all", "request_query")
            if any(idx.get(section) for section in sections):
                invalidated_cache_keys = _generic_cache_invalidation_locked(
                    idx,
                    manager_name,
                    instance,
                    old_relevant_values,
                )
                set_full_index(idx)
            else:
                invalidated_cache_keys = _generic_cache_invalidation_from_shards(
                    manager_name,
                    instance,
                    old_relevant_values,
                )
        else:
            invalidated_cache_keys = _generic_cache_invalidation_from_shards(
                manager_name,
                instance,
                old_relevant_values,
            )
    finally:
        release_lock()
    if invalidated_cache_keys:
        record_invalidated_cache_keys_for_graphql_rewarm(invalidated_cache_keys)
