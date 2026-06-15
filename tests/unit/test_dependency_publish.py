from __future__ import annotations

from collections.abc import Mapping
import pickle
from typing import Any
from unittest import mock

from django.test import SimpleTestCase, override_settings

from general_manager.cache import dependency_publish
from general_manager.cache.dependency_cache import (
    DependencyCacheEntry,
    DependencyCacheHit,
)
from general_manager.cache.dependency_index import (
    begin_dependency_data_change,
    end_dependency_data_change,
    get_dependency_generation,
)
from general_manager.cache.dependency_publish import (
    CacheComputeLease,
    CachePublishAborted,
    PendingDependencyCachePublication,
    acquire_compute_lease,
    coordination_cache,
    publish_dependency_cache_entries,
    publish_dependency_cache_entry,
    release_compute_lease,
    wait_for_cached_dependency_hit,
    _compute_lock_key,
)


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-dependency-publish",
    }
}


class FakeDependencyCacheBackend:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.timeouts: dict[str, int | None] = {}
        self.set_order: list[str] = []

    def get(self, key: str, default: Any = None) -> Any:
        cached_value = self.store.get(key, default)
        if cached_value is not default:
            return pickle.loads(cached_value)  # noqa: S301
        return default

    def set(self, key: str, value: Any, timeout: int | None = None) -> None:
        self.store[key] = pickle.dumps(value)
        self.timeouts[key] = timeout
        self.set_order.append(key)


class FakeDependencyCacheSetManyBackend(FakeDependencyCacheBackend):
    def __init__(self) -> None:
        super().__init__()
        self.set_many_calls: list[tuple[dict[str, Any], int | None]] = []

    def set_many(
        self,
        data: Mapping[str, Any],
        timeout: int | None = None,
    ) -> None:
        payloads = dict(data)
        self.set_many_calls.append((payloads, timeout))
        for key, value in payloads.items():
            self.set(key, value, timeout)


