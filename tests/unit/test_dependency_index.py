from django.test import TestCase, override_settings
from general_manager.cache.dependency_index import (
    DATA_CHANGE_COUNT_KEY,
    DATA_CHANGE_LOCK_KEY,
    DEPENDENCY_GENERATION_KEY,
    acquire_lock,
    begin_dependency_data_change,
    end_dependency_data_change,
    get_full_index,
    get_dependency_generation,
    is_dependency_data_change_active,
    release_lock,
    set_full_index,
    record_dependencies,
    record_many_dependencies,
    remove_cache_key_from_index,
    invalidate_cache_key,
    invalidate_and_remove_cache_keys,
    invalidate_request_query_dependencies,
    capture_old_values,
    generic_cache_invalidation,
    parse_dependency_identifier,
    serialize_dependency_identifier,
    cache,
    dependency_index,
)
import time
import json
from datetime import datetime, timezone, date
from unittest.mock import patch
from general_manager.cache.signals import data_change, post_data_change, pre_data_change
from types import SimpleNamespace


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-dependency-index",
    }
}


@override_settings(CACHES=TEST_CACHES)
class TestAcquireReleaseLock(TestCase):
    def setUp(self):
        # Clear the cache before each test
        cache.clear()

    def test_acquire_lock(self):
        locked = acquire_lock()
        self.assertTrue(locked)

    def test_lock_funcionality(self):
        acquire_lock()
        secound_locked = acquire_lock()
        self.assertFalse(secound_locked)
        release_lock()
        locked = acquire_lock()
        self.assertTrue(locked)

    def test_release_lock(self):
        locked = acquire_lock()
        self.assertTrue(locked)
        release_lock()
        locked = acquire_lock()
        self.assertTrue(locked)
        release_lock()

    def test_release_lock_without_acquire(self):
        release_lock()

    def test_lock_ttl(self):
        locked = acquire_lock(0.1)  # type: ignore
        self.assertTrue(locked)
        time.sleep(0.15)
        locked = acquire_lock()
        self.assertTrue(locked)


