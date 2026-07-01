from __future__ import annotations

import pickle
from collections.abc import Iterable, Mapping
from typing import cast
from unittest.mock import patch

from django.test import SimpleTestCase

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_cache import (
    DEPENDENCY_CACHE_ENTRY_VERSION,
    DependencyCacheEntry,
    DependencyCacheHit,
    make_dependency_cache_entry,
    read_dependency_cache_hit,
    read_many_dependency_cache_hits,
    replay_dependency_cache_hit,
)
from general_manager.cache.dependency_index import Dependency


def _trusted_pickle_loads(data: bytes) -> object:
    return pickle.loads(data)  # noqa: S301 - test cache uses controlled data


class PickleCache:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.get_calls: list[str] = []
        self.get_many_calls: list[tuple[str, ...]] = []

    def get(self, key: str, default: object = None) -> object:
        self.get_calls.append(key)
        cached_value = self.store.get(key, default)
        if cached_value is not default:
            return _trusted_pickle_loads(cast(bytes, cached_value))
        return default

    def set(self, key: str, value: object, timeout: int | None = None) -> None:
        del timeout
        self.store[key] = pickle.dumps(value)

    def get_many(self, keys: Iterable[str]) -> Mapping[str, object]:
        key_tuple = tuple(keys)
        self.get_many_calls.append(key_tuple)
        return {
            key: _trusted_pickle_loads(self.store[key])
            for key in key_tuple
            if key in self.store
        }


class PickleCacheWithoutGetMany:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.get_calls: list[str] = []

    def get(self, key: str, default: object = None) -> object:
        self.get_calls.append(key)
        cached_value = self.store.get(key, default)
        if cached_value is not default:
            return _trusted_pickle_loads(cast(bytes, cached_value))
        return default

    def set(self, key: str, value: object, timeout: int | None = None) -> None:
        del timeout
        self.store[key] = pickle.dumps(value)


