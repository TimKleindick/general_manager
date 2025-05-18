from django.test import TestCase, override_settings
from general_manager.cache.dependencyIndex import (
    acquire_lock,
    release_lock,
    get_full_index,
    set_full_index,
    record_dependencies,
    remove_cache_key_from_index,
    invalidate_cache_key,
    capture_old_values,
    generic_cache_invalidation,
    cache,
    dependency_index,
)
import time
import json
from unittest.mock import patch, call
from general_manager.cache.signals import pre_data_change
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
        self.assertSetEqual(set(idx.keys()), {"filter", "exclude"})
        self.assertIsInstance(idx["filter"], dict)
        self.assertIsInstance(idx["exclude"], dict)

    def test_set_full_index(self):
        idx = get_full_index()
        self.assertIsInstance(idx, dict)
        self.assertSetEqual(set(idx.keys()), {"filter", "exclude"})
        self.assertIsInstance(idx["filter"], dict)
        self.assertIsInstance(idx["exclude"], dict)

        new_idx: dependency_index = {
            "filter": {"project": {"name": {"value1": {"1", "2", "3"}}}},
            "exclude": {},
        }
        set_full_index(new_idx)
        idx = get_full_index()
        self.assertEqual(idx, new_idx)


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
            },
        )

    @patch("general_manager.cache.dependencyIndex.acquire_lock")
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
            },
        )

    @patch("general_manager.cache.dependencyIndex.acquire_lock")
    @patch("general_manager.cache.dependencyIndex.LOCK_TIMEOUT", 0.1)
    def test_raises_timeout_error(self, mock_acquire):
        mock_acquire.side_effect = [False] * 10
        with self.assertRaises(TimeoutError):
            record_dependencies(
                "abc123",
                [
                    ("project", "filter", json.dumps({"name": 123})),
                    ("project", "identification", json.dumps({"id": 1})),
                ],
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
            },
        )

    @patch("general_manager.cache.dependencyIndex.acquire_lock")
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
            },
        )

    @patch("general_manager.cache.dependencyIndex.acquire_lock")
    @patch("general_manager.cache.dependencyIndex.LOCK_TIMEOUT", 0.1)
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
        }

        set_full_index(idx)
        mock_acquire.side_effect = [False] * 10
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


class DummyManager:
    __name__ = "DummyManager"  # manager_name = sender.__name__

    def __init__(self):
        self.identification = 42
        self.title = "Mein Titel"
        self.owner = SimpleNamespace(name="Max Mustermann")
        self.count = 0


class CaptureOldValuesTests(TestCase):

    @patch("general_manager.cache.dependencyIndex.get_full_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    def test_no_instance_no_values_set(self, mock_get_full_index):
        mock_get_full_index.return_value = {
            "filter": {"DummyManager": ["foo"]},
            "exclude": {},
        }
        capture_old_values(sender=DummyManager, instance=None)

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    def test_empty_lookups_does_nothing(self, mock_get_full_index):
        mock_get_full_index.return_value = {"filter": {}, "exclude": {}}
        inst = DummyManager()
        capture_old_values(sender=DummyManager, instance=inst)
        self.assertFalse(hasattr(inst, "_old_values"))

    @patch("general_manager.cache.dependencyIndex.get_full_index")
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


class GenericCacheInvalidationTests(TestCase):
    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_filter_invalidation_on_new_match(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):

        mock_get_index.return_value = {
            "filter": {"DummyManager2": {"status": {"'active'": ["A", "B"]}}},
            "exclude": {},
        }

        old_vals = {"status": "inactive"}
        inst = DummyManager2(status="active", count=0)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values=old_vals,
        )

        # Assert: filter => new_match=True ⇒ invalidate & remove per key
        expected_calls = [call("A"), call("B")]
        self.assertEqual(mock_invalidate.call_args_list, expected_calls)
        self.assertEqual(mock_remove.call_args_list, expected_calls)

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_exclude_invalidation_only_on_change(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):

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

        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_invalidation_when_no_old_values(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {},
            "exclude": {"DummyManager2": {"status": {"'active'": ["X"]}}},
        }
        inst = DummyManager2(status="active", count=0)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={},
        )

        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_invalidation_when_no_old_values_and_no_match(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager2": {"status": {"'active'": ["X"]}}},
            "exclude": {"DummyManager2": {"status": {"'active'": ["X"]}}},
        }
        inst = DummyManager2(status="inactive", count=0)

        generic_cache_invalidation(
            sender=DummyManager2,
            instance=inst,
            old_relevant_values={},
        )

        mock_invalidate.assert_not_called()
        mock_remove.assert_not_called()

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_invalidation_with_contains(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__contains": {"'hallo'": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "hallo world"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_invalidation_with_contains_no_match(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__contains": {"'hallo'": ["X"]}}},
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_invalidation_with_endswith(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__endswith": {"'hallo'": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "halihallo"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_invalidation_with_startswith(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__startswith": {"'hallo'": ["X"]}}},
            "exclude": {},
        }

        inst = DummyManager()
        inst.title = "halloworld"

        generic_cache_invalidation(
            sender=DummyManager,
            instance=inst,
            old_relevant_values={},
        )
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_invalidation_with_endswith_and_startswith(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {
                "DummyManager": {
                    "title__endswith": {"'hallo'": ["X"]},
                    "title__startswith": {"'hallo'": ["Y"]},
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
        expected_calls = [call("X"), call("Y")]
        self.assertEqual(mock_invalidate.call_args_list, expected_calls)
        self.assertEqual(mock_remove.call_args_list, expected_calls)

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_with_invalid_operation(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        mock_get_index.return_value = {
            "filter": {"DummyManager": {"title__invalid": {"'hallo'": ["X"]}}},
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
        mock_invalidate.assert_called_once_with("X")
        mock_remove.assert_called_once_with("X")