@override_settings(CACHES=TEST_CACHES)
class TestFullIndex(TestCase):
    def setUp(self):
        # Clear the cache before each test
        cache.clear()

    def test_get_full_index_without_setting_first(self):
        idx = get_full_index()
        self.assertIsInstance(idx, dict)
        self.assertSetEqual(
            set(idx.keys()), {"filter", "exclude", "request_query", "all"}
        )
        self.assertIsInstance(idx["filter"], dict)
        self.assertIsInstance(idx["exclude"], dict)
        self.assertIsInstance(idx["request_query"], dict)
        self.assertIsInstance(idx["all"], dict)

    def test_set_full_index(self):
        idx = get_full_index()
        self.assertIsInstance(idx, dict)
        self.assertSetEqual(
            set(idx.keys()), {"filter", "exclude", "request_query", "all"}
        )
        self.assertIsInstance(idx["filter"], dict)
        self.assertIsInstance(idx["exclude"], dict)
        self.assertIsInstance(idx["request_query"], dict)
        self.assertIsInstance(idx["all"], dict)

        new_idx: dependency_index = {
            "filter": {"project": {"name": {"value1": {"1", "2", "3"}}}},
            "exclude": {},
            "request_query": {},
            "all": {},
        }
        set_full_index(new_idx)
        idx = get_full_index()
        self.assertEqual(idx, new_idx)

    def test_get_full_index_backfills_missing_sections(self):
        cache.set("dependency_index", {"filter": {"project": {}}}, None)

        idx = get_full_index()

        self.assertEqual(
            idx,
            {
                "filter": {"project": {}},
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )


@override_settings(CACHES=TEST_CACHES)
class TestDependencyGenerationAndBarrier(TestCase):
    def setUp(self):
        cache.clear()

    def test_generation_defaults_to_zero(self):
        self.assertEqual(get_dependency_generation(), 0)

    def test_begin_data_change_bumps_generation_and_sets_barrier(self):
        generation = begin_dependency_data_change()

        self.assertEqual(generation, 1)
        self.assertEqual(cache.get(DEPENDENCY_GENERATION_KEY), 1)
        self.assertTrue(is_dependency_data_change_active())
        self.assertEqual(cache.get(DATA_CHANGE_LOCK_KEY), "1")

    def test_end_data_change_releases_barrier_without_changing_generation(self):
        begin_dependency_data_change()

        end_dependency_data_change()

        self.assertFalse(is_dependency_data_change_active())
        self.assertEqual(get_dependency_generation(), 1)

    def test_create_data_change_owns_generation_and_barrier_lifecycle(self):
        class Example:
            @classmethod
            @data_change
            def create(cls):
                return SimpleNamespace(identification=None)

        result = Example.create()

        self.assertIsNotNone(result)
        self.assertEqual(get_dependency_generation(), 1)
        self.assertFalse(is_dependency_data_change_active())
        self.assertEqual(cache.get(DATA_CHANGE_COUNT_KEY), 0)

    def test_overlapping_data_changes_keep_barrier_until_last_end(self):
        begin_dependency_data_change()
        begin_dependency_data_change()

        end_dependency_data_change()
        self.assertTrue(is_dependency_data_change_active())
        self.assertEqual(cache.get(DATA_CHANGE_COUNT_KEY), 1)

        end_dependency_data_change()
        self.assertFalse(is_dependency_data_change_active())
        self.assertEqual(cache.get(DATA_CHANGE_COUNT_KEY), 0)

    def test_data_change_exception_releases_dependency_barrier(self):
        class Example:
            @data_change
            def update(self):
                raise RuntimeError("boom")

        calls = []

        def receiver(**kwargs):
            calls.append(kwargs)

        post_data_change.connect(
            receiver,
            weak=False,
            dispatch_uid="test_failed_mutation_no_post",
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "boom"):
                Example().update()
        finally:
            post_data_change.disconnect(dispatch_uid="test_failed_mutation_no_post")

        self.assertEqual(calls, [])
        self.assertFalse(is_dependency_data_change_active())
        self.assertEqual(cache.get(DATA_CHANGE_COUNT_KEY), 0)

    @patch(
        "general_manager.cache.dependency_index.end_dependency_data_change",
        side_effect=RuntimeError("cleanup"),
    )
    def test_data_change_preserves_mutation_exception_when_cleanup_fails(
        self, mock_end_dependency_data_change
    ):
        class Example:
            @data_change
            def update(self):
                raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "^boom$"):
            Example().update()

        mock_end_dependency_data_change.assert_called_once_with()

    @patch(
        "general_manager.cache.dependency_index.end_dependency_data_change",
        side_effect=RuntimeError("cleanup"),
    )
    def test_data_change_propagates_cleanup_exception_after_success(
        self, mock_end_dependency_data_change
    ):
        class Example:
            @data_change
            def mutate(self):
                return self

        with self.assertRaisesRegex(RuntimeError, "^cleanup$"):
            Example().mutate()

        mock_end_dependency_data_change.assert_called_once_with()

    def test_pre_data_change_exception_releases_dependency_barrier(self):
        class Example:
            @data_change
            def update(self):
                return self

        def receiver(**kwargs):
            raise RuntimeError

        pre_data_change.connect(
            receiver,
            weak=False,
            dispatch_uid="test_pre_failure_releases_barrier",
        )
        try:
            with self.assertRaises(RuntimeError):
                Example().update()
        finally:
            pre_data_change.disconnect(
                dispatch_uid="test_pre_failure_releases_barrier",
            )

        self.assertFalse(is_dependency_data_change_active())
        self.assertEqual(cache.get(DATA_CHANGE_COUNT_KEY), 0)

    def test_post_data_change_exception_releases_dependency_barrier(self):
        class Example:
            @data_change
            def mutate(self):
                return self

        def receiver(**kwargs):
            raise RuntimeError

        post_data_change.connect(
            receiver,
            weak=False,
            dispatch_uid="test_post_failure_releases_barrier",
        )
        try:
            with self.assertRaises(RuntimeError):
                Example().mutate()
        finally:
            post_data_change.disconnect(
                dispatch_uid="test_post_failure_releases_barrier",
            )

        self.assertFalse(is_dependency_data_change_active())
        self.assertEqual(cache.get(DATA_CHANGE_COUNT_KEY), 0)

    def test_begin_data_change_stores_barrier_and_count_without_timeout(self):
        with patch.object(cache, "set", wraps=cache.set) as set_spy:
            begin_dependency_data_change()

        set_spy.assert_any_call(DATA_CHANGE_COUNT_KEY, 1, None)
        set_spy.assert_any_call(DATA_CHANGE_LOCK_KEY, "1", None)

    def test_end_data_change_preserves_positive_count_without_timeout(self):
        original_set = cache.set
        set_calls = []

        def set_spy(key, value, timeout=None):
            set_calls.append((key, value, timeout))
            return original_set(key, value, timeout)

        with patch.object(cache, "set", side_effect=set_spy):
            begin_dependency_data_change()
            begin_dependency_data_change()
            set_calls.clear()
            end_dependency_data_change()

        self.assertTrue(is_dependency_data_change_active())
        self.assertEqual(cache.get(DATA_CHANGE_COUNT_KEY), 1)
        self.assertIn((DATA_CHANGE_COUNT_KEY, 1, None), set_calls)
        self.assertIn((DATA_CHANGE_LOCK_KEY, "1", None), set_calls)


