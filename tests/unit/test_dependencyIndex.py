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
from datetime import datetime, timezone, date
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

    def test_remove_cache_key_clears_composite_dependencies(self):
        record_dependencies(
            "combo",
            [
                ("project", "filter", json.dumps({"name": 123, "status": "active"})),
            ],
        )
        remove_cache_key_from_index("combo")
        idx = get_full_index()
        self.assertEqual(idx, {"filter": {}, "exclude": {}})

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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

        mock_invalidate.assert_called_once_with("DATE")
        mock_remove.assert_called_once_with("DATE")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

        mock_invalidate.assert_called_once_with("OBJ")
        mock_remove.assert_called_once_with("OBJ")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
                    "timestamp": {repr(iso_with_z): {"DATEZ"}},
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

        mock_invalidate.assert_called_once_with("DATEZ")
        mock_remove.assert_called_once_with("DATEZ")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
                    "day": {repr(iso_date): {"DAY"}},
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

        mock_invalidate.assert_called_once_with("DAY")
        mock_remove.assert_called_once_with("DAY")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

        expected_calls = [call("EXC"), call("EXC")]
        self.assertEqual(mock_invalidate.call_args_list, expected_calls)
        self.assertEqual(mock_remove.call_args_list, expected_calls)

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

        mock_invalidate.assert_called_once_with("OBJ")
        mock_remove.assert_called_once_with("OBJ")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

        mock_invalidate.assert_called_once_with("OBJ")
        mock_remove.assert_called_once_with("OBJ")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

        mock_invalidate.assert_called_once_with("REP")
        mock_remove.assert_called_once_with("REP")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
    def test_datetime_naive_value_adjusts_timezone(
        self,
        mock_remove,
        mock_invalidate,
        mock_get_index,
    ):
        val_key = repr("2024-07-24T12:00:00+00:00")
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

        mock_invalidate.assert_called_once_with("NAIVE")
        mock_remove.assert_called_once_with("NAIVE")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

        mock_invalidate.assert_called_once_with("DAYOBJ")
        mock_remove.assert_called_once_with("DAYOBJ")

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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

    @patch("general_manager.cache.dependencyIndex.get_full_index")
    @patch("general_manager.cache.dependencyIndex.invalidate_cache_key")
    @patch("general_manager.cache.dependencyIndex.remove_cache_key_from_index")
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
                    "status": {"'active'": {"CMP"}},
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