class DependencyCacheEntryTests(SimpleTestCase):
    def setUp(self) -> None:
        DependencyTracker.reset_thread_local_storage()

    def tearDown(self) -> None:
        DependencyTracker.reset_thread_local_storage()

    def test_make_dependency_cache_entry_tags_payload_version(self) -> None:
        dependencies: set[Dependency] = {("Project", "identification", '{"id": 1}')}

        entry = make_dependency_cache_entry({"status": "ready"}, dependencies)

        self.assertEqual(entry.version, DEPENDENCY_CACHE_ENTRY_VERSION)
        self.assertEqual(entry.value, {"status": "ready"})
        self.assertEqual(entry.dependencies, frozenset(dependencies))

    def test_reads_falsey_combined_values_as_hits(self) -> None:
        values: list[object] = [None, False, 0, [], {}]
        dependencies: set[Dependency] = {("Project", "identification", '{"id": 1}')}

        for index, value in enumerate(values):
            with self.subTest(value=value):
                cache_backend = PickleCache()
                cache_key = f"cache-{index}"
                cache_backend.set(
                    cache_key,
                    make_dependency_cache_entry(value, dependencies),
                    None,
                )

                marker = object()
                hit = read_dependency_cache_hit(
                    cache_backend,
                    cache_key,
                    sentinel=marker,
                )

                self.assertIsInstance(hit, DependencyCacheHit)
                assert isinstance(hit, DependencyCacheHit)
                self.assertEqual(hit.value, value)
                self.assertEqual(hit.dependencies, frozenset(dependencies))

    def test_missing_key_returns_sentinel(self) -> None:
        cache_backend = PickleCache()
        marker = object()

        self.assertIs(
            read_dependency_cache_hit(cache_backend, "missing", sentinel=marker),
            marker,
        )

    def test_legacy_split_value_and_dependencies_read_correctly(self) -> None:
        cache_backend = PickleCache()
        dependencies: set[Dependency] = {("Project", "identification", '{"id": 7}')}
        cache_backend.set("cache-a", False, None)
        cache_backend.set("cache-a:deps", dependencies, None)

        hit = read_dependency_cache_hit(cache_backend, "cache-a")

        self.assertIsInstance(hit, DependencyCacheHit)
        assert isinstance(hit, DependencyCacheHit)
        self.assertIs(hit.value, False)
        self.assertEqual(hit.dependencies, frozenset(dependencies))

    def test_legacy_value_without_deps_is_still_a_hit_with_empty_dependencies(
        self,
    ) -> None:
        cache_backend = PickleCache()
        cache_backend.set("cache-a", 0, None)

        hit = read_dependency_cache_hit(cache_backend, "cache-a")

        self.assertIsInstance(hit, DependencyCacheHit)
        assert isinstance(hit, DependencyCacheHit)
        self.assertEqual(hit.value, 0)
        self.assertEqual(hit.dependencies, frozenset())

    def test_malformed_legacy_dependencies_are_treated_as_cache_miss(self) -> None:
        cache_backend = PickleCache()
        marker = object()
        cache_backend.set("cache-a", "value", None)
        cache_backend.set("cache-a:deps", "not dependency tuples", None)

        self.assertIs(
            read_dependency_cache_hit(cache_backend, "cache-a", sentinel=marker),
            marker,
        )

    def test_plain_dict_value_is_not_misclassified_as_combined_payload(self) -> None:
        cache_backend = PickleCache()
        value = {
            "version": DEPENDENCY_CACHE_ENTRY_VERSION,
            "value": "not framework metadata",
            "dependencies": [],
        }
        dependencies: set[Dependency] = {("Project", "identification", '{"id": 3}')}
        cache_backend.set("cache-a", value, None)
        cache_backend.set("cache-a:deps", dependencies, None)

        hit = read_dependency_cache_hit(cache_backend, "cache-a")

        self.assertIsInstance(hit, DependencyCacheHit)
        assert isinstance(hit, DependencyCacheHit)
        self.assertEqual(hit.value, value)
        self.assertEqual(hit.dependencies, frozenset(dependencies))

    def test_unknown_combined_payload_version_is_treated_as_miss(self) -> None:
        cache_backend = PickleCache()
        cache_backend.set(
            "cache-a",
            DependencyCacheEntry(version=999, value="future", dependencies=frozenset()),
            None,
        )
        marker = object()

        self.assertIs(
            read_dependency_cache_hit(cache_backend, "cache-a", sentinel=marker),
            marker,
        )

    def test_current_combined_payload_reads_dependencies_without_legacy_validation(
        self,
    ) -> None:
        cache_backend = PickleCache()
        dependencies: set[Dependency] = {("Project", "identification", '{"id": 1}')}
        cache_backend.set(
            "cache-a",
            make_dependency_cache_entry("ready", dependencies),
            None,
        )

        with patch(
            "general_manager.cache.dependency_cache._legacy_dependency_set",
            side_effect=AssertionError("legacy validation should not run"),
        ):
            hit = read_dependency_cache_hit(cache_backend, "cache-a")

        self.assertIsInstance(hit, DependencyCacheHit)
        assert isinstance(hit, DependencyCacheHit)
        self.assertEqual(hit.value, "ready")
        self.assertEqual(hit.dependencies, frozenset(dependencies))

    def test_legacy_combined_payload_still_rejects_malformed_dependencies(
        self,
    ) -> None:
        cache_backend = PickleCache()
        marker = object()
        cache_backend.set(
            "cache-a",
            DependencyCacheEntry(
                version=1,
                value="legacy",
                dependencies=frozenset({("Project", "bad-action", '{"id": 1}')}),  # type: ignore[arg-type]
            ),
            None,
        )

        self.assertIs(
            read_dependency_cache_hit(cache_backend, "cache-a", sentinel=marker),
            marker,
        )

    def test_bulk_read_uses_one_get_many_for_combined_entries(self) -> None:
        cache_backend = PickleCache()
        dependencies: set[Dependency] = {("Project", "identification", '{"id": 1}')}
        for index in range(5):
            cache_backend.set(
                f"cache-{index}",
                make_dependency_cache_entry(index, dependencies),
                None,
            )

        hits = read_many_dependency_cache_hits(
            cache_backend,
            [f"cache-{index}" for index in range(5)],
        )

        self.assertEqual(set(hits), {f"cache-{index}" for index in range(5)})
        self.assertEqual([hit.value for hit in hits.values()], [0, 1, 2, 3, 4])
        self.assertEqual(
            cache_backend.get_many_calls,
            [("cache-0", "cache-1", "cache-2", "cache-3", "cache-4")],
        )
        self.assertEqual(cache_backend.get_calls, [])

    def test_bulk_read_fetches_legacy_dependencies_in_one_extra_get_many(self) -> None:
        cache_backend = PickleCache()
        for index in range(3):
            cache_backend.set(f"cache-{index}", index, None)
            cache_backend.set(
                f"cache-{index}:deps",
                {("Project", "identification", f'{{"id": {index}}}')},
                None,
            )

        hits = read_many_dependency_cache_hits(
            cache_backend,
            ["cache-0", "cache-1", "cache-2"],
        )

        self.assertEqual(
            [hits[f"cache-{index}"].value for index in range(3)], [0, 1, 2]
        )
        self.assertEqual(
            cache_backend.get_many_calls,
            [
                ("cache-0", "cache-1", "cache-2"),
                ("cache-0:deps", "cache-1:deps", "cache-2:deps"),
            ],
        )
        self.assertEqual(cache_backend.get_calls, [])

    def test_bulk_read_omits_malformed_legacy_dependencies(self) -> None:
        cache_backend = PickleCache()
        cache_backend.set("cache-a", "value", None)
        cache_backend.set("cache-a:deps", {"not": "dependency tuples"}, None)

        self.assertEqual(
            read_many_dependency_cache_hits(cache_backend, ["cache-a"]), {}
        )

    def test_bulk_read_falls_back_to_single_reads_without_get_many(self) -> None:
        cache_backend = PickleCacheWithoutGetMany()
        dependencies: set[Dependency] = {("Project", "identification", '{"id": 1}')}
        for index in range(2):
            cache_backend.set(
                f"cache-{index}",
                make_dependency_cache_entry(index, dependencies),
                None,
            )

        hits = read_many_dependency_cache_hits(cache_backend, ["cache-0", "cache-1"])

        self.assertEqual([hits["cache-0"].value, hits["cache-1"].value], [0, 1])
        self.assertEqual(cache_backend.get_calls, ["cache-0", "cache-1"])

    def test_replay_dependency_cache_hit_tracks_dependencies(self) -> None:
        hit = DependencyCacheHit(
            value="ready",
            dependencies=frozenset(
                {
                    ("Project", "identification", '{"id": 1}'),
                    ("User", "all", ""),
                }
            ),
        )

        with DependencyTracker() as dependencies:
            replay_dependency_cache_hit(hit)

        self.assertEqual(dependencies, set(hit.dependencies))
