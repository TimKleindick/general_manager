from __future__ import annotations

import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any
from unittest import mock

from django.test import TestCase, override_settings

from general_manager.cache.dependency_matching import stable_value_hash
from general_manager.cache.dependency_shards import (
    ALL_RECORDS_VALUE,
    DEPENDENCY_SHARD_PREFIX,
    REVERSE_MEMBERSHIP_REGISTRY_KEY,
    ReverseDependencyMembership,
    all_records_shard_key,
    cache_set_members,
    candidate_cache_keys_for_lookup,
    clear_legacy_dependency_index,
    composite_lookup_shard_key,
    exact_lookup_shard_key,
    record_cache_dependencies,
    record_many_cache_dependencies,
    remove_cache_key_from_shards,
    request_query_shard_key,
    reverse_membership_key,
    scan_lookup_shard_key,
)
from general_manager.cache.dependency_index import cache
from general_manager.cache.dependency_index import (
    capture_old_values,
    generic_cache_invalidation,
    record_dependencies,
)


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-dependency-shards",
    }
}


class CountingShardCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.get_calls: list[str] = []
        self.get_many_calls: list[tuple[str, ...]] = []
        self.set_calls: list[str] = []
        self.set_many_calls: list[tuple[str, ...]] = []
        self.delete_calls: list[str] = []
        self.delete_many_calls: list[tuple[str, ...]] = []

    def _clone(self, value: Any) -> Any:
        if isinstance(value, set):
            return set(value)
        if isinstance(value, frozenset):
            return frozenset(value)
        return value

    def get(self, key: str, default: Any = None) -> Any:
        self.get_calls.append(key)
        if key in self.store:
            return self._clone(self.store[key])
        return default

    def get_many(self, keys: Any) -> dict[str, Any]:
        key_tuple = tuple(keys)
        self.get_many_calls.append(key_tuple)
        return {
            key: self._clone(self.store[key]) for key in key_tuple if key in self.store
        }

    def set(self, key: str, value: Any, timeout: int | None = None) -> None:
        self.set_calls.append(key)
        self.store[key] = self._clone(value)

    def set_many(
        self,
        data: Mapping[str, Any],
        timeout: int | None = None,
    ) -> list[str]:
        payload = dict(data)
        self.set_many_calls.append(tuple(payload))
        for key, value in payload.items():
            self.store[key] = self._clone(value)
        return []

    def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        self.store.pop(key, None)

    def delete_many(self, keys: Any) -> None:
        key_tuple = tuple(keys)
        self.delete_many_calls.append(key_tuple)
        for key in key_tuple:
            self.store.pop(key, None)


