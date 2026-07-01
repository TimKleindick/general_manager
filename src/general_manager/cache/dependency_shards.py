"""Cache-backed sharded dependency metadata for dependency-cached results.

Dependencies are `(manager_name, action, identifier)` tuples. `action` is one of
`"filter"`, `"exclude"`, `"identification"`, `"request_query"`, or `"all"`.
Composite dependencies are the subset of filter/exclude dependencies whose
identifier serializes more than one lookup, and simple dependencies are all
other dependency tuples retained in reverse metadata.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from django.core.cache import cache

from general_manager.cache.dependency_matching import (
    SCAN_OPERATORS,
    lookup_spec_from_key,
    parse_dependency_identifier,
    stable_value_hash,
)

DEPENDENCY_SHARD_PREFIX = "general_manager:dependency:v1"
LEGACY_DEPENDENCY_INDEX_KEY = "dependency_index"
ALL_RECORDS_VALUE = "__all__"
REVERSE_MEMBERSHIP_REGISTRY_KEY = f"{DEPENDENCY_SHARD_PREFIX}:reverse_keys"
VALUE_NOT_PROVIDED = object()

DependencyAction = Literal[
    "filter", "exclude", "identification", "request_query", "all"
]
Dependency = tuple[str, DependencyAction, str]
CompositeDependency = tuple[str, Literal["filter", "exclude"], str]
SimpleDependency = tuple[str, DependencyAction, str]


@dataclass(frozen=True, slots=True)
class ReverseDependencyMembership:
    """Reverse metadata describing where one cache key was registered.

    Attributes:
        cache_key: Application cache key being tracked.
        shard_keys: Forward shard cache keys containing `cache_key`.
        composite_dependencies: Filter/exclude dependency tuples that must be
            re-evaluated after a candidate lookup changes.
        simple_dependencies: Non-composite dependency tuples retained for
            runtime invalidation checks. The default empty set is part of the
            public construction contract.

    Notes:
        Read helpers treat only actual `ReverseDependencyMembership` instances
        as valid reverse payloads. They do not validate or sanitize dependency
        tuple members stored inside an otherwise valid instance.
    """

    cache_key: str
    shard_keys: frozenset[str]
    composite_dependencies: frozenset[CompositeDependency]
    simple_dependencies: frozenset[SimpleDependency] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class _DependencyShardPlan:
    shard_keys: frozenset[str]
    composite_dependencies: frozenset[CompositeDependency]
    simple_dependencies: frozenset[SimpleDependency]
    lookup_registrations: frozenset[tuple[str, str, str]]


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def reverse_membership_key(cache_key: str) -> str:
    """Return the reverse metadata cache key for an application cache key.

    The raw `cache_key` is SHA-256 hashed, so application key characters are not
    embedded in the returned string. The format is
    `general_manager:dependency:v1:reverse:{hash}`.
    """
    return f"{DEPENDENCY_SHARD_PREFIX}:reverse:{_hash_text(cache_key)}"


def exact_lookup_shard_key(
    manager_name: str,
    action: str,
    lookup: str,
    operator: str,
    value: object,
) -> str:
    """Return the shard key for an exact lookup/value dependency.

    Args:
        manager_name: Name of the manager class that owns the dependency.
        action: Dependency action namespace, normally `"filter"` or `"exclude"`.
        lookup: Normalized lookup attribute path.
        operator: Lookup operator namespace. Exact dependencies use `"eq"`.
        value: Dependency value to hash with GeneralManager's stable serializer.

    Returns:
        A deterministic cache key for cache entries depending on that exact
        lookup value. The format is
        `general_manager:dependency:v1:lookup:{manager}:{action}:{lookup}:`
        `{operator}:{stable_value_hash(value)}`. Text inputs are interpolated as
        provided; only `value` is normalized and hashed. Serialization rules and
        errors are those of `stable_value_hash()`: mappings, sequences, sets,
        dates, datetimes, JSON scalar values, mapping-shaped `__getstate__()`,
        and `repr(...)` fallback are normalized deterministically before the
        SHA-256 hash is computed.
    """
    return (
        f"{DEPENDENCY_SHARD_PREFIX}:lookup:{manager_name}:{action}:"
        f"{lookup}:{operator}:{stable_value_hash(value)}"
    )


def scan_lookup_shard_key(
    manager_name: str,
    action: str,
    lookup: str,
    operator: str,
) -> str:
    """Return the shard key for a lookup that must be predicate-scanned.

    Args:
        manager_name: Name of the manager class that owns the dependency.
        action: Dependency action namespace, normally `"filter"` or `"exclude"`.
        lookup: Lookup string stored for the scan operator.
        operator: Non-exact lookup operator, such as `"gte"` or `"contains"`.

    Returns:
        A deterministic cache key for cache entries that need runtime predicate
        evaluation when this lookup changes. The format is
        `general_manager:dependency:v1:scan:{manager}:{action}:{lookup}:`
        `{operator}`. Text inputs are interpolated as provided.
    """
    return f"{DEPENDENCY_SHARD_PREFIX}:scan:{manager_name}:{action}:{lookup}:{operator}"


def composite_lookup_shard_key(
    manager_name: str,
    action: str,
    lookup: str,
) -> str:
    """Return the shard key containing composite candidates for one lookup.

    Args:
        manager_name: Name of the manager class that owns the dependency.
        action: Composite dependency action, either `"filter"` or `"exclude"`.
        lookup: Normalized lookup attribute path that can affect the composite.

    Returns:
        A deterministic cache key for cache entries whose multi-lookup
        dependency must be re-evaluated when the lookup changes. The format is
        `general_manager:dependency:v1:composite_lookup:{manager}:{action}:`
        `{lookup}`. Text inputs are interpolated as provided.
    """
    return (
        f"{DEPENDENCY_SHARD_PREFIX}:composite_lookup:{manager_name}:{action}:{lookup}"
    )


def all_records_shard_key(manager_name: str) -> str:
    """Return the shard key for cache entries affected by any row change.

    Args:
        manager_name: Name of the manager class that owns the dependency.

    Returns:
        A deterministic cache key for cache entries invalidated by every change
        for that manager. The format is
        `general_manager:dependency:v1:all:{manager}`. The manager name is
        interpolated as provided.
    """
    return f"{DEPENDENCY_SHARD_PREFIX}:all:{manager_name}"


def request_query_shard_key(manager_name: str) -> str:
    """Return the shard key for request-query cache entries for a manager.

    Args:
        manager_name: Name of the remote/request manager class.

    Returns:
        A deterministic cache key for request-query dependency candidates. The
        format is `general_manager:dependency:v1:request_query:{manager}`. The
        manager name is interpolated as provided.
    """
    return f"{DEPENDENCY_SHARD_PREFIX}:request_query:{manager_name}"


def lookup_registry_key(manager_name: str, action: str) -> str:
    """Return the cache key that stores lookup names tracked for a manager/action.

    Args:
        manager_name: Name of the manager class.
        action: Lookup action namespace, normally `"filter"` or `"exclude"`.

    Returns:
        The cache key for the set of lookup names tracked for that pair. The
        format is `general_manager:dependency:v1:lookups:{manager}:{action}`.
        Text inputs are interpolated as provided.
    """
    return f"{DEPENDENCY_SHARD_PREFIX}:lookups:{manager_name}:{action}"


def cache_set_members(key: str) -> set[str]:
    """Read string members from a cache-backed set-like value.

    Args:
        key: Cache key to read.

    Returns:
        String members from a cached set, frozenset, list, or tuple. Missing
        values, `None`, and other payload types return an empty set. Non-string
        members inside an accepted collection are dropped member-by-member.
    """
    members = cache.get(key, set())
    return _cache_member_set(members)


def _cache_get_many(keys: Iterable[str]) -> dict[str, object]:
    key_tuple = tuple(dict.fromkeys(keys))
    if not key_tuple:
        return {}
    return dict(cache.get_many(key_tuple))


def _cache_set_many(payloads: Mapping[str, object]) -> None:
    if payloads:
        cache.set_many(dict(payloads), None)


def _cache_delete_many(keys: Iterable[str]) -> None:
    key_tuple = tuple(dict.fromkeys(keys))
    if not key_tuple:
        return
    delete_many = getattr(cache, "delete_many", None)
    if callable(delete_many):
        delete_many(key_tuple)
        return
    for key in key_tuple:
        cache.delete(key)


def _cache_member_set(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (set, frozenset, list, tuple)):
        return {member for member in value if isinstance(member, str)}
    return set()


def _cache_set_add_many(additions: Mapping[str, set[str]]) -> None:
    pending = {key: set(members) for key, members in additions.items() if members}
    if not pending:
        return

    existing_sets = _cache_get_many(pending)
    payloads: dict[str, set[str]] = {}
    for key, members in pending.items():
        current = _cache_member_set(existing_sets.get(key, set()))
        current.update(members)
        payloads[key] = current
    _cache_set_many(payloads)


def _cache_set_discard_many(removals: Mapping[str, set[str]]) -> None:
    pending = {key: set(members) for key, members in removals.items() if members}
    if not pending:
        return

    existing_sets = _cache_get_many(pending)
    payloads: dict[str, set[str]] = {}
    delete_keys: list[str] = []
    for key, members in pending.items():
        current = _cache_member_set(existing_sets.get(key, set()))
        current.difference_update(members)
        if current:
            payloads[key] = current
        else:
            delete_keys.append(key)

    _cache_set_many(payloads)
    _cache_delete_many(delete_keys)


def _cache_set_add(key: str, member: str) -> None:
    _cache_set_add_many({key: {member}})


def _cache_set_discard(key: str, member: str) -> None:
    _cache_set_discard_many({key: {member}})


def _lookup_name_for_candidate(lookup: str) -> str:
    spec = lookup_spec_from_key(lookup)
    return "__".join(spec.attr_path)


def _register_lookup(manager_name: str, action: str, lookup: str) -> None:
    _cache_set_add(lookup_registry_key(manager_name, action), lookup)


def _cache_keys_from_member_collection(value: object) -> set[str]:
    if not isinstance(value, (set, frozenset, list, tuple)):
        return set()
    return {member for member in value if isinstance(member, str)}


def _legacy_dependency_cache_keys(legacy_index: object) -> set[str]:
    if not isinstance(legacy_index, dict):
        return set()

    cache_keys: set[str] = set()
    all_section = legacy_index.get("all", {})
    if isinstance(all_section, dict):
        for key_set in all_section.values():
            cache_keys.update(_cache_keys_from_member_collection(key_set))

    request_query_section = legacy_index.get("request_query", {})
    if isinstance(request_query_section, dict):
        for query_section in request_query_section.values():
            if not isinstance(query_section, dict):
                continue
            for key_set in query_section.values():
                cache_keys.update(_cache_keys_from_member_collection(key_set))

    for action in ("filter", "exclude"):
        action_section = legacy_index.get(action, {})
        if not isinstance(action_section, dict):
            continue
        for model_section in action_section.values():
            if not isinstance(model_section, dict):
                continue
            cache_dependencies = model_section.get("__cache_dependencies__", {})
            if isinstance(cache_dependencies, dict):
                cache_keys.update(
                    key for key in cache_dependencies if isinstance(key, str)
                )
            for lookup, lookup_map in model_section.items():
                if lookup == "__cache_dependencies__" or not isinstance(
                    lookup_map, dict
                ):
                    continue
                for key_set in lookup_map.values():
                    cache_keys.update(_cache_keys_from_member_collection(key_set))
    return cache_keys


def clear_legacy_dependency_index() -> set[str]:
    """Delete legacy full-index metadata and cache values referenced by it.

    Returns:
        Cache keys found in the legacy index and submitted to `cache.delete()`
        as stale cached values. The supported legacy schema has top-level
        `"all"` manager sections, `"request_query"` manager/query sections, and
        `"filter"` / `"exclude"` manager sections containing lookup maps plus
        optional `"__cache_dependencies__"` cache-key maps. Malformed legacy
        structures are non-dict section payloads, non-set-like member
        collections, or non-string cache keys; malformed branches are skipped
        member-by-member where possible and may produce an empty set after
        deleting the index key. Delete failures or backend errors propagate from
        Django's cache backend.
    """
    legacy_index = cache.get(LEGACY_DEPENDENCY_INDEX_KEY, None)
    if legacy_index is None:
        return set()
    cache_keys = _legacy_dependency_cache_keys(legacy_index)
    for cache_key in cache_keys:
        cache.delete(cache_key)
    cache.delete(LEGACY_DEPENDENCY_INDEX_KEY)
    return cache_keys


@lru_cache(maxsize=16384)
def _shard_keys_for_dependency(
    manager_name: str,
    action: DependencyAction,
    identifier: str,
) -> _DependencyShardPlan:
    shard_keys: set[str] = set()
    composites: set[CompositeDependency] = set()
    simple_dependencies: set[SimpleDependency] = set()
    lookup_registrations: set[tuple[str, str, str]] = set()

    if action == "all":
        shard_keys.add(all_records_shard_key(manager_name))
        simple_dependencies.add((manager_name, action, identifier))
        return _DependencyShardPlan(
            frozenset(shard_keys),
            frozenset(composites),
            frozenset(simple_dependencies),
            frozenset(lookup_registrations),
        )

    if action == "request_query":
        shard_keys.add(request_query_shard_key(manager_name))
        simple_dependencies.add((manager_name, action, identifier))
        return _DependencyShardPlan(
            frozenset(shard_keys),
            frozenset(composites),
            frozenset(simple_dependencies),
            frozenset(lookup_registrations),
        )

    if action == "identification":
        shard_keys.add(
            exact_lookup_shard_key(
                manager_name,
                "filter",
                "identification",
                "eq",
                identifier,
            )
        )
        simple_dependencies.add((manager_name, action, identifier))
        return _DependencyShardPlan(
            frozenset(shard_keys),
            frozenset(composites),
            frozenset(simple_dependencies),
            frozenset(lookup_registrations),
        )

    if action not in {"filter", "exclude"}:
        return _DependencyShardPlan(
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
        )

    params = parse_dependency_identifier(identifier)
    if not isinstance(params, dict):
        return _DependencyShardPlan(
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset(),
        )

    if not params:
        shard_keys.add(all_records_shard_key(manager_name))
        simple_dependencies.add((manager_name, action, identifier))
        return _DependencyShardPlan(
            frozenset(shard_keys),
            frozenset(composites),
            frozenset(simple_dependencies),
            frozenset(lookup_registrations),
        )

    sort_lookups = [
        str(lookup).removeprefix("__sort__")
        for lookup in params
        if str(lookup).startswith("__sort__")
    ]
    if sort_lookups:
        composite = (manager_name, action, identifier)
        composites.add(composite)
        for sort_lookup in sort_lookups:
            lookup_registrations.add((manager_name, action, sort_lookup))
            shard_keys.add(
                composite_lookup_shard_key(manager_name, action, sort_lookup)
            )
        return _DependencyShardPlan(
            frozenset(shard_keys),
            frozenset(composites),
            frozenset(simple_dependencies),
            frozenset(lookup_registrations),
        )

    if len(params) > 1:
        composite = (manager_name, action, identifier)
        composites.add(composite)
        for lookup in params:
            candidate_lookup = _lookup_name_for_candidate(str(lookup))
            lookup_registrations.add((manager_name, action, candidate_lookup))
            shard_keys.add(
                composite_lookup_shard_key(
                    manager_name,
                    action,
                    candidate_lookup,
                )
            )
        return _DependencyShardPlan(
            frozenset(shard_keys),
            frozenset(composites),
            frozenset(simple_dependencies),
            frozenset(lookup_registrations),
        )

    lookup, value = next(iter(params.items()))
    spec = lookup_spec_from_key(str(lookup))
    candidate_lookup = "__".join(spec.attr_path)
    lookup_registrations.add((manager_name, action, candidate_lookup))
    if spec.operator == "eq":
        shard_keys.add(
            exact_lookup_shard_key(manager_name, action, spec.lookup, "eq", value)
        )
    else:
        shard_keys.add(
            scan_lookup_shard_key(manager_name, action, spec.lookup, spec.operator)
        )
    simple_dependencies.add((manager_name, action, identifier))
    return _DependencyShardPlan(
        frozenset(shard_keys),
        frozenset(composites),
        frozenset(simple_dependencies),
        frozenset(lookup_registrations),
    )


def _reverse_membership_for_dependencies(
    cache_key: str,
    dependency_set: set[Dependency],
) -> tuple[ReverseDependencyMembership, set[tuple[str, str, str]]]:
    shard_keys: set[str] = set()
    composites: set[CompositeDependency] = set()
    simple_dependencies: set[SimpleDependency] = set()
    lookup_registrations: set[tuple[str, str, str]] = set()

    for manager_name, action, identifier in dependency_set:
        plan = _shard_keys_for_dependency(manager_name, action, identifier)
        shard_keys.update(plan.shard_keys)
        composites.update(plan.composite_dependencies)
        simple_dependencies.update(plan.simple_dependencies)
        lookup_registrations.update(plan.lookup_registrations)

    return (
        ReverseDependencyMembership(
            cache_key=cache_key,
            shard_keys=frozenset(shard_keys),
            composite_dependencies=frozenset(composites),
            simple_dependencies=frozenset(simple_dependencies),
        ),
        lookup_registrations,
    )


def record_cache_dependencies(
    cache_key: str,
    dependencies: Iterable[Dependency],
) -> None:
    """Record dependency metadata for one cache key in deterministic shards.

    Args:
        cache_key: Cache entry whose dependency metadata should be replaced.
        dependencies: Dependency tuples in `(manager_name, action, identifier)`
            form. Empty iterables are a no-op. A non-empty iterable has the same
            replacement semantics as `record_many_cache_dependencies()`,
            including writing empty reverse metadata when all supplied
            dependencies normalize to no shards.
    """
    record_many_cache_dependencies(((cache_key, dependencies),))


def record_many_cache_dependencies(
    entries: Iterable[tuple[str, Iterable[Dependency]]],
) -> None:
    """Record dependency metadata for many cache keys.

    Args:
        entries: `(cache_key, dependencies)` pairs. Duplicate cache keys and
            duplicate dependencies are merged before the cache backend is
            touched.

    Behavior:
        A non-empty dependency set replaces prior reverse membership for that
        cache key, clears legacy full-index metadata once per batch, and writes
        all shard/reverse updates with batched cache operations where possible.
        Empty dependency iterables are ignored per cache key; repeated entries
        for the same cache key are unioned as a Python set before writing, so an
        empty repeated entry does not cancel a non-empty repeated entry. The
        public type contract requires well-formed 3-tuples with string manager
        names, typed actions, and string identifiers; callers that violate that
        contract may receive normal Python unpacking/type errors. Within that
        contract, unsupported actions, malformed filter/exclude JSON, and
        non-mapping filter/exclude identifiers map to no shard. If every supplied
        dependency for a cache key maps to no shard, the previous metadata is
        still replaced with an empty reverse membership. Cache writes are not
        atomic across shards; the order is legacy cleanup, previous-shard
        removal, new shard/lookup-registry writes, then reverse metadata writes.
        Backend errors and partial-write behavior propagate from Django's cache
        backend.
    """
    normalized: dict[str, set[Dependency]] = {}
    for cache_key, dependencies in entries:
        dependency_set = set(dependencies)
        if dependency_set:
            normalized.setdefault(cache_key, set()).update(dependency_set)
    if not normalized:
        return

    clear_legacy_dependency_index()

    reverse_keys = {
        cache_key: reverse_membership_key(cache_key) for cache_key in normalized
    }
    existing_reverses = _cache_get_many(reverse_keys.values())

    shard_removals: dict[str, set[str]] = defaultdict(set)
    for cache_key, reverse_key in reverse_keys.items():
        reverse = existing_reverses.get(reverse_key)
        if isinstance(reverse, ReverseDependencyMembership):
            for shard_key in reverse.shard_keys:
                shard_removals[shard_key].add(cache_key)

    _cache_set_discard_many(shard_removals)

    set_additions: dict[str, set[str]] = defaultdict(set)
    reverse_payloads: dict[str, ReverseDependencyMembership] = {}

    for cache_key, dependency_set in normalized.items():
        reverse, lookup_registrations = _reverse_membership_for_dependencies(
            cache_key,
            dependency_set,
        )
        for shard_key in reverse.shard_keys:
            set_additions[shard_key].add(cache_key)
        for manager_name, action, lookup in lookup_registrations:
            set_additions[lookup_registry_key(manager_name, action)].add(lookup)

        reverse_key = reverse_keys[cache_key]
        reverse_payloads[reverse_key] = reverse
        set_additions[REVERSE_MEMBERSHIP_REGISTRY_KEY].add(reverse_key)

    _cache_set_add_many(set_additions)
    _cache_set_many(reverse_payloads)


def remove_cache_key_from_shards(cache_key: str) -> None:
    """Remove a cache key from all shards using its reverse membership.

    Args:
        cache_key: Cache entry whose shard membership should be removed.

    Behavior:
        Missing reverse metadata or a payload that is not a
        `ReverseDependencyMembership` is treated as already removed; the reverse
        metadata key is deleted and removed from the reverse registry. Backend
        errors and partial-write behavior propagate from Django's cache backend.
    """
    reverse_key = reverse_membership_key(cache_key)
    reverse = cache.get(reverse_key)
    if not isinstance(reverse, ReverseDependencyMembership):
        cache.delete(reverse_key)
        _cache_set_discard(REVERSE_MEMBERSHIP_REGISTRY_KEY, reverse_key)
        return
    _cache_set_discard_many(
        {shard_key: {cache_key} for shard_key in reverse.shard_keys}
    )
    cache.delete(reverse_key)
    _cache_set_discard(REVERSE_MEMBERSHIP_REGISTRY_KEY, reverse_key)


def candidate_cache_keys_for_lookup(
    manager_name: str,
    action: Literal["filter", "exclude"],
    lookup: str,
    *,
    old_value: object = VALUE_NOT_PROVIDED,
    new_value: object = VALUE_NOT_PROVIDED,
) -> set[str]:
    """Return cache keys stored in shards that may be affected by a lookup change.

    Args:
        manager_name: Name of the changed manager class.
        action: Dependency action to inspect, either `"filter"` or `"exclude"`.
            Runtime callers are expected to honor this typed contract; the
            helper does not perform additional action validation.
        lookup: Changed lookup attribute path.
        old_value: Optional previous value for exact-match shard candidates.
            The sentinel `VALUE_NOT_PROVIDED` suppresses the exact old-value
            lookup. `None` is a real dependency value and is hashed.
        new_value: Optional current value for exact-match shard candidates. The
            sentinel `VALUE_NOT_PROVIDED` suppresses the exact new-value lookup.
            Serialization errors propagate from `stable_value_hash()`.

    Returns:
        Cache keys from exact, scan, composite, and all-records shards that may
        need runtime dependency evaluation. Scan shards are queried for every
        operator in `SCAN_OPERATORS` using both `{lookup}__{operator}` and the
        normalized base lookup name. Exact old/new values are looked up only in
        equality (`"eq"`) shards for the base lookup name returned by
        `lookup_spec_from_key()`, which treats a supported operator suffix such
        as `status__gte` as lookup path `status` plus operator `gte`.
    """
    candidates = set()
    candidate_lookup = _lookup_name_for_candidate(lookup)

    for value in (old_value, new_value):
        if value is not VALUE_NOT_PROVIDED:
            candidates.update(
                cache_set_members(
                    exact_lookup_shard_key(
                        manager_name,
                        action,
                        candidate_lookup,
                        "eq",
                        value,
                    )
                )
            )

    for operator in SCAN_OPERATORS:
        scan_lookup = f"{candidate_lookup}__{operator}"
        candidates.update(
            cache_set_members(
                scan_lookup_shard_key(manager_name, action, scan_lookup, operator)
            )
        )
        candidates.update(
            cache_set_members(
                scan_lookup_shard_key(manager_name, action, candidate_lookup, operator)
            )
        )

    candidates.update(
        cache_set_members(
            composite_lookup_shard_key(manager_name, action, candidate_lookup)
        )
    )
    candidates.update(cache_set_members(all_records_shard_key(manager_name)))
    return candidates


def request_query_cache_keys(manager_name: str) -> set[str]:
    """Return request-query cache keys for a manager.

    Args:
        manager_name: Name of the manager class.

    Returns:
        Cache keys registered as request-query dependencies for that manager.
        Missing or malformed shard payloads follow `cache_set_members()`
        behavior.
    """
    return cache_set_members(request_query_shard_key(manager_name))


def all_records_cache_keys(manager_name: str) -> set[str]:
    """Return all-records cache keys for a manager.

    Args:
        manager_name: Name of the manager class.

    Returns:
        Cache keys registered as all-records dependencies for that manager.
        Missing or malformed shard payloads follow `cache_set_members()`
        behavior.
    """
    return cache_set_members(all_records_shard_key(manager_name))


def tracked_lookup_names(manager_name: str) -> set[str]:
    """Return lookup attribute paths tracked for a manager across filter/exclude.

    Args:
        manager_name: Name of the manager class.

    Returns:
        Lookup attribute paths that should be read from changed manager
        instances before sharded invalidation runs. Filter and exclude registries
        are read independently; missing or malformed payloads on either side
        contribute an empty set through `cache_set_members()`. Registered names
        are normalized lookup attribute paths from `lookup_spec_from_key()`;
        operator suffixes are stripped before registration.
    """
    return cache_set_members(
        lookup_registry_key(manager_name, "filter")
    ) | cache_set_members(lookup_registry_key(manager_name, "exclude"))


def reverse_memberships() -> tuple[ReverseDependencyMembership, ...]:
    """Return all reverse memberships known to the shard store.

    Returns:
        Valid reverse-membership payloads currently listed in the reverse
        membership registry. Missing or malformed registry members are skipped.
        A malformed registry payload contributes no reverse keys through
        `cache_set_members()`. Dependency tuple members inside a valid
        `ReverseDependencyMembership` are returned unchanged.
    """
    memberships = []
    for reverse_key in cache_set_members(REVERSE_MEMBERSHIP_REGISTRY_KEY):
        reverse = cache.get(reverse_key)
        if isinstance(reverse, ReverseDependencyMembership):
            memberships.append(reverse)
    return tuple(memberships)