@override_settings(CACHES=TEST_CACHES)
class TestRecordDependencies(TestCase):
    def setUp(self):
        # Clear the cache before each test
        cache.clear()

    def test_record_dependencies(self):
        record_dependencies(
            "abc123",
            [
                ("project", "filter", json.dumps({"name": 123})),
                ("project", "identification", json.dumps({"id": 1})),
            ],
        )
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {
                    "project": {
                        "name": {
                            "123": {"abc123"},
                        },
                        "identification": {'{"id": 1}': {"abc123"}},
                    },
                },
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    def test_record_dependencies_with_existing_key(self):
        record_dependencies(
            "abc123",
            [
                ("project", "filter", json.dumps({"name": 123})),
                ("project", "identification", json.dumps({"id": 1})),
            ],
        )
        record_dependencies(
            "abc123",
            [
                ("project", "filter", json.dumps({"name": 456})),
                ("project", "identification", json.dumps({"id": 2})),
            ],
        )
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {
                    "project": {
                        "name": {
                            "123": {"abc123"},
                            "456": {"abc123"},
                        },
                        "identification": {
                            '{"id": 1}': {"abc123"},
                            '{"id": 2}': {"abc123"},
                        },
                    },
                },
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    def test_record_dependencies_with_new_key(self):
        record_dependencies(
            "abc123",
            [
                ("project", "filter", json.dumps({"name": 123})),
                ("project", "identification", json.dumps({"id": 1})),
            ],
        )
        record_dependencies(
            "cde456",
            [
                ("project", "filter", json.dumps({"name": 123})),
            ],
        )
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {
                    "project": {
                        "name": {
                            "123": {"abc123", "cde456"},
                        },
                        "identification": {'{"id": 1}': {"abc123"}},
                    },
                },
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    def test_record_dependencies_with_empty_list(self):
        record_dependencies(
            "abc123",
            [],
        )
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {},
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    def test_record_dependencies_tracks_composite_parameters(self):
        record_dependencies(
            "combo",
            [
                ("project", "filter", json.dumps({"name": 123, "status": "active"})),
            ],
        )
        idx = get_full_index()
        project_section = idx["filter"]["project"]
        self.assertIn("__cache_dependencies__", project_section)
        self.assertIn("combo", project_section["__cache_dependencies__"])
        self.assertIn(
            json.dumps({"name": 123, "status": "active"}),
            project_section["__cache_dependencies__"]["combo"],
        )

    def test_record_dependencies_with_date_identifier(self):
        identifier = serialize_dependency_identifier({"day": date(2024, 7, 24)})

        record_dependencies(
            "day-cache",
            [("DateOnlyManager", "filter", identifier)],
        )

        idx = get_full_index()
        self.assertEqual(
            idx["filter"]["DateOnlyManager"]["day"],
            {'"2024-07-24"': {"day-cache"}},
        )

    def test_parse_dependency_identifier_roundtrips_json(self):
        self.assertEqual(
            parse_dependency_identifier(
                serialize_dependency_identifier({"day": date(2024, 7, 24)})
            ),
            {"day": "2024-07-24"},
        )

    def test_record_request_query_dependencies(self):
        identifier = serialize_dependency_identifier(
            {
                "operation": "search",
                "filters": {"query": ["alpha"]},
                "excludes": {},
            }
        )

        record_dependencies(
            "request-cache",
            [("RemoteProject", "request_query", identifier)],
        )

        idx = get_full_index()
        self.assertIn("request_query", idx)
        self.assertEqual(
            idx["request_query"]["RemoteProject"][identifier],
            {"request-cache"},
        )

    def test_record_all_dependencies(self):
        record_dependencies(
            "all-cache",
            [("RemoteProject", "all", "")],
        )

        idx = get_full_index()
        self.assertEqual(idx["all"]["RemoteProject"], {"all-cache"})

    def test_record_dependencies_with_empty_filter_params_tracks_all_lookup(self):
        record_dependencies(
            "empty-filter",
            [("project", "filter", json.dumps({}))],
        )

        idx = get_full_index()
        self.assertEqual(
            idx["filter"]["project"]["__all__"]["__all__"],
            {"empty-filter"},
        )

    def test_record_dependencies_with_empty_exclude_params_tracks_all_lookup(self):
        record_dependencies(
            "empty-exclude",
            [("project", "exclude", json.dumps({}))],
        )

        idx = get_full_index()
        self.assertEqual(
            idx["exclude"]["project"]["__all__"]["__all__"],
            {"empty-exclude"},
        )

    def test_parse_dependency_identifier_returns_none_for_non_json(self):
        self.assertIsNone(parse_dependency_identifier("{bad"))
        self.assertIsNone(parse_dependency_identifier(repr({"day": date(2024, 7, 24)})))

    def test_serialize_dependency_identifier_orders_sets_deterministically(self):
        self.assertEqual(
            serialize_dependency_identifier({"members": {"b", "a"}}),
            json.dumps({"members": ["a", "b"]}, sort_keys=True),
        )

    @patch("general_manager.cache.dependency_index.acquire_lock")
    def test_waits_until_lock_is_acquired(self, mock_acquire):
        mock_acquire.side_effect = [False, False, True]
        record_dependencies(
            "abc123",
            [
                ("project", "filter", json.dumps({"name": 123})),
                ("project", "identification", json.dumps({"id": 1})),
            ],
        )
        self.assertEqual(mock_acquire.call_count, 3)
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {
                    "project": {
                        "name": {
                            "123": {"abc123"},
                        },
                        "identification": {'{"id": 1}': {"abc123"}},
                    },
                },
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    @patch("general_manager.cache.dependency_index.acquire_lock")
    @patch("general_manager.cache.dependency_index.LOCK_TIMEOUT", 0.1)
    def test_raises_timeout_error(self, mock_acquire):
        mock_acquire.return_value = False
        with self.assertRaises(TimeoutError):
            record_dependencies(
                "abc123",
                [
                    ("project", "filter", json.dumps({"name": 123})),
                    ("project", "identification", json.dumps({"id": 1})),
                ],
            )