@override_settings(CACHES=TEST_CACHES)
class TestDependencyPublish(SimpleTestCase):
    def setUp(self) -> None:
        coordination_cache.clear()

    def tearDown(self) -> None:
        coordination_cache.clear()

    def make_pending_publication(
        self,
        *,
        cache_key: str,
        result: Any,
        dependencies: set[tuple[str, str, str]],
        cache_backend: FakeDependencyCacheBackend,
        started_generation: int | None = None,
    ) -> PendingDependencyCachePublication:
        return PendingDependencyCachePublication(
            cache_key=cache_key,
            result=result,
            dependencies=frozenset(dependencies),
            cache_backend=cache_backend,
            timeout=None,
            started_generation=(
                get_dependency_generation()
                if started_generation is None
                else started_generation
            ),
            lease=CacheComputeLease(
                key=_compute_lock_key(cache_key),
                token=f"lease-{cache_key}",
            ),
        )

    def test_acquire_compute_lease_allows_one_active_lease_per_key(self) -> None:
        lease = acquire_compute_lease("cache-a")

        self.assertIsInstance(lease, CacheComputeLease)
        assert lease is not None
        self.assertEqual(lease.key, _compute_lock_key("cache-a"))
        self.assertEqual(coordination_cache.get(lease.key), lease.token)
        self.assertIsNone(acquire_compute_lease("cache-a"))

    def test_release_compute_lease_removes_owned_token_for_immediate_reuse(
        self,
    ) -> None:
        lease = acquire_compute_lease("cache-a")
        assert lease is not None

        release_compute_lease(lease)

        self.assertIsNone(coordination_cache.get(lease.key))
        next_lease = acquire_compute_lease("cache-a")
        self.assertIsInstance(next_lease, CacheComputeLease)
        assert next_lease is not None
        self.assertNotEqual(next_lease.token, lease.token)

    def test_release_compute_lease_never_removes_new_owner_token(self) -> None:
        lease = acquire_compute_lease("cache-a")
        assert lease is not None
        coordination_cache.set(lease.key, "other-token", None)

        release_compute_lease(lease)

        self.assertEqual(coordination_cache.get(lease.key), "other-token")
        self.assertIsNone(acquire_compute_lease("cache-a"))

    def test_publish_records_dependencies_before_combined_entry(self) -> None:
        cache_backend = FakeDependencyCacheBackend()
        cache_key = "cache-a"
        dependencies = {
            ("Project", "filter", '{"name": "alpha"}'),
            ("Project", "identification", "1"),
        }
        events: list[str] = []
        recorded_entries: list[tuple[str, set[tuple[str, str, str]]]] = []

        def record_many_fn(entries: Any) -> None:
            for key, dependency_set in entries:
                recorded_entries.append((key, set(dependency_set)))
            events.append("record")

        original_set = cache_backend.set

        def record_set_order(key: str, value: Any, timeout: int | None = None) -> None:
            events.append(f"set:{key}")
            original_set(key, value, timeout)

        cache_backend.set = record_set_order  # type: ignore[method-assign]

        publish_dependency_cache_entry(
            cache_key=cache_key,
            result={"status": "ready"},
            dependencies=dependencies,
            cache_backend=cache_backend,
            timeout=30,
            started_generation=get_dependency_generation(),
            record_many_fn=record_many_fn,
        )

        self.assertEqual(events, ["record", f"set:{cache_key}"])
        self.assertEqual(recorded_entries, [(cache_key, dependencies)])
        payload = cache_backend.get(cache_key)
        self.assertIsInstance(payload, DependencyCacheEntry)
        assert isinstance(payload, DependencyCacheEntry)
        self.assertEqual(payload.value, {"status": "ready"})
        self.assertEqual(payload.dependencies, frozenset(dependencies))
        self.assertEqual(cache_backend.timeouts[cache_key], 30)
        self.assertNotIn(f"{cache_key}:deps", cache_backend.store)

    def test_batch_publish_records_index_once_before_bulk_value_write(self) -> None:
        cache_backend = FakeDependencyCacheSetManyBackend()
        events: list[str] = []
        original_set_full_index = dependency_publish.set_full_index

        def record_set_full_index(idx: Any) -> None:
            events.append("index")
            original_set_full_index(idx)

        original_set_many = cache_backend.set_many

        def record_set_many(
            data: Mapping[str, Any],
            timeout: int | None = None,
        ) -> None:
            events.append("set_many")
            original_set_many(data, timeout)

        cache_backend.set_many = record_set_many  # type: ignore[method-assign]

        with mock.patch(
            "general_manager.cache.dependency_publish.set_full_index",
            side_effect=record_set_full_index,
        ):
            publish_dependency_cache_entries(
                [
                    self.make_pending_publication(
                        cache_key="cache-a",
                        result="alpha",
                        dependencies={("Project", "identification", "1")},
                        cache_backend=cache_backend,
                    ),
                    self.make_pending_publication(
                        cache_key="cache-b",
                        result="bravo",
                        dependencies={("Project", "identification", "2")},
                        cache_backend=cache_backend,
                    ),
                ]
            )

        self.assertEqual(events, ["index", "set_many"])
        self.assertEqual(len(cache_backend.set_many_calls), 1)
        self.assertEqual(
            set(cache_backend.set_many_calls[0][0]), {"cache-a", "cache-b"}
        )
        self.assertEqual(
            cache_backend.get("cache-a").value,
            "alpha",
        )
        self.assertEqual(
            cache_backend.get("cache-b").dependencies,
            frozenset({("Project", "identification", "2")}),
        )

    def test_batch_publish_falls_back_to_per_key_set_without_set_many(self) -> None:
        cache_backend = FakeDependencyCacheBackend()

        publish_dependency_cache_entries(
            [
                self.make_pending_publication(
                    cache_key="cache-a",
                    result="alpha",
                    dependencies={("Project", "identification", "1")},
                    cache_backend=cache_backend,
                ),
                self.make_pending_publication(
                    cache_key="cache-b",
                    result="bravo",
                    dependencies={("Project", "identification", "2")},
                    cache_backend=cache_backend,
                ),
            ]
        )

        self.assertEqual(cache_backend.set_order, ["cache-a", "cache-b"])
        self.assertEqual(cache_backend.get("cache-a").value, "alpha")
        self.assertEqual(cache_backend.get("cache-b").value, "bravo")

    def test_batch_publish_skips_entries_from_stale_generations(self) -> None:
        cache_backend = FakeDependencyCacheSetManyBackend()
        stale_generation = get_dependency_generation()
        begin_dependency_data_change()
        end_dependency_data_change()
        current_generation = get_dependency_generation()

        publish_dependency_cache_entries(
            [
                self.make_pending_publication(
                    cache_key="cache-stale",
                    result="old",
                    dependencies={("Project", "identification", "1")},
                    cache_backend=cache_backend,
                    started_generation=stale_generation,
                ),
                self.make_pending_publication(
                    cache_key="cache-current",
                    result="new",
                    dependencies={("Project", "identification", "2")},
                    cache_backend=cache_backend,
                    started_generation=current_generation,
                ),
            ]
        )

        self.assertIsNone(cache_backend.get("cache-stale"))
        self.assertEqual(cache_backend.get("cache-current").value, "new")

    def test_batch_publish_aborts_without_writes_when_data_change_active(self) -> None:
        cache_backend = FakeDependencyCacheSetManyBackend()
        begin_dependency_data_change()
        try:
            with self.assertRaises(CachePublishAborted):
                publish_dependency_cache_entries(
                    [
                        self.make_pending_publication(
                            cache_key="cache-a",
                            result="blocked",
                            dependencies={("Project", "identification", "1")},
                            cache_backend=cache_backend,
                        )
                    ]
                )
        finally:
            end_dependency_data_change()

        self.assertEqual(cache_backend.store, {})
        self.assertEqual(cache_backend.set_many_calls, [])

    def test_publish_aborts_without_writes_when_generation_changed(self) -> None:
        cache_backend = FakeDependencyCacheBackend()
        started_generation = get_dependency_generation()
        begin_dependency_data_change()
        end_dependency_data_change()
        recorded_entries: list[Any] = []

        with self.assertRaises(CachePublishAborted):
            publish_dependency_cache_entry(
                cache_key="cache-a",
                result="stale",
                dependencies={("Project", "identification", "1")},
                cache_backend=cache_backend,
                timeout=None,
                started_generation=started_generation,
                record_many_fn=recorded_entries.extend,
            )

        self.assertEqual(recorded_entries, [])
        self.assertEqual(cache_backend.store, {})

    def test_publish_aborts_without_writes_when_data_change_active(self) -> None:
        cache_backend = FakeDependencyCacheBackend()
        recorded_entries: list[Any] = []
        begin_dependency_data_change()
        try:
            with self.assertRaises(CachePublishAborted):
                publish_dependency_cache_entry(
                    cache_key="cache-a",
                    result="barrier-blocked",
                    dependencies={("Project", "identification", "1")},
                    cache_backend=cache_backend,
                    timeout=None,
                    started_generation=get_dependency_generation(),
                    record_many_fn=recorded_entries.extend,
                )
        finally:
            end_dependency_data_change()

        self.assertEqual(recorded_entries, [])
        self.assertEqual(cache_backend.store, {})

    def test_publish_aborts_without_writes_when_generation_changes_after_recording(
        self,
    ) -> None:
        cache_backend = FakeDependencyCacheBackend()
        started_generation = get_dependency_generation()
        recorded_entries: list[Any] = []

        with (
            mock.patch(
                "general_manager.cache.dependency_publish.get_dependency_generation",
                side_effect=[started_generation, started_generation + 1],
            ),
            self.assertRaises(CachePublishAborted),
        ):
            publish_dependency_cache_entry(
                cache_key="cache-a",
                result="stale",
                dependencies={("Project", "identification", "1")},
                cache_backend=cache_backend,
                timeout=None,
                started_generation=started_generation,
                record_many_fn=recorded_entries.extend,
            )

        self.assertEqual(
            recorded_entries,
            [("cache-a", {("Project", "identification", "1")})],
        )
        self.assertEqual(cache_backend.store, {})

    def test_publish_aborts_without_writes_when_barrier_starts_after_recording(
        self,
    ) -> None:
        cache_backend = FakeDependencyCacheBackend()
        started_generation = get_dependency_generation()
        recorded_entries: list[Any] = []

        with (
            mock.patch(
                "general_manager.cache.dependency_publish.is_dependency_data_change_active",
                side_effect=[False, True],
            ),
            self.assertRaises(CachePublishAborted),
        ):
            publish_dependency_cache_entry(
                cache_key="cache-a",
                result="stale",
                dependencies={("Project", "identification", "1")},
                cache_backend=cache_backend,
                timeout=None,
                started_generation=started_generation,
                record_many_fn=recorded_entries.extend,
            )

        self.assertEqual(
            recorded_entries,
            [("cache-a", {("Project", "identification", "1")})],
        )
        self.assertEqual(cache_backend.store, {})

    def test_wait_for_cached_dependency_hit_returns_cached_none(self) -> None:
        cache_backend = FakeDependencyCacheBackend()
        publish_dependency_cache_entry(
            cache_key="cache-a",
            result=None,
            dependencies={("Project", "identification", "1")},
            cache_backend=cache_backend,
            timeout=None,
            started_generation=get_dependency_generation(),
            record_many_fn=lambda _entries: None,
        )

        hit = wait_for_cached_dependency_hit(
            cache_backend,
            "cache-a",
            timeout_seconds=0.01,
        )

        self.assertIsInstance(hit, DependencyCacheHit)
        assert isinstance(hit, DependencyCacheHit)
        self.assertIsNone(hit.value)
        self.assertEqual(
            hit.dependencies,
            frozenset({("Project", "identification", "1")}),
        )

    def test_wait_for_cached_dependency_hit_returns_sentinel_on_timeout(
        self,
    ) -> None:
        cache_backend = FakeDependencyCacheBackend()
        marker = object()

        self.assertIs(
            wait_for_cached_dependency_hit(
                cache_backend,
                "cache-a",
                timeout_seconds=0,
                sentinel=marker,
            ),
            marker,
        )

    def test_wait_for_cached_dependency_hit_polls_until_value_exists(self) -> None:
        cache_backend = FakeDependencyCacheBackend()
        marker = object()
        attempts = 0
        original_get = cache_backend.get

        def delayed_get(key: str, default: Any = None) -> Any:
            nonlocal attempts
            attempts += 1
            if attempts == 3:
                publish_dependency_cache_entry(
                    cache_key="cache-a",
                    result="ready",
                    dependencies={("Project", "identification", "1")},
                    cache_backend=cache_backend,
                    timeout=None,
                    started_generation=get_dependency_generation(),
                    record_many_fn=lambda _entries: None,
                )
            return original_get(key, default)

        cache_backend.get = delayed_get  # type: ignore[method-assign]

        with mock.patch("general_manager.cache.dependency_publish.time.sleep"):
            hit = wait_for_cached_dependency_hit(
                cache_backend,
                "cache-a",
                timeout_seconds=1,
                sentinel=marker,
            )

        self.assertIsInstance(hit, DependencyCacheHit)
        assert isinstance(hit, DependencyCacheHit)
        self.assertEqual(hit.value, "ready")
        self.assertEqual(attempts, 3)

    def test_wait_for_cached_dependency_hit_returns_published_value(self) -> None:
        cache_backend = FakeDependencyCacheBackend()
        cache_key = "cache-a"
        marker = object()

        publish_dependency_cache_entry(
            cache_key=cache_key,
            result="ready",
            dependencies={("Project", "identification", "1")},
            cache_backend=cache_backend,
            timeout=None,
            started_generation=get_dependency_generation(),
            record_many_fn=lambda _entries: None,
        )

        hit = wait_for_cached_dependency_hit(
            cache_backend,
            cache_key,
            timeout_seconds=0.01,
            sentinel=marker,
        )

        self.assertIsInstance(hit, DependencyCacheHit)
        assert isinstance(hit, DependencyCacheHit)
        self.assertEqual(hit.value, "ready")
