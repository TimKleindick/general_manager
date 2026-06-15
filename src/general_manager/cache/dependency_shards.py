"""Cache-backed sharded dependency metadata for dependency-cached results."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from django.core.cache import cache

from general_manager.cache.dependency_matching import (
    SCAN_OPERATORS,
    lookup_spec_from_key,
    parse_dependency_identifier,
    stable_value_hash,
)

DEPENDENCY_SHARD_PREFIX = "general_manager:dependency:v1"
ALL_RECORDS_VALUE = "__all__"
REVERSE_MEMBERSHIP_REGISTRY_KEY = f"{DEPENDENCY_SHARD_PREFIX}:reverse_keys"

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


def _cache_set_add(key: str, member: str) -> None:
    members = cache_set_members(key)
    members.add(member)
    cache.set(key, members, None)


def _cache_set_discard(key: str, member: str) -> None:
    members = cache_set_members(key)
    members.discard(member)
    if members:
        cache.set(key, members, None)
    else:
        cache.delete(key)


def _lookup_name_for_candidate(lookup: str) -> str:
    spec = lookup_spec_from_key(lookup)
    return "__".join(spec.attr_path)


def _register_lookup(manager_name: str, action: str, lookup: str) -> None:
    _cache_set_add(lookup_registry_key(manager_name, action), lookup)


def _shard_keys_for_dependency(
    manager_name: str,
    action: DependencyAction,
    identifier: str,
) -> tuple[set[str], set[CompositeDependency], set[SimpleDependency]]:
    shard_keys: set[str] = set()
    composites: set[CompositeDependency] = set()
    simple_dependencies: set[SimpleDependency] = set()

    if action == "all":
        shard_keys.add(all_records_shard_key(manager_name))
        simple_dependencies.add((manager_name, action, identifier))
        return shard_keys, composites, simple_dependencies

    if action == "request_query":
        shard_keys.add(request_query_shard_key(manager_name))
        simple_dependencies.add((manager_name, action, identifier))
        return shard_keys, composites, simple_dependencies

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
        return shard_keys, composites, simple_dependencies

    if action not in {"filter", "exclude"}:
        return shard_keys, composites, simple_dependencies

    params = parse_dependency_identifier(identifier)
    if not isinstance(params, dict):
        return shard_keys, composites, simple_dependencies

    if not params:
        shard_keys.add(all_records_shard_key(manager_name))
        simple_dependencies.add((manager_name, action, identifier))
        return shard_keys, composites, simple_dependencies

    sort_lookups = [
        str(lookup).removeprefix("__sort__")
        for lookup in params
        if str(lookup).startswith("__sort__")
    ]
    if sort_lookups:
        composite = (manager_name, action, identifier)
        composites.add(composite)
        for sort_lookup in sort_lookups:
            _register_lookup(manager_name, action, sort_lookup)
            shard_keys.add(
                composite_lookup_shard_key(manager_name, action, sort_lookup)
            )
        return shard_keys, composites, simple_dependencies

    if len(params) > 1:
        composite = (manager_name, action, identifier)
        composites.add(composite)
        for lookup in params:
            candidate_lookup = _lookup_name_for_candidate(str(lookup))
            _register_lookup(manager_name, action, candidate_lookup)
            shard_keys.add(
                composite_lookup_shard_key(
                    manager_name,
                    action,
                    candidate_lookup,
                )
            )
        return shard_keys, composites, simple_dependencies

    lookup, value = next(iter(params.items()))
    spec = lookup_spec_from_key(str(lookup))
    candidate_lookup = "__".join(spec.attr_path)
    _register_lookup(manager_name, action, candidate_lookup)
    if spec.operator == "eq":
        shard_keys.add(
            exact_lookup_shard_key(manager_name, action, spec.lookup, "eq", value)
        )
    else:
        shard_keys.add(
            scan_lookup_shard_key(manager_name, action, spec.lookup, spec.operator)
        )
    simple_dependencies.add((manager_name, action, identifier))
    return shard_keys, composites, simple_dependencies


def record_cache_dependencies(
    cache_key: str,
    dependencies: Iterable[Dependency],
) -> None:
    """Record dependency metadata for one cache key in deterministic shards."""
    dependency_set = set(dependencies)
    if not dependency_set:
        return

    remove_cache_key_from_shards(cache_key)

    shard_keys: set[str] = set()
    composites: set[CompositeDependency] = set()
    simple_dependencies: set[SimpleDependency] = set()
    for manager_name, action, identifier in dependency_set:
        new_shards, new_composites, new_simple = _shard_keys_for_dependency(
            manager_name,
            action,
            identifier,
        )
        shard_keys.update(new_shards)
        composites.update(new_composites)
        simple_dependencies.update(new_simple)

    for shard_key in shard_keys:
        _cache_set_add(shard_key, cache_key)

    cache.set(
        reverse_membership_key(cache_key),
        ReverseDependencyMembership(
            cache_key=cache_key,
            shard_keys=frozenset(shard_keys),
            composite_dependencies=frozenset(composites),
            simple_dependencies=frozenset(simple_dependencies),
        ),
        None,
    )
    _cache_set_add(REVERSE_MEMBERSHIP_REGISTRY_KEY, reverse_membership_key(cache_key))


def record_many_cache_dependencies(
    entries: Iterable[tuple[str, Iterable[Dependency]]],
) -> None:
    """Record dependency metadata for many cache keys."""
    for cache_key, dependencies in entries:
        record_cache_dependencies(cache_key, dependencies)


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
    old_value: Any = None,
    new_value: Any = None,
) -> set[str]:
    """Return cache keys stored in shards that may be affected by a lookup change."""
    candidates = set()
    candidate_lookup = _lookup_name_for_candidate(lookup)

    for value in (old_value, new_value):
        if value is not None:
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
