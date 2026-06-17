"""Cache-backed sharded dependency metadata for dependency-cached results."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

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
    """Return the reverse metadata cache key for an application cache key."""
    return f"{DEPENDENCY_SHARD_PREFIX}:reverse:{_hash_text(cache_key)}"


def exact_lookup_shard_key(
    manager_name: str,
    action: str,
    lookup: str,
    operator: str,
    value: Any,
) -> str:
    """Return the shard key for an exact lookup/value dependency."""
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
    """Return the shard key for a lookup that must be predicate-scanned."""
    return f"{DEPENDENCY_SHARD_PREFIX}:scan:{manager_name}:{action}:{lookup}:{operator}"


def composite_lookup_shard_key(
    manager_name: str,
    action: str,
    lookup: str,
) -> str:
    """Return the shard key containing composite candidates for one lookup."""
    return (
        f"{DEPENDENCY_SHARD_PREFIX}:composite_lookup:{manager_name}:{action}:{lookup}"
    )


def all_records_shard_key(manager_name: str) -> str:
    """Return the shard key for cache entries affected by any row change."""
    return f"{DEPENDENCY_SHARD_PREFIX}:all:{manager_name}"


def request_query_shard_key(manager_name: str) -> str:
    """Return the shard key for request-query cache entries for a manager."""
    return f"{DEPENDENCY_SHARD_PREFIX}:request_query:{manager_name}"


def lookup_registry_key(manager_name: str, action: str) -> str:
    """Return the cache key that stores lookup names tracked for a manager/action."""
    return f"{DEPENDENCY_SHARD_PREFIX}:lookups:{manager_name}:{action}"


def cache_set_members(key: str) -> set[str]:
    """Read a cache-backed set."""
    members = cache.get(key, set())
    if members is None:
        return set()
    return set(members)


def _cache_get_many(keys: Iterable[str]) -> dict[str, Any]:
    key_tuple = tuple(dict.fromkeys(keys))
    if not key_tuple:
        return {}
    return dict(cache.get_many(key_tuple))


def _cache_set_many(payloads: Mapping[str, Any]) -> None:
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


def _cache_member_set(value: Any) -> set[str]:
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


def _cache_keys_from_member_collection(value: Any) -> set[str]:
    if not isinstance(value, (set, frozenset, list, tuple)):
        return set()
    return {member for member in value if isinstance(member, str)}


def _legacy_dependency_cache_keys(legacy_index: Any) -> set[str]:
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
    """Delete legacy full-index metadata and cache values referenced by it."""
    legacy_index = cache.get(LEGACY_DEPENDENCY_INDEX_KEY, None)
    if legacy_index is None:
        return set()
    cache_keys = _legacy_dependency_cache_keys(legacy_index)
    for cache_key in cache_keys:
        cache.delete(cache_key)
    cache.delete(LEGACY_DEPENDENCY_INDEX_KEY)
    return cache_keys


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
    """Record dependency metadata for one cache key in deterministic shards."""
    record_many_cache_dependencies(((cache_key, dependencies),))


def record_many_cache_dependencies(
    entries: Iterable[tuple[str, Iterable[Dependency]]],
) -> None:
    """Record dependency metadata for many cache keys."""
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
    """Remove a cache key from all shards using its reverse membership."""
    reverse_key = reverse_membership_key(cache_key)
    reverse = cache.get(reverse_key)
    if not isinstance(reverse, ReverseDependencyMembership):
        cache.delete(reverse_key)
        _cache_set_discard(REVERSE_MEMBERSHIP_REGISTRY_KEY, reverse_key)
        return
    for shard_key in reverse.shard_keys:
        _cache_set_discard(shard_key, cache_key)
    cache.delete(reverse_key)
    _cache_set_discard(REVERSE_MEMBERSHIP_REGISTRY_KEY, reverse_key)


def candidate_cache_keys_for_lookup(
    manager_name: str,
    action: Literal["filter", "exclude"],
    lookup: str,
    *,
    old_value: Any = VALUE_NOT_PROVIDED,
    new_value: Any = VALUE_NOT_PROVIDED,
) -> set[str]:
    """Return cache keys stored in shards that may be affected by a lookup change."""
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
    """Return request-query cache keys for a manager."""
    return cache_set_members(request_query_shard_key(manager_name))


def all_records_cache_keys(manager_name: str) -> set[str]:
    """Return all-records cache keys for a manager."""
    return cache_set_members(all_records_shard_key(manager_name))


def tracked_lookup_names(manager_name: str) -> set[str]:
    """Return lookup attribute paths tracked for a manager across filter/exclude."""
    return cache_set_members(
        lookup_registry_key(manager_name, "filter")
    ) | cache_set_members(lookup_registry_key(manager_name, "exclude"))


def reverse_memberships() -> tuple[ReverseDependencyMembership, ...]:
    """Return all reverse memberships known to the shard store."""
    memberships = []
    for reverse_key in cache_set_members(REVERSE_MEMBERSHIP_REGISTRY_KEY):
        reverse = cache.get(reverse_key)
        if isinstance(reverse, ReverseDependencyMembership):
            memberships.append(reverse)
    return tuple(memberships)