@override_settings(CACHES=TEST_CACHES)
class DependencyShardKeyTests(TestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_shard_keys_are_deterministic_and_namespaced(self) -> None:
        value_hash = stable_value_hash("open")

        assert exact_lookup_shard_key("Project", "filter", "status", "eq", "open") == (
            f"{DEPENDENCY_SHARD_PREFIX}:lookup:Project:filter:status:eq:{value_hash}"
        )
        assert scan_lookup_shard_key("Project", "filter", "status__in", "in") == (
            f"{DEPENDENCY_SHARD_PREFIX}:scan:Project:filter:status__in:in"
        )
        assert all_records_shard_key("Project") == (
            f"{DEPENDENCY_SHARD_PREFIX}:all:Project"
        )
        assert request_query_shard_key("RemoteProject") == (
            f"{DEPENDENCY_SHARD_PREFIX}:request_query:RemoteProject"
        )

    def test_record_cache_dependencies_writes_shards_and_reverse_membership(
        self,
    ) -> None:
        record_cache_dependencies(
            "cache-a",
            [
                ("Project", "filter", json.dumps({"status": "open"})),
                ("Project", "filter", json.dumps({"priority__gte": 3})),
                ("RemoteProject", "request_query", json.dumps({"operation": "list"})),
                ("Project", "all", ""),
            ],
        )

        exact_key = exact_lookup_shard_key("Project", "filter", "status", "eq", "open")
        scan_key = scan_lookup_shard_key("Project", "filter", "priority__gte", "gte")
        request_key = request_query_shard_key("RemoteProject")
        all_key = all_records_shard_key("Project")

        assert cache_set_members(exact_key) == {"cache-a"}
        assert cache_set_members(scan_key) == {"cache-a"}
        assert cache_set_members(request_key) == {"cache-a"}
        assert cache_set_members(all_key) == {"cache-a"}

        reverse = cache.get(reverse_membership_key("cache-a"))
        assert reverse == ReverseDependencyMembership(
            cache_key="cache-a",
            shard_keys=frozenset({exact_key, scan_key, request_key, all_key}),
            composite_dependencies=frozenset(),
            simple_dependencies=frozenset(
                {
                    ("Project", "filter", json.dumps({"status": "open"})),
                    ("Project", "filter", json.dumps({"priority__gte": 3})),
                    (
                        "RemoteProject",
                        "request_query",
                        json.dumps({"operation": "list"}),
                    ),
                    ("Project", "all", ""),
                }
            ),
        )

    def test_record_composite_dependencies_use_candidate_shards(self) -> None:
        identifier = json.dumps({"status": "open", "priority__gte": 3})

        record_cache_dependencies("cache-combo", [("Project", "filter", identifier)])

        candidate_keys = candidate_cache_keys_for_lookup("Project", "filter", "status")

        assert candidate_keys == {"cache-combo"}
        reverse = cache.get(reverse_membership_key("cache-combo"))
        assert reverse.composite_dependencies == frozenset(
            {("Project", "filter", identifier)}
        )

    def test_remove_cache_key_uses_reverse_membership_without_scanning(self) -> None:
        record_cache_dependencies(
            "cache-a",
            [("Project", "filter", json.dumps({"status": "open"}))],
        )
        shard_key = exact_lookup_shard_key("Project", "filter", "status", "eq", "open")

        remove_cache_key_from_shards("cache-a")

        assert cache_set_members(shard_key) == set()
        assert cache.get(reverse_membership_key("cache-a")) is None

    def test_empty_filter_dependency_tracks_all_records_shard(self) -> None:
        record_cache_dependencies("cache-all-filter", [("Project", "filter", "{}")])

        assert cache_set_members(all_records_shard_key("Project")) == {
            "cache-all-filter"
        }

    def test_candidate_lookup_combines_exact_scan_and_composite_shards(self) -> None:
        record_cache_dependencies(
            "cache-exact",
            [("Project", "filter", json.dumps({"status": "open"}))],
        )
        record_cache_dependencies(
            "cache-scan",
            [("Project", "filter", json.dumps({"status__in": ["open", "closed"]}))],
        )
        record_cache_dependencies(
            "cache-combo",
            [("Project", "filter", json.dumps({"status": "open", "priority": 3}))],
        )

        assert candidate_cache_keys_for_lookup(
            "Project",
            "filter",
            "status",
            old_value="closed",
            new_value="open",
        ) == {"cache-exact", "cache-scan", "cache-combo"}

    def test_unknown_cache_key_cleanup_is_noop(self) -> None:
        remove_cache_key_from_shards("missing")

        assert cache.get(reverse_membership_key("missing")) is None

    def test_all_records_value_constant_is_compatible_with_existing_index_name(
        self,
    ) -> None:
        assert ALL_RECORDS_VALUE == "__all__"

    def test_cache_set_members_treats_none_as_empty_set(self) -> None:
        cache.set("empty-set-key", None, None)

        assert cache_set_members("empty-set-key") == set()

    def test_clear_legacy_dependency_index_removes_known_legacy_cache_keys(
        self,
    ) -> None:
        legacy_index = {
            "all": {"Project": {"all-cache", 123}},
            "request_query": {
                "BadRemote": "not-a-query-section",
                "RemoteProject": {"query": {"request-cache"}},
            },
            "filter": {
                "Project": {
                    "__cache_dependencies__": {"composite-cache": {"identifier"}},
                    "status": {"not-a-cache-set": "not-a-member-collection"},
                }
            },
            "exclude": {
                "BadProject": "not-a-model-section",
            },
        }
        cache.set("dependency_index", legacy_index, None)
        for cache_key in ("all-cache", "request-cache", "composite-cache"):
            cache.set(cache_key, "cached-value", None)

        assert clear_legacy_dependency_index() == {
            "all-cache",
            "request-cache",
            "composite-cache",
        }
        assert cache.get("dependency_index") is None
        assert cache.get("all-cache") is None
        assert cache.get("request-cache") is None
        assert cache.get("composite-cache") is None

    def test_clear_legacy_dependency_index_deletes_malformed_index(self) -> None:
        cache.set("dependency_index", "not-an-index", None)

        assert clear_legacy_dependency_index() == set()
        assert cache.get("dependency_index") is None

        cache.set("dependency_index", {"filter": "not-an-action-section"}, None)

        assert clear_legacy_dependency_index() == set()
        assert cache.get("dependency_index") is None

    def test_invalid_dependencies_write_empty_reverse_membership(self) -> None:
        record_cache_dependencies(
            "cache-invalid",
            [
                ("Project", "unknown", ""),  # type: ignore[list-item]
                ("Project", "filter", "{bad"),
            ],
        )

        reverse = cache.get(reverse_membership_key("cache-invalid"))
        assert reverse == ReverseDependencyMembership(
            cache_key="cache-invalid",
            shard_keys=frozenset(),
            composite_dependencies=frozenset(),
            simple_dependencies=frozenset(),
        )

    def test_sort_dependencies_use_composite_lookup_shards(self) -> None:
        identifier = json.dumps(
            {
                "__sort__rank": {
                    "filters": {"status": "open"},
                    "excludes": {},
                    "reverse": False,
                }
            }
        )

        record_cache_dependencies("cache-sort", [("Project", "filter", identifier)])

        sort_shard_key = composite_lookup_shard_key("Project", "filter", "rank")
        assert cache_set_members(sort_shard_key) == {"cache-sort"}
        assert candidate_cache_keys_for_lookup("Project", "filter", "rank") == {
            "cache-sort"
        }
        reverse = cache.get(reverse_membership_key("cache-sort"))
        assert reverse.composite_dependencies == frozenset(
            {("Project", "filter", identifier)}
        )

    def test_record_many_cache_dependencies_batches_shared_shard_writes(
        self,
    ) -> None:
        counting_cache = CountingShardCache()
        dependencies = {
            ("Project", "filter", json.dumps({"status": "open"})),
            ("Project", "filter", json.dumps({"priority": 3})),
            ("Project", "filter", json.dumps({"owner": "team-a"})),
            ("Project", "filter", json.dumps({"region": "emea"})),
            ("Project", "filter", json.dumps({"stage": "draft"})),
        }
        entries = [(f"cache-{index}", dependencies) for index in range(25)]

        with mock.patch(
            "general_manager.cache.dependency_shards.cache",
            counting_cache,
        ):
            record_many_cache_dependencies(entries)

        cache_keys = {f"cache-{index}" for index in range(25)}
        status_shard = exact_lookup_shard_key(
            "Project",
            "filter",
            "status",
            "eq",
            "open",
        )
        priority_shard = exact_lookup_shard_key(
            "Project",
            "filter",
            "priority",
            "eq",
            3,
        )

        assert counting_cache.store[status_shard] == cache_keys
        assert counting_cache.store[priority_shard] == cache_keys

        reverse = counting_cache.store[reverse_membership_key("cache-0")]
        assert isinstance(reverse, ReverseDependencyMembership)
        assert reverse.cache_key == "cache-0"
        assert status_shard in reverse.shard_keys
        assert priority_shard in reverse.shard_keys

        assert counting_cache.store[REVERSE_MEMBERSHIP_REGISTRY_KEY] == {
            reverse_membership_key(f"cache-{index}") for index in range(25)
        }
        assert counting_cache.get_calls == ["dependency_index"]
        assert counting_cache.set_calls == []
        assert len(counting_cache.get_many_calls) <= 2
        assert len(counting_cache.set_many_calls) <= 2


@override_settings(CACHES=TEST_CACHES)
class DependencyIndexShardFacadeTests(TestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_record_dependencies_does_not_create_full_dependency_index(self) -> None:
        record_dependencies(
            "cache-a",
            [("Project", "filter", json.dumps({"status": "open"}))],
        )

        assert cache.get("dependency_index") is None
        assert cache_set_members(
            exact_lookup_shard_key("Project", "filter", "status", "eq", "open")
        ) == {"cache-a"}

    def test_capture_old_values_uses_sharded_lookup_registry(self) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [("Project", "filter", json.dumps({"status": "open"}))],
        )
        instance = SimpleNamespace(status="open", identification=1)

        capture_old_values(sender=Project, instance=instance)

        assert instance._old_values == {"status": "open"}

    def test_generic_cache_invalidation_uses_sharded_candidates(self) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [("Project", "filter", json.dumps({"status": "open"}))],
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="closed"),
            old_relevant_values={"status": "open"},
        )

        assert cache.get("cache-a") is None
        assert (
            cache_set_members(
                exact_lookup_shard_key("Project", "filter", "status", "eq", "open")
            )
            == set()
        )

    def test_generic_cache_invalidation_invalidates_matching_filter_when_lookup_unchanged(
        self,
    ) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [("Project", "filter", json.dumps({"status": "open"}))],
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="open", title="renamed"),
            old_relevant_values={"status": "open"},
        )

        assert cache.get("cache-a") is None
        assert (
            cache_set_members(
                exact_lookup_shard_key("Project", "filter", "status", "eq", "open")
            )
            == set()
        )

    def test_generic_cache_invalidation_invalidates_exact_none_lookup(self) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [("Project", "filter", json.dumps({"status": None}))],
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="open"),
            old_relevant_values={"status": None},
        )

        assert cache.get("cache-a") is None
        assert (
            cache_set_members(
                exact_lookup_shard_key("Project", "filter", "status", "eq", None)
            )
            == set()
        )

    def test_record_dependencies_clears_legacy_index_and_cached_values(self) -> None:
        cache.set(
            "dependency_index",
            {
                "filter": {"Project": {"status": {'"open"': {"legacy-cache"}}}},
                "exclude": {},
                "request_query": {},
                "all": {},
            },
            None,
        )
        cache.set("legacy-cache", "legacy-value", None)

        record_dependencies(
            "cache-a",
            [("Project", "filter", json.dumps({"status": "open"}))],
        )

        assert cache.get("dependency_index") is None
        assert cache.get("legacy-cache") is None
        assert cache_set_members(
            exact_lookup_shard_key("Project", "filter", "status", "eq", "open")
        ) == {"cache-a"}

    def test_generic_cache_invalidation_invalidates_identification_dependency(
        self,
    ) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [("Project", "identification", json.dumps({"id": 1}))],
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(identification={"id": 1}),
            old_relevant_values={},
        )

        assert cache.get("cache-a") is None

    def test_generic_cache_invalidation_ignores_identification_mismatch(self) -> None:
        class Project:
            pass

        cache_key = "cache-a"
        identification = {"id": 2}
        record_dependencies(
            cache_key,
            [("Project", "filter", json.dumps({"status": "open"}))],
        )
        cache.set(
            reverse_membership_key(cache_key),
            ReverseDependencyMembership(
                cache_key=cache_key,
                shard_keys=frozenset(),
                composite_dependencies=frozenset(),
                simple_dependencies=frozenset(
                    {("Project", "identification", json.dumps({"id": 1}))}
                ),
            ),
            None,
        )
        cache.set(cache_key, "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(identification=identification, status="closed"),
            old_relevant_values={"status": "open"},
        )

        assert cache.get(cache_key) == "cached-value"

    def test_generic_cache_invalidation_uses_request_query_match_when_candidate(
        self,
    ) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [
                ("Project", "request_query", json.dumps({"operation": "list"})),
                ("Project", "filter", json.dumps({"status": "open"})),
            ],
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="closed"),
            old_relevant_values={"status": "open"},
        )

        assert cache.get("cache-a") is None

    def test_generic_cache_invalidation_accepts_request_query_reverse_match(
        self,
    ) -> None:
        class Project:
            pass

        cache_key = "cache-a"
        record_dependencies(
            "registry-seed",
            [("Project", "filter", json.dumps({"status": "seed"}))],
        )
        cache.set(
            exact_lookup_shard_key("Project", "filter", "status", "eq", "open"),
            {cache_key},
            None,
        )
        cache.set(
            reverse_membership_key(cache_key),
            ReverseDependencyMembership(
                cache_key=cache_key,
                shard_keys=frozenset(),
                composite_dependencies=frozenset(),
                simple_dependencies=frozenset(
                    {("Project", "request_query", json.dumps({"operation": "list"}))}
                ),
            ),
            None,
        )
        cache.set(cache_key, "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="closed"),
            old_relevant_values={"status": "open"},
        )

        assert cache.get(cache_key) is None

    def test_generic_cache_invalidation_invalidates_candidate_without_reverse_metadata(
        self,
    ) -> None:
        class Project:
            pass

        record_dependencies(
            "registry-seed",
            [("Project", "filter", json.dumps({"status": "seed"}))],
        )
        cache.set(
            exact_lookup_shard_key("Project", "filter", "status", "eq", "open"),
            {"cache-a"},
            None,
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="closed"),
            old_relevant_values={"status": "open"},
        )

        assert cache.get("cache-a") is None

    def test_generic_cache_invalidation_ignores_malformed_reverse_identifier(
        self,
    ) -> None:
        class Project:
            pass

        cache_key = "cache-a"
        record_dependencies(
            "registry-seed",
            [("Project", "filter", json.dumps({"status": "seed"}))],
        )
        cache.set(
            exact_lookup_shard_key("Project", "filter", "status", "eq", "open"),
            {cache_key},
            None,
        )
        cache.set(
            reverse_membership_key(cache_key),
            ReverseDependencyMembership(
                cache_key=cache_key,
                shard_keys=frozenset(),
                composite_dependencies=frozenset(),
                simple_dependencies=frozenset({("Project", "filter", "{bad")}),
            ),
            None,
        )
        cache.set(cache_key, "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="closed"),
            old_relevant_values={"status": "open"},
        )

        assert cache.get(cache_key) == "cached-value"

    def test_generic_cache_invalidation_ignores_invalid_reverse_dependencies(
        self,
    ) -> None:
        class Project:
            pass

        cache_key = "cache-a"
        record_dependencies(
            "registry-seed",
            [("Project", "filter", json.dumps({"status": "seed"}))],
        )
        cache.set(
            exact_lookup_shard_key("Project", "filter", "status", "eq", "open"),
            {cache_key},
            None,
        )
        cache.set(
            reverse_membership_key(cache_key),
            ReverseDependencyMembership(
                cache_key=cache_key,
                shard_keys=frozenset(),
                composite_dependencies=frozenset(),
                simple_dependencies=frozenset(
                    {
                        ("Project", "unknown", ""),  # type: ignore[arg-type]
                        ("Project", "filter", "{bad"),
                        ("Project", "filter", "{}"),
                    }
                ),
            ),
            None,
        )
        cache.set(cache_key, "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="closed"),
            old_relevant_values={"status": "open"},
        )

        assert cache.get(cache_key) is None

    def test_generic_cache_invalidation_ignores_unknown_reverse_action(self) -> None:
        class Project:
            pass

        cache_key = "cache-a"
        record_dependencies(
            "registry-seed",
            [("Project", "filter", json.dumps({"status": "seed"}))],
        )
        cache.set(
            exact_lookup_shard_key("Project", "filter", "status", "eq", "open"),
            {cache_key},
            None,
        )
        cache.set(
            reverse_membership_key(cache_key),
            ReverseDependencyMembership(
                cache_key=cache_key,
                shard_keys=frozenset(),
                composite_dependencies=frozenset(),
                simple_dependencies=frozenset(
                    {("Project", "unknown", "")}  # type: ignore[arg-type]
                ),
            ),
            None,
        )
        cache.set(cache_key, "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="closed"),
            old_relevant_values={"status": "open"},
        )

        assert cache.get(cache_key) == "cached-value"

    def test_generic_cache_invalidation_respects_unchanged_exclude_dependency(
        self,
    ) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [("Project", "exclude", json.dumps({"status": "archived"}))],
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="archived"),
            old_relevant_values={"status": "archived"},
        )

        assert cache.get("cache-a") == "cached-value"

    def test_generic_cache_invalidation_evaluates_composite_dependencies(self) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [
                (
                    "Project",
                    "filter",
                    json.dumps({"status": "open", "priority__gte": 3}),
                )
            ],
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="open", priority=3),
            old_relevant_values={"status": "closed", "priority": 3},
        )

        assert cache.get("cache-a") is None

    def test_generic_cache_invalidation_skips_non_matching_composite_dependency(
        self,
    ) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [
                (
                    "Project",
                    "filter",
                    json.dumps({"status": "open", "priority__gte": 3}),
                )
            ],
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="closed", priority=3),
            old_relevant_values={"status": "closed", "priority": 3},
        )

        assert cache.get("cache-a") == "cached-value"

    def test_generic_cache_invalidation_evaluates_sort_bucket_dependencies(
        self,
    ) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [
                (
                    "Project",
                    "filter",
                    json.dumps(
                        {
                            "__sort__rank": {
                                "filters": {"status": "open"},
                                "excludes": {"priority__lt": 0},
                                "reverse": False,
                            }
                        }
                    ),
                )
            ],
        )
        cache.set("cache-a", "cached-value", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(status="open", priority=2, rank=5),
            old_relevant_values={"status": "open", "priority": 2, "rank": 10},
        )

        assert cache.get("cache-a") is None

    def test_generic_cache_invalidation_skips_malformed_sort_bucket_dependencies(
        self,
    ) -> None:
        class Project:
            pass

        record_dependencies(
            "cache-a",
            [
                (
                    "Project",
                    "filter",
                    json.dumps({"__sort__rank": "not-a-payload"}),
                )
            ],
        )
        record_dependencies(
            "cache-b",
            [
                (
                    "Project",
                    "filter",
                    json.dumps(
                        {
                            "__sort__rank": {
                                "filters": "not-a-filter-map",
                                "excludes": {},
                            }
                        }
                    ),
                )
            ],
        )
        cache.set("cache-a", "cached-value-a", None)
        cache.set("cache-b", "cached-value-b", None)

        generic_cache_invalidation(
            sender=Project,
            instance=SimpleNamespace(rank=2),
            old_relevant_values={"rank": 1},
        )

        assert cache.get("cache-a") == "cached-value-a"
        assert cache.get("cache-b") == "cached-value-b"