@override_settings(CACHES=TEST_CACHES)
class TestRecordManyDependencies(TestCase):
    def setUp(self):
        cache.clear()

    @patch("general_manager.cache.dependency_index.acquire_lock")
    def test_records_many_dependency_sets_under_one_lock(self, mock_acquire):
        mock_acquire.return_value = True

        record_many_dependencies(
            [
                (
                    "cache-a",
                    {
                        ("project", "filter", json.dumps({"name": "A"})),
                        ("project", "identification", "1"),
                    },
                ),
                (
                    "cache-b",
                    {
                        ("project", "filter", json.dumps({"name": "B"})),
                        ("project", "identification", "2"),
                    },
                ),
            ]
        )

        self.assertEqual(mock_acquire.call_count, 1)
        self.assertEqual(
            get_full_index(),
            {
                "filter": {
                    "project": {
                        "name": {
                            '"A"': {"cache-a"},
                            '"B"': {"cache-b"},
                        },
                        "identification": {
                            "1": {"cache-a"},
                            "2": {"cache-b"},
                        },
                    }
                },
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    def test_record_many_deduplicates_exact_cache_dependency_pairs(self):
        dependency = ("project", "filter", json.dumps({"name": "A"}))

        record_many_dependencies(
            [
                ("cache-a", {dependency}),
                ("cache-a", {dependency}),
                ("cache-b", {dependency}),
            ]
        )

        self.assertEqual(
            get_full_index()["filter"]["project"]["name"],
            {'"A"': {"cache-a", "cache-b"}},
        )

    def test_record_many_ignores_empty_dependency_sets(self):
        record_many_dependencies([("cache-a", set()), ("cache-b", [])])

        self.assertEqual(
            get_full_index(),
            {"filter": {}, "exclude": {}, "request_query": {}, "all": {}},
        )


@override_settings(CACHES=TEST_CACHES)
class TestRemoveCacheKeyFromIndex(TestCase):
    def setUp(self):
        # Clear the cache before each test
        cache.clear()

    def test_remove_cache_key_from_index(self):
        record_dependencies(
            "abc123",
            [
                ("project", "filter", json.dumps({"name": 123})),
                ("project", "identification", json.dumps({"id": 1})),
            ],
        )
        remove_cache_key_from_index("abc123")
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {},
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    def test_remove_cache_key_from_index_with_multiple_keys(self):
        record_dependencies(
            "abc123",
            [
                ("project", "filter", json.dumps({"name": 123})),
                ("project", "identification", json.dumps({"id": 1})),
            ],
        )
        record_dependencies(
            "cde456",
            [
                ("project", "filter", json.dumps({"name": 123})),
            ],
        )
        remove_cache_key_from_index("abc123")
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {
                    "project": {
                        "name": {
                            "123": {"cde456"},
                        },
                    },
                },
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    def test_remove_cache_key_from_index_with_non_existent_key(self):
        record_dependencies(
            "abc123",
            [
                ("project", "filter", json.dumps({"name": 123})),
                ("project", "identification", json.dumps({"id": 1})),
            ],
        )
        remove_cache_key_from_index("non_existent_key")
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {
                    "project": {
                        "name": {
                            "123": {"abc123"},
                        },
                        "identification": {'{"id": 1}': {"abc123"}},
                    },
                },
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    def test_remove_cache_key_from_index_with_empty_index(self):
        remove_cache_key_from_index("abc123")
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {},
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    def test_remove_cache_key_clears_composite_dependencies(self):
        record_dependencies(
            "combo",
            [
                ("project", "filter", json.dumps({"name": 123, "status": "active"})),
            ],
        )
        remove_cache_key_from_index("combo")
        idx = get_full_index()
        self.assertEqual(
            idx,
            {"filter": {}, "exclude": {}, "request_query": {}, "all": {}},
        )

    def test_remove_cache_key_from_index_clears_all_and_request_query_sections(self):
        record_dependencies(
            "mixed-key",
            [
                ("RemoteProject", "all", ""),
                (
                    "RemoteProject",
                    "request_query",
                    serialize_dependency_identifier({"operation": "search"}),
                ),
            ],
        )

        remove_cache_key_from_index("mixed-key")

        self.assertEqual(
            get_full_index(),
            {"filter": {}, "exclude": {}, "request_query": {}, "all": {}},
        )

    @patch("general_manager.cache.dependency_index.acquire_lock")
    def test_waits_until_lock_is_acquired(self, mock_acquire):
        idx: dependency_index = {
            "filter": {
                "project": {
                    "name": {
                        "123": {"abc123"},
                    },
                    "identification": {'{"id": 1}': {"abc123"}},
                },
            },
            "exclude": {},
            "request_query": {},
            "all": {},
        }

        set_full_index(idx)
        mock_acquire.side_effect = [
            False,
            False,
            True,
        ]
        remove_cache_key_from_index("abc123")
        self.assertEqual(mock_acquire.call_count, 3)
        idx = get_full_index()
        self.assertEqual(
            idx,
            {
                "filter": {},
                "exclude": {},
                "request_query": {},
                "all": {},
            },
        )

    @patch("general_manager.cache.dependency_index.acquire_lock")
    @patch("general_manager.cache.dependency_index.LOCK_TIMEOUT", 0.1)
    def test_raises_timeout_error(self, mock_acquire):
        idx: dependency_index = {
            "filter": {
                "project": {
                    "name": {
                        "123": {"abc123"},
                    },
                    "identification": {'{"id": 1}': {"abc123"}},
                },
            },
            "exclude": {},
            "request_query": {},
            "all": {},
        }

        set_full_index(idx)
        mock_acquire.return_value = False
        with self.assertRaises(TimeoutError):
            remove_cache_key_from_index("abc123")


@override_settings(CACHES=TEST_CACHES)
class TestInvalidateCacheKey(TestCase):
    def setUp(self):
        # Clear the cache before each test
        cache.clear()
        cache.set("abc123", "test_value")
        cache.set("cde456", "test_value_2")
        cache.set("xyz789", "test_value_3")

    def test_invalidate_cache_key(self):
        invalidate_cache_key("abc123")
        self.assertIsNone(cache.get("abc123"))
        self.assertEqual(cache.get("cde456"), "test_value_2")
        self.assertEqual(cache.get("xyz789"), "test_value_3")

    def test_invalidate_cache_key_with_non_existent_key(self):
        invalidate_cache_key("non_existent_key")
        self.assertEqual(cache.get("abc123"), "test_value")
        self.assertEqual(cache.get("cde456"), "test_value_2")
        self.assertEqual(cache.get("xyz789"), "test_value_3")

    def test_invalidate_cache_key_with_empty_cache(self):
        cache.clear()
        invalidate_cache_key("abc123")
        self.assertIsNone(cache.get("abc123"))
        self.assertIsNone(cache.get("cde456"))
        self.assertIsNone(cache.get("xyz789"))


@override_settings(CACHES=TEST_CACHES)
class TestInvalidateRequestQueryDependencies(TestCase):
    def setUp(self):
        cache.clear()

    def test_invalidates_only_target_manager_request_query_keys(self):
        cache.set("remote-a", {"value": 1}, None)
        cache.set("remote-b", {"value": 2}, None)
        cache.set("other-manager", {"value": 3}, None)
        set_full_index(
            {
                "filter": {},
                "exclude": {},
                "request_query": {
                    "RemoteProject": {
                        "query-a": {"remote-a"},
                        "query-b": {"remote-b"},
                    },
                    "OtherProject": {
                        "query-c": {"other-manager"},
                    },
                },
            }
        )

        invalidated = invalidate_request_query_dependencies("RemoteProject")

        self.assertEqual(invalidated, ("remote-a", "remote-b"))
        self.assertIsNone(cache.get("remote-a"))
        self.assertIsNone(cache.get("remote-b"))
        self.assertEqual(cache.get("other-manager"), {"value": 3})
        self.assertEqual(
            get_full_index(),
            {
                "filter": {},
                "exclude": {},
                "request_query": {
                    "OtherProject": {
                        "query-c": {"other-manager"},
                    },
                },
                "all": {},
            },
        )

    def test_returns_empty_tuple_when_manager_has_no_request_query_keys(self):
        set_full_index(
            {
                "filter": {},
                "exclude": {},
                "request_query": {"OtherProject": {"query-c": {"other-manager"}}},
                "all": {},
            }
        )

        invalidated = invalidate_request_query_dependencies("RemoteProject")

        self.assertEqual(invalidated, ())
        self.assertEqual(
            get_full_index(),
            {
                "filter": {},
                "exclude": {},
                "request_query": {"OtherProject": {"query-c": {"other-manager"}}},
                "all": {},
            },
        )


@override_settings(CACHES=TEST_CACHES)
class TestInvalidateAndRemoveCacheKeys(TestCase):
    def setUp(self):
        cache.clear()

    def test_noop_when_given_no_keys(self):
        invalidate_and_remove_cache_keys([])
        self.assertEqual(
            get_full_index(),
            {"filter": {}, "exclude": {}, "request_query": {}, "all": {}},
        )

    def test_invalidates_and_removes_keys_across_all_sections(self):
        cache.set("cache-a", "A", None)
        cache.set("cache-b", "B", None)
        cache.set("cache-c", "C", None)
        set_full_index(
            {
                "filter": {"project": {"name": {'"test"': {"cache-a", "cache-b"}}}},
                "exclude": {"project": {"status": {'"archived"': {"cache-b"}}}},
                "request_query": {"project": {"query": {"cache-b", "cache-c"}}},
                "all": {"project": {"cache-a", "cache-c"}},
            }
        )

        invalidate_and_remove_cache_keys(["cache-a", "cache-b", "cache-a"])

        self.assertIsNone(cache.get("cache-a"))
        self.assertIsNone(cache.get("cache-b"))
        self.assertEqual(cache.get("cache-c"), "C")
        self.assertEqual(
            get_full_index(),
            {
                "filter": {},
                "exclude": {},
                "request_query": {"project": {"query": {"cache-c"}}},
                "all": {"project": {"cache-c"}},
            },
        )


class DummyManager:
    __name__ = "DummyManager"  # manager_name = sender.__name__

    def __init__(self):
        self.identification = 42
        self.title = "Mein Titel"
        self.owner = SimpleNamespace(name="Max Mustermann")
        self.count = 0


class CaptureOldValuesTests(TestCase):
    @patch("general_manager.cache.dependency_index.get_full_index")
    def test_capture_old_values_sets_old_values_correctly(self, mock_get_full_index):
        mock_get_full_index.return_value = {
            "filter": {"DummyManager": ["title", "owner__name"]},
            "exclude": {},
        }
        inst = DummyManager()

        pre_data_change.send(sender=DummyManager, instance=inst)

        self.assertTrue(hasattr(inst, "_old_values"))
        self.assertEqual(
            inst._old_values,  # type: ignore
            {"title": "Mein Titel", "owner__name": "Max Mustermann"},
        )

    @patch("general_manager.cache.dependency_index.get_full_index")
    def test_no_instance_no_values_set(self, mock_get_full_index):
        mock_get_full_index.return_value = {
            "filter": {"DummyManager": ["foo"]},
            "exclude": {},
        }
        capture_old_values(sender=DummyManager, instance=None)

    @patch("general_manager.cache.dependency_index.get_full_index")
    def test_empty_lookups_does_nothing(self, mock_get_full_index):
        mock_get_full_index.return_value = {"filter": {}, "exclude": {}}
        inst = DummyManager()
        capture_old_values(sender=DummyManager, instance=inst)
        self.assertFalse(hasattr(inst, "_old_values"))

    @patch("general_manager.cache.dependency_index.get_full_index")
    def test_old_values_with_operator(self, mock_get_full_index):
        mock_get_full_index.return_value = {
            "filter": {"DummyManager": {"title": {"test": {"abc123"}}}},
            "exclude": {"DummyManager": {"count__gt": {"10": {"abc123"}}}},
        }
        inst = DummyManager()
        inst.count = 100
        pre_data_change.send(sender=DummyManager, instance=inst)

        self.assertTrue(hasattr(inst, "_old_values"))
        self.assertEqual(
            inst._old_values,  # type: ignore
            {"title": "Mein Titel", "count": 100},
        )


class DummyManager2:
    __name__ = "DummyManager2"

    def __init__(self, status, count):
        self.status = status
        self.count = count


class ObjectManager:
    __name__ = "ObjectManager"

    def __init__(self, payload):
        self.payload = payload


class DateOnlyManager:
    __name__ = "DateOnlyManager"

    def __init__(self, day):
        self.day = day


class DateManager:
    __name__ = "DateManager"

    def __init__(self, timestamp):
        self.timestamp = timestamp


class ReprObject:
    def __repr__(self):
        return "{'key': 'value'}"

    def __eq__(self, other):
        return isinstance(other, ReprObject)


class HashablePayload:
    class InvalidFooTypeError(TypeError):
        """Raised when HashablePayload receives a non-integer foo value."""

        def __init__(self) -> None:
            """
            Initialize the InvalidFooTypeError with a default error message.

            This constructor sets the exception message to "foo must be an int." to indicate the provided `foo` value has an invalid (non-int) type.
            """
            super().__init__("foo must be an int.")

    def __init__(self, foo: int):
        """
        Initialize a HashablePayload instance with the given integer value.

        Parameters:
            foo (int): Integer value to store on the instance.

        Raises:
            HashablePayload.InvalidFooTypeError: If `foo` is not an int.
        """
        if not isinstance(foo, int):
            raise HashablePayload.InvalidFooTypeError()
        self.foo = foo

    def __repr__(self):
        """
        Provide an unambiguous string representation of the HashablePayload instance.

        Returns:
            str: A string in the form "HashablePayload(foo=<value>)" showing the `foo` attribute.
        """
        return f"HashablePayload(foo={self.foo})"

    def __eq__(self, other):
        return isinstance(other, HashablePayload) and self.foo == other.foo

    def __hash__(self):
        return hash(self.foo)


class ReprManager:
    __name__ = "ReprManager"

    def __init__(self, value):
        self.value = value


class MissingAttrManager:
    __name__ = "MissingAttrManager"

    def __init__(self, status):
        self.status = status


class GenericCacheInvalidationTests(TestCase):
    def assert_cache_keys_removed_from_index(
        self,
        idx: dict[object, object],
        *cache_keys: str,
    ) -> None:
        remaining_keys = set(cache_keys)

        def assert_absent(value: object) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    self.assertNotIn(key, remaining_keys)
                    assert_absent(nested)
            elif isinstance(value, (list, set, tuple)):
                for nested in value:
                    self.assertNotIn(nested, remaining_keys)

        assert_absent(idx)

    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    @patch(
        "general_manager.cache.dependency_index.invalidate_request_query_dependencies",
        return_value=(),
    )
    def test_filter_invalidation_uses_locked_internals_without_public_helper_reentry(
        self,
        mock_invalidate_request_queries,
        mock_remove,
    ):
        cache.set("FILTER-1", "value", None)
        set_full_index(
            {
                "filter": {"DummyManager2": {"status": {'"active"': {"FILTER-1"}}}},
                "exclude": {},
                "request_query": {},
                "all": {},
            }
        )
        inst = DummyManager2(status="active", count=1)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={"status": "inactive"},
        )

        mock_invalidate_request_queries.assert_not_called()
        mock_remove.assert_not_called()
        self.assertIsNone(cache.get("FILTER-1"))
        self.assertEqual(
            get_full_index(),
            {"filter": {}, "exclude": {}, "request_query": {}, "all": {}},
        )

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch(
        "general_manager.cache.dependency_index.invalidate_request_query_dependencies"
    )
    def test_all_lookup_invalidates_registered_cache_keys(
        self,
        mock_invalidate_request_queries,
        mock_invalidate,
        mock_remove,
        mock_get_index,
    ):
        mock_invalidate_request_queries.return_value = ()
        mock_get_index.return_value = {
            "filter": {"DummyManager2": {"__all__": {"__all__": {"ALL-1", "ALL-2"}}}},
            "exclude": {},
            "all": {"DummyManager2": {"ROOT-1"}},
        }
        inst = DummyManager2(status="active", count=1)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={},
        )

        mock_invalidate_request_queries.assert_not_called()
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(
            mock_get_index.return_value,
            "ROOT-1",
            "ALL-1",
            "ALL-2",
        )

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch(
        "general_manager.cache.dependency_index.invalidate_request_query_dependencies"
    )
    def test_request_query_invalidation_uses_batch_helper(
        self,
        mock_invalidate_request_queries,
        mock_invalidate,
        mock_remove,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {},
            "exclude": {},
            "request_query": {"DummyManager2": {"query": {"rq-1", "rq-2"}}},
            "all": {},
        }
        inst = DummyManager2(status="active", count=1)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={"status": "inactive"},
        )

        mock_invalidate_request_queries.assert_not_called()
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(
            mock_get_index.return_value,
            "rq-1",
            "rq-2",
        )

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_filter_invalidation_on_new_match(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager2": {"status": {'"active"': ["A", "B"]}}},
            "exclude": {},
        }

        old_vals = {"status": "inactive"}
        inst = DummyManager2(status="active", count=0)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "A", "B")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_filter_invalidation_when_old_value_was_none(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager2": {"count": {"null": ["MISSING"]}}},
            "exclude": {},
        }
        old_vals = {"count": None}
        inst = DummyManager2(status="any", count=1000)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(
            mock_get_index.return_value, "MISSING"
        )

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_filter_invalidation_when_old_value_was_none_for_in(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager2": {"count__in": {"[null]": ["MISSING"]}}},
            "exclude": {},
        }
        old_vals = {"count": None}
        inst = DummyManager2(status="any", count=1000)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(
            mock_get_index.return_value, "MISSING"
        )

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_exclude_invalidation_only_on_change(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        """
        Ensures keys listed under an exclude rule are invalidated and removed when the exclude condition changes.

        Sets up an index containing an exclude rule for DummyManager2 (count__gt 5) and simulates a transition from an old value that matched the exclude (count = 10) to a new instance value that no longer matches (count = 3); asserts that the affected cache key is invalidated and removed.
        """
        mock_get_index.return_value = {
            "filter": {},
            "exclude": {"DummyManager2": {"count__gt": {"5": ["X"]}}},
        }
        old_vals = {"count": 10}
        inst = DummyManager2(status="any", count=3)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_no_invalidation_when_nothing_matches_or_changes(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {"filter": {}, "exclude": {}}
        old_vals = {}
        inst = DummyManager2(status=None, count=None)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_invalidation_when_no_old_values(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {},
            "exclude": {"DummyManager2": {"status": {'"active"': ["X"]}}},
        }
        inst = DummyManager2(status="active", count=0)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={},
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_invalidation_when_no_old_values_and_no_match(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager2": {"status": {'"active"': ["X"]}}},
            "exclude": {"DummyManager2": {"status": {'"active"': ["X"]}}},
        }
        inst = DummyManager2(status="inactive", count=0)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={},
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_invalidation_with_contains(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__contains": {'"hallo"': ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "hallo world"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_invalidation_with_contains_no_match(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__contains": {'"hallo"': ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "world"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_invalidation_with_endswith(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__endswith": {'"hallo"': ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "halihallo"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_invalidation_with_startswith(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__startswith": {'"hallo"': ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "halloworld"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_invalidation_with_endswith_and_startswith(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {
                "DummyManager": {
                    "title__endswith": {'"hallo"': ["X"]},
                    "title__startswith": {'"hallo"': ["Y"]},
                }
            },
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "halloworldhallo"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X", "Y")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_invalidation_with_regex(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__regex": {"^hallo.*world$": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "hallo super duper world"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_with_invalid_operation(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__invalid": {'"hallo"': ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "halloworld"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_with_lte(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"count__lte": {"2": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.count = 2

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_with_gte(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"count__gte": {"2": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.count = 2

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_with_lt(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"count__lt": {"2": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.count = 1

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_with_gt(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"count__gt": {"2": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.count = 3

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_with_in(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"count__in": {"[2, 3, 4]": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.count = 3

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_with_not_in(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"count__in": {"[2, 3, 4]": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.count = 5

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_with_in_old_but_not_in_new(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"count__in": {"[2, 3, 4]": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.count = 5

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={"count": 3},
        )
        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "X")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_composite_filter_requires_all_conditions(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        identifier = json.dumps({"count__gte": 4, "count__lte": 8})
        mock_get_index.return_value = {
            "filter": {
                "DummyManager2": {
                    "__cache_dependencies__": {"COMP": {identifier}},
                    "count__gte": {"4": {"COMP"}},
                    "count__lte": {"8": {"COMP"}},
                }
            },
            "exclude": {},
        }
        inst = DummyManager2(status="unchanged", count=9)
        old_vals = {"count": 9}

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_datetime_values_are_coerced_from_iso_strings(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        iso_string = "2024-07-24T12:00:00"
        identifier = json.dumps({"timestamp": iso_string})
        mock_get_index.return_value = {
            "filter": {
                "DateManager": {
                    "__cache_dependencies__": {"DATE": {identifier}},
                    "timestamp": {repr(iso_string): {"DATE"}},
                }
            },
            "exclude": {},
        }
        aware_dt = datetime(2024, 7, 24, 12, 0, tzinfo=timezone.utc)
        inst = DateManager(timestamp=aware_dt)
        old_vals = {"timestamp": aware_dt}

        generic_cache_invalidation(
            sender=DateManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "DATE")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_repr_fallback_invalidation_on_conversion_failure(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        payload = SimpleNamespace(foo=1)
        payload_repr = repr(payload)
        mock_get_index.return_value = {
            "filter": {
                "ObjectManager": {
                    "payload": {payload_repr: {"OBJ"}},
                }
            },
            "exclude": {},
        }
        inst = ObjectManager(payload=SimpleNamespace(foo=1))
        old_vals = {"payload": payload}

        generic_cache_invalidation(
            sender=ObjectManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "OBJ")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_regex_compile_failure_returns_without_invalidation(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {
                "DummyManager": {
                    "title__regex": {"[unbalanced": {"REG"}},
                }
            },
            "exclude": {},
        }
        inst = DummyManager()
        inst.title = "sample text"
        old_vals = {"title": "sample text"}

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_datetime_values_with_z_suffix_are_handled(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        iso_with_z = "2024-07-24T12:00:00Z"
        mock_get_index.return_value = {
            "filter": {
                "DateManager": {
                    "timestamp": {json.dumps(iso_with_z): {"DATEZ"}},
                }
            },
            "exclude": {},
        }
        aware_dt = datetime(2024, 7, 24, 12, 0, tzinfo=timezone.utc)
        inst = DateManager(timestamp=aware_dt)
        old_vals = {"timestamp": aware_dt}

        generic_cache_invalidation(
            sender=DateManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "DATEZ")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_date_values_are_converted_from_iso_strings(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        iso_date = "2024-07-24"
        mock_get_index.return_value = {
            "filter": {
                "DateOnlyManager": {
                    "day": {json.dumps(iso_date): {"DAY"}},
                }
            },
            "exclude": {},
        }
        today = date(2024, 7, 24)
        inst = DateOnlyManager(day=today)
        old_vals = {"day": today}

        generic_cache_invalidation(
            sender=DateOnlyManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "DAY")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_composite_exclude_invalidation_when_conditions_change(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        identifier = json.dumps({"status": "blocked", "count__gte": 5})
        mock_get_index.return_value = {
            "filter": {},
            "exclude": {
                "DummyManager2": {
                    "__cache_dependencies__": {"EXC": {identifier}},
                    "status": {"'blocked'": {"EXC"}},
                    "count__gte": {"5": {"EXC"}},
                }
            },
        }
        inst = DummyManager2(status="active", count=6)
        old_vals = {"status": "blocked", "count": 6}

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "EXC")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_eq_repr_fallback_matches(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        val_key = repr(SimpleNamespace(foo=1))
        mock_get_index.return_value = {
            "filter": {
                "ObjectManager": {
                    "payload": {val_key: {"OBJ"}},
                }
            },
            "exclude": {},
        }
        inst = ObjectManager(payload=SimpleNamespace(foo=1))
        old_vals = {"payload": SimpleNamespace(foo=1)}

        generic_cache_invalidation(
            sender=ObjectManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "OBJ")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_eq_with_non_string_key_uses_raw_value(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        payload_key = HashablePayload(1)
        mock_get_index.return_value = {
            "filter": {
                "ObjectManager": {
                    "payload": {payload_key: {"OBJ"}},
                }
            },
            "exclude": {},
        }
        inst = ObjectManager(payload=HashablePayload(1))
        old_vals = {"payload": payload_key}

        generic_cache_invalidation(
            sender=ObjectManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "OBJ")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_in_with_invalid_literal_is_ignored(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {
                "DummyManager2": {
                    "status__in": {"[1, 2": {"INKEY"}},
                }
            },
            "exclude": {},
        }
        inst = DummyManager2(status="active", count=0)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={},
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_in_with_repr_fallback_matches(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        val_key = '[{"key": "value"}]'
        mock_get_index.return_value = {
            "filter": {
                "ReprManager": {
                    "value__in": {val_key: {"REP"}},
                }
            },
            "exclude": {},
        }
        inst = ReprManager(value=ReprObject())

        generic_cache_invalidation(
            sender=ReprManager,
            instance=inst,
            old_relevant_values={},
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "REP")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_datetime_invalid_literal_does_not_invalidate(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {
                "DateManager": {
                    "timestamp": {repr("invalid"): {"BAD"}},
                }
            },
            "exclude": {},
        }
        aware_dt = datetime(2024, 7, 24, 12, 0, tzinfo=timezone.utc)
        inst = DateManager(timestamp=aware_dt)
        old_vals = {"timestamp": aware_dt}

        generic_cache_invalidation(
            sender=DateManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_datetime_naive_value_adjusts_timezone(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        val_key = json.dumps("2024-07-24T12:00:00+00:00")
        mock_get_index.return_value = {
            "filter": {
                "DateManager": {
                    "timestamp": {val_key: {"NAIVE"}},
                }
            },
            "exclude": {},
        }
        naive_dt = datetime(2024, 7, 24, 12, 0)
        inst = DateManager(timestamp=naive_dt)
        old_vals = {"timestamp": naive_dt}

        generic_cache_invalidation(
            sender=DateManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "NAIVE")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_date_invalid_literal_does_not_invalidate(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {
                "DateOnlyManager": {
                    "day": {repr("invalid-date"): {"BAD"}},
                }
            },
            "exclude": {},
        }
        today = date(2024, 7, 24)
        inst = DateOnlyManager(day=today)
        old_vals = {"day": today}

        generic_cache_invalidation(
            sender=DateOnlyManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_date_with_raw_object_matches(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        date_key = date(2024, 7, 24)
        mock_get_index.return_value = {
            "filter": {
                "DateOnlyManager": {
                    "day": {date_key: {"DAYOBJ"}},
                }
            },
            "exclude": {},
        }
        inst = DateOnlyManager(day=date(2024, 7, 24))
        old_vals = {"day": date_key}

        generic_cache_invalidation(
            sender=DateOnlyManager,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
        self.assert_cache_keys_removed_from_index(mock_get_index.return_value, "DAYOBJ")

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_range_threshold_with_uncoercible_value(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {
                "DummyManager2": {
                    "count__gt": {repr("not-int"): {"RANGE"}},
                }
            },
            "exclude": {},
        }
        inst = DummyManager2(status="active", count=10)
        old_vals = {"count": 5}

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values=old_vals,
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_missing_attribute_path_returns_none(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {
                "MissingAttrManager": {
                    "missing__field": {repr("value"): {"MISS"}},
                }
            },
            "exclude": {},
        }
        inst = MissingAttrManager(status="open")

        generic_cache_invalidation(
            sender=MissingAttrManager,
            instance=inst,
            old_relevant_values={},
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_composite_dependencies_skip_unrelated_lookup(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        identifier = json.dumps({"count__gte": 5})
        mock_get_index.return_value = {
            "filter": {
                "DummyManager2": {
                    "__cache_dependencies__": {"CMP": {identifier}},
                    "status": {'"active"': {"CMP"}},
                }
            },
            "exclude": {},
        }
        inst = DummyManager2(status="active", count=10)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={"count": 10},
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependency_index.get_full_index")
    @patch("general_manager.cache.dependency_index.invalidate_cache_key")
    @patch("general_manager.cache.dependency_index.remove_cache_key_from_index")
    def test_composite_dependencies_skip_malformed_identifier(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {
                "DummyManager2": {
                    "__cache_dependencies__": {"CMP": {"{bad"}},
                    "count__gte": {"5": {"CMP"}},
                }
            },
            "exclude": {},
        }
        inst = DummyManager2(status="active", count=10)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={"count": 10},
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()
