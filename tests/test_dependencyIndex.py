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
)
import time

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-dependency-index",
    }
}


@override_settings(CACHES=TEST_CACHES)
class TestAquireReleaseLock(TestCase):
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
