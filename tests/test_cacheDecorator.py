from django.test import SimpleTestCase
from django.core.cache import cache
from unittest import mock
from general_manager.cache.cacheDecorator import cached, DependencyTracker
from general_manager.auxiliary.makeCacheKey import make_cache_key

import time


class FakeCacheBackend:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, timeout=None):
        self.store[key] = value


class TestCacheDecoratorBackend(SimpleTestCase):
    def setUp(self):
        # Vor jedem Test: alten Thread‐lokalen Speicher löschen, falls vorhanden
        try:
            del DependencyTracker.__dict__["_DependencyTracker__local"].dependencies
        except Exception:
            pass

        self.fake_cache = FakeCacheBackend()
        self.record_calls = []

        # record_fn speichert eine Kopie des Sets, das der echte Tracker hergibt
        def record_fn(key, deps):
            self.record_calls.append((key, set(deps)))

        self.record_fn = record_fn

    def tearDown(self):
        # clear the record_calls after each test
        with DependencyTracker():
            pass

    def test_real_tracker_records_manual_tracks(self):
        with DependencyTracker() as deps:
            self.assertEqual(deps, set())
            DependencyTracker.track("MyModel", "filter", "foo")
            DependencyTracker.track("MyModel", "identification", "bar")
            self.assertEqual(
                deps,
                {("MyModel", "filter", "foo"), ("MyModel", "identification", "bar")},
            )

        self.assertFalse(
            hasattr(DependencyTracker(), "dependencies"),
            msg="thread-lokal variable should be deleted after use",
        )

    def test_cache_decorator_should_not_change_result(self):
        @cached(timeout=5)
        def sample_function(x, y):
            return x + y

        result = sample_function(1, 2)
        self.assertEqual(
            result, 3, "The result should be the same as the original function"
        )

    def test_cache_with_timeout(self):
        @cached(timeout=5)
        def sample_function(x, y):
            return x + y

        # Spy at cache.get und cache.set
        with (
            mock.patch.object(cache, "get", wraps=cache.get) as get_spy,
            mock.patch.object(cache, "set", wraps=cache.set) as set_spy,
        ):

            sample_function(1, 2)
            self.assertTrue(get_spy.called, "First call: cache.get() should be called")
            self.assertTrue(
                set_spy.called, "At cache miss, cache.set() should be called"
            )

            get_spy.reset_mock()
            set_spy.reset_mock()

            sample_function(1, 2)
            self.assertTrue(get_spy.called, "Second call: cache.get() should be called")
            self.assertFalse(
                set_spy.called, "At cache hit, cache.set() should NOT be called"
            )

    def test_cache_miss_and_hit(self):
        @cached(timeout=1)
        def sample_function(x, y):
            return x + y

        # Spy at cache.get und cache.set
        with (
            mock.patch.object(cache, "get", wraps=cache.get) as get_spy,
            mock.patch.object(cache, "set", wraps=cache.set) as set_spy,
        ):

            sample_function(1, 2)
            self.assertTrue(get_spy.called, "First call: cache.get() should be called")
            self.assertTrue(
                set_spy.called, "At cache miss, cache.set() should be called"
            )
            time.sleep(2)
            # Wait for the cache to expire
            get_spy.reset_mock()
            set_spy.reset_mock()

            sample_function(1, 2)
            self.assertTrue(get_spy.called, "Second call: cache.get() should be called")
            self.assertTrue(set_spy.called, "Should be cache miss")

    def test_cache_with_custom_backend(self):

        custom_cache = FakeCacheBackend()

        @cached(timeout=5, cache_backend=custom_cache)
        def sample_function(x, y):
            return x + y

        sample_function(1, 2)
        self.assertTrue(
            len(custom_cache.store) > 0, "Cache should have stored the result"
        )

    def test_cache_decorator_uses_real_tracker(self):

        @cached(timeout=None, cache_backend=self.fake_cache, record_fn=self.record_fn)
        def fn(x, y):
            # echte Abhängigkeiten aufzeichnen
            DependencyTracker.track("User", "identification", str(x))
            DependencyTracker.track("Profile", "identification", str(y))
            return x + y

        # first call: Cache miss
        res = fn(2, 3)
        self.assertEqual(res, 5)
        key = make_cache_key(fn, (2, 3), {})

        # Result should be in the fake cache
        self.assertIn(key, self.fake_cache.store)
        self.assertEqual(self.fake_cache.store[key], 5)

        # record_fn should have been called once
        self.assertEqual(len(self.record_calls), 1)
        rec_key, deps = self.record_calls[0]
        self.assertEqual(rec_key, key)
        self.assertEqual(
            deps, {("User", "identification", "2"), ("Profile", "identification", "3")}
        )

        # second call: Cache hit -> no record_fn call
        self.record_calls.clear()
        res2 = fn(2, 3)
        self.assertEqual(res2, 5)
        self.assertEqual(self.record_calls, [])

    def test_cache_decorator_with_timeout_and_record_fn(self):
        @cached(timeout=5, cache_backend=self.fake_cache, record_fn=self.record_fn)
        def fn(x, y):
            # echte Abhängigkeiten aufzeichnen
            DependencyTracker.track("User", "identification", str(x))
            DependencyTracker.track("Profile", "identification", str(y))
            return x + y

        # first call: Cache miss
        res = fn(2, 3)
        self.assertEqual(res, 5)
        key = make_cache_key(fn, (2, 3), {})

        # Result should be in the fake cache
        self.assertIn(key, self.fake_cache.store)
        self.assertEqual(self.fake_cache.store[key], 5)

        # no record_fn call because of timeout
        self.assertEqual(len(self.record_calls), 0)
        self.assertEqual(res, 5)
        self.assertEqual(self.record_calls, [])

        # second call: Cache hit -> no record_fn call
        self.record_calls.clear()
        res2 = fn(2, 3)
        self.assertEqual(res2, 5)
        self.assertEqual(self.record_calls, [])

    def test_nested_cache_decorator(self):
        @cached(cache_backend=self.fake_cache, record_fn=self.record_fn)
        def outer_function(x, y):
            DependencyTracker.track("User", "identification", str(x))
            return inner_function(x, y)

        @cached(cache_backend=self.fake_cache, record_fn=self.record_fn)
        def inner_function(x, y):
            DependencyTracker.track("Profile", "identification", str(y))
            return x + y

        # first call: Cache miss
        res = outer_function(2, 3)
        self.assertEqual(res, 5)
        key_outer = make_cache_key(outer_function, (2, 3), {})
        key_inner = make_cache_key(inner_function, (2, 3), {})

        # Result should be in the fake cache
        self.assertIn(key_outer, self.fake_cache.store)
        self.assertIn(key_inner, self.fake_cache.store)
        self.assertEqual(self.fake_cache.store[key_outer], 5)
        self.assertEqual(self.fake_cache.store[key_inner], 5)

        # record_fn should have been called twice
        # once for the outer function and once for the inner function
        self.assertEqual(len(self.record_calls), 2)
        # first call: inner_function
        rec_key, deps = self.record_calls[0]
        self.assertEqual(rec_key, key_inner)
        self.assertEqual(deps, {("Profile", "identification", "3")})
        # second call: outer_function
        rec_key, deps = self.record_calls[1]
        self.assertEqual(rec_key, key_outer)
        self.assertEqual(
            deps,
            {("User", "identification", "2"), ("Profile", "identification", "3")},
        )

        # second call: Cache hit -> no record_fn call
        self.record_calls.clear()
        res2 = outer_function(2, 3)
        self.assertEqual(res2, 5)
        self.assertEqual(self.record_calls, [])

    def test_nested_cache_decorator_with_inner_cache_hit(self):
        @cached(cache_backend=self.fake_cache, record_fn=self.record_fn)
        def outer_function(x, y):
            DependencyTracker.track("User", "identification", str(x))
            return inner_function(x, y)

        @cached(cache_backend=self.fake_cache, record_fn=self.record_fn)
        def inner_function(x, y):
            DependencyTracker.track("Profile", "identification", str(y))
            return x + y

        # first call: inner function: Cache miss
        inner_res = inner_function(2, 3)
        self.assertEqual(inner_res, 5)
        # now the inner function should be in the cache
        key_inner = make_cache_key(inner_function, (2, 3), {})
        self.assertIn(key_inner, self.fake_cache.store)
        self.assertEqual(self.fake_cache.store[key_inner], 5)

        # second call: outer function: Cache miss
        res = outer_function(2, 3)
        self.assertEqual(res, 5)
        key_outer = make_cache_key(outer_function, (2, 3), {})

        # Result should be in the fake cache
        self.assertIn(key_outer, self.fake_cache.store)
        self.assertEqual(self.fake_cache.store[key_outer], 5)

        # record_fn should have been called twice
        # once for the outer function and once for the inner function
        self.assertEqual(len(self.record_calls), 2)
        # first call: inner_function
        rec_key, deps = self.record_calls[0]
        self.assertEqual(rec_key, key_inner)
        self.assertEqual(deps, {("Profile", "identification", "3")})
        # second call: outer_function
        rec_key, deps = self.record_calls[1]
        self.assertEqual(rec_key, key_outer)
        self.assertEqual(
            deps,
            {("User", "identification", "2"), ("Profile", "identification", "3")},
        )

        # second call: Cache hit -> no record_fn call
        self.record_calls.clear()
        res2 = outer_function(2, 3)
        self.assertEqual(res2, 5)
        self.assertEqual(self.record_calls, [])
