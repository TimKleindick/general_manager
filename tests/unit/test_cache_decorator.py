from django.test import SimpleTestCase
from django.core.cache import cache
from unittest import mock
from general_manager.cache.cache_decorator import cached, DependencyTracker
from general_manager.cache.dependency_index import (
    begin_dependency_data_change,
    end_dependency_data_change,
)
from general_manager.cache.dependency_publish import CachePublishAborted
from general_manager.cache.run_context import CalculationRunContext
from general_manager.utils.make_cache_key import make_cache_key
import pickle
import time


def _trusted_pickle_loads(data: bytes) -> object:
    """
    Deserialize pickle-serialized bytes produced by the in-memory test cache.

    Returns:
        object: The deserialized Python object.
    """

    return pickle.loads(data)  # noqa: S301 - test cache operates on controlled input


class FakeCacheBackend:
    def __init__(self):
        """
        Initializes the in-memory cache store.
        """
        self.store = {}
        self.timeouts = {}

    def get(self, key, default=None):
        """
        Retrieves a value from the cache by key, unpickling it if present.

        Args:
            key: The cache key to look up.
            default: Value to return if the key is not found.

        Returns:
            The unpickled value associated with the key, or the default if not found.
        """
        cached_value = self.store.get(key, default)
        if cached_value is not default:
            return _trusted_pickle_loads(cached_value)  # type: ignore
        return default

    def set(self, key, value, timeout=None):
        """
        Store `value` in the in-memory cache under `key`, serializing it before storage.

        Parameters:
            key: Cache key under which the value will be stored.
            value: The Python object to cache; it is serialized with pickle prior to storage.
            timeout: Optional expiration time for the cache entry. This backend does not enforce expiration.
        """
        self.store[key] = pickle.dumps(value)
        self.timeouts[key] = timeout


class TestCacheDecoratorBackend(SimpleTestCase):
    def setUp(self):
        # clear the thread-local storage before each test

        """
        Prepares the test environment before each test case.

        Initializes a fake cache backend and a call recording list, and resets thread-local dependency tracking state to ensure test isolation.
        """
        DependencyTracker.reset_thread_local_storage()
        cache.clear()

        self.fake_cache = FakeCacheBackend()
        self.record_calls = []

        # record_fn saves a copy of the set that the real tracker gives
        def record_fn(key, deps):
            """
            Records a cache key and its associated dependencies for later inspection.

            Args:
                key: The cache key being recorded.
                deps: An iterable of dependencies associated with the cache key.
            """
            self.record_calls.append((key, set(deps)))

        self.record_fn = record_fn

    def tearDown(self):
        # clear the record_calls after each test
        """
        Cleans up dependency tracking state after each test by resetting DependencyTracker.
        """
        with DependencyTracker():
            pass
        cache.clear()

    def test_real_tracker_records_manual_tracks(self):
        """
        Tests that DependencyTracker correctly records manually tracked dependencies and cleans up thread-local storage after use.
        """
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
        """
        Tests that the cached decorator does not alter the original function's return value.
        """

        @cached()
        def sample_function(x, y):
            return x + y

        result = sample_function(1, 2)
        self.assertEqual(
            result, 3, "The result should be the same as the original function"
        )

    def test_cache_with_timeout(self):
        """
        Tests that the cached decorator with a timeout correctly triggers cache set on miss and skips set on hit.

        Verifies that on the first call, the function result is cached and both cache.get and cache.set are called. On a subsequent call with the same arguments before expiration, only cache.get is called, confirming a cache hit.
        """

        @cached(scope="timeout", timeout=5)
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
        """
        Tests that the cached function triggers a cache miss after expiration and resets the cache.

        Verifies that after the cache timeout, the function results in a cache miss, causing both
        `cache.get` and `cache.set` to be called again.
        """

        @cached(scope="timeout", timeout=1)
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
            # Wait for the cache to expire
            time.sleep(1.1)
            get_spy.reset_mock()
            set_spy.reset_mock()

            sample_function(1, 2)
            self.assertTrue(get_spy.called, "Second call: cache.get() should be called")
            self.assertTrue(set_spy.called, "Should be cache miss")

    def test_cache_with_custom_backend(self):
        """
        Tests that the cached decorator correctly stores results using a custom cache backend.

        Verifies that invoking a cached function with a custom backend results in the computed value being stored in the backend's cache.
        """
        custom_cache = FakeCacheBackend()

        @cached(scope="timeout", timeout=5, cache_backend=custom_cache)
        def sample_function(x, y):
            """
            Returns the sum of two values.

            Args:
                x: The first value to add.
                y: The second value to add.

            Returns:
                The sum of x and y.
            """
            return x + y

        sample_function(1, 2)
        self.assertTrue(
            len(custom_cache.store) > 0, "Cache should have stored the result"
        )
        key = make_cache_key(sample_function, (1, 2), {})
        self.assertEqual(custom_cache.timeouts[key], 5)

    def test_cache_decorator_uses_real_tracker(self):
        """
        Tests that the cached decorator records dependencies using DependencyTracker on cache miss and invokes the record function, but does not record dependencies or call the record function on cache hit.
        """

        @cached(
            scope="dependency",
            cache_backend=self.fake_cache,
            record_fn=self.record_fn,
        )
        def fn(x, y):
            # Record actual dependencies
            DependencyTracker.track("User", "identification", str(x))
            DependencyTracker.track("Profile", "identification", str(y))
            return x + y

        # first call: Cache miss
        res = fn(2, 3)
        self.assertEqual(res, 5)
        key = make_cache_key(fn, (2, 3), {})

        # Result should be in the fake cache
        self.assertIn(key, self.fake_cache.store)
        self.assertEqual(_trusted_pickle_loads(self.fake_cache.store[key]), 5)

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

    def test_dependency_scope_waits_for_published_value_when_lease_is_owned(self):
        calls = 0

        @cached(
            scope="dependency",
            cache_backend=self.fake_cache,
            record_fn=self.record_fn,
        )
        def fn(value):
            nonlocal calls
            calls += 1
            return value * 2

        key = make_cache_key(fn, (3,), {})
        deps_key = f"{key}:deps"
        self.fake_cache.set(deps_key, {("User", "identification", "3")}, None)
        original_get = self.fake_cache.get
        key_reads = 0

        def publish_after_initial_miss(cache_key, default=None):
            nonlocal key_reads
            if cache_key == key:
                key_reads += 1
                if key_reads == 2:
                    self.fake_cache.set(key, 6, None)
            return original_get(cache_key, default)

        self.fake_cache.get = publish_after_initial_miss

        with mock.patch(
            "general_manager.cache.cache_decorator.acquire_compute_lease",
            return_value=None,
        ):
            with DependencyTracker() as dependencies:
                self.assertEqual(fn(3), 6)

        self.assertEqual(calls, 0)
        self.assertEqual(dependencies, {("User", "identification", "3")})

    def test_dependency_scope_returns_result_without_caching_when_publish_aborts(self):
        calls = 0

        @cached(
            scope="dependency",
            cache_backend=self.fake_cache,
            record_fn=self.record_fn,
        )
        def fn(value):
            nonlocal calls
            calls += 1
            DependencyTracker.track("User", "identification", str(value))
            return value * 2

        with mock.patch(
            "general_manager.cache.cache_decorator.publish_dependency_cache_entry",
            side_effect=CachePublishAborted,
        ):
            self.assertEqual(fn(3), 6)

        key = make_cache_key(fn, (3,), {})
        self.assertEqual(calls, 1)
        self.assertNotIn(key, self.fake_cache.store)
        self.assertNotIn(f"{key}:deps", self.fake_cache.store)
        self.assertEqual(self.record_calls, [])

    def test_dependency_scope_does_not_cache_when_generation_changes_during_compute(
        self,
    ):
        @cached(
            scope="dependency",
            cache_backend=self.fake_cache,
        )
        def fn(value):
            DependencyTracker.track("User", "identification", str(value))
            begin_dependency_data_change()
            end_dependency_data_change()
            return value * 2

        self.assertEqual(fn(3), 6)

        key = make_cache_key(fn, (3,), {})
        self.assertNotIn(key, self.fake_cache.store)
        self.assertNotIn(f"{key}:deps", self.fake_cache.store)

    def test_dependency_scope_does_not_cache_when_barrier_is_active_at_publish(
        self,
    ):
        @cached(
            scope="dependency",
            cache_backend=self.fake_cache,
        )
        def fn(value):
            DependencyTracker.track("User", "identification", str(value))
            begin_dependency_data_change()
            return value * 2

        try:
            self.assertEqual(fn(3), 6)
        finally:
            end_dependency_data_change()

        key = make_cache_key(fn, (3,), {})
        self.assertNotIn(key, self.fake_cache.store)
        self.assertNotIn(f"{key}:deps", self.fake_cache.store)

    def test_cache_decorator_with_timeout_and_record_fn(self):
        """
        Tests that the cached decorator with a timeout stores results, but does not call the
        dependency recording function, and that cache hits do not trigger dependency recording.
        """

        @cached(
            scope="timeout",
            timeout=5,
            cache_backend=self.fake_cache,
            record_fn=self.record_fn,
        )
        def fn(x, y):
            # Record actual dependencies
            DependencyTracker.track("User", "identification", str(x))
            DependencyTracker.track("Profile", "identification", str(y))
            return x + y

        # first call: Cache miss
        res = fn(2, 3)
        self.assertEqual(res, 5)
        key = make_cache_key(fn, (2, 3), {})

        # Result should be in the fake cache
        self.assertIn(key, self.fake_cache.store)
        self.assertEqual(_trusted_pickle_loads(self.fake_cache.store[key]), 5)

        # no record_fn call because of timeout
        self.assertEqual(len(self.record_calls), 0)
        self.assertNotIn(f"{key}:deps", self.fake_cache.store)
        self.assertEqual(self.fake_cache.timeouts[key], 5)
        self.assertEqual(res, 5)
        self.assertEqual(self.record_calls, [])

        # second call: Cache hit -> no record_fn call
        self.record_calls.clear()
        res2 = fn(2, 3)
        self.assertEqual(res2, 5)
        self.assertEqual(self.record_calls, [])

    def test_nested_cache_decorator(self):
        """
        Verify nested cached functions cache results and record their dependencies on cache misses.

        Ensures that both inner and outer functions store their computed results in the provided cache backend and that the recording function is invoked once per miss with the correct dependency sets (inner first, then outer). Also verifies that subsequent calls that hit the cache do not trigger dependency recording.
        """

        @cached(
            scope="dependency",
            cache_backend=self.fake_cache,
            record_fn=self.record_fn,
        )
        def outer_function(x, y):
            DependencyTracker.track("User", "identification", str(x))
            return inner_function(x, y)

        @cached(
            scope="dependency",
            cache_backend=self.fake_cache,
            record_fn=self.record_fn,
        )
        def inner_function(x, y):
            """
            Tracks a dependency and returns the sum of two values.

            Args:
                x: First value to add.
                y: Second value to add; also used for dependency tracking.

            Returns:
                The sum of x and y.
            """
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
        self.assertEqual(_trusted_pickle_loads(self.fake_cache.store[key_outer]), 5)
        self.assertEqual(_trusted_pickle_loads(self.fake_cache.store[key_inner]), 5)

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
        """
        Tests nested cached functions where the inner function cache is hit before the outer function is called.

        Verifies that dependency recording occurs separately for inner and outer cached functions on cache misses, and that no dependency recording occurs on cache hits. Ensures cached results are correctly stored and retrieved from the custom cache backend.
        """

        @cached(
            scope="dependency",
            cache_backend=self.fake_cache,
            record_fn=self.record_fn,
        )
        def outer_function(x, y):
            DependencyTracker.track("User", "identification", str(x))
            return inner_function(x, y)

        @cached(
            scope="dependency",
            cache_backend=self.fake_cache,
            record_fn=self.record_fn,
        )
        def inner_function(x, y):
            """
            Tracks a dependency and returns the sum of two values.

            Args:
                x: The first value to add.
                y: The second value to add and to use for dependency tracking.

            Returns:
                The sum of x and y.
            """
            DependencyTracker.track("Profile", "identification", str(y))
            return x + y

        # first call: inner function: Cache miss
        inner_res = inner_function(2, 3)
        self.assertEqual(inner_res, 5)
        # now the inner function should be in the cache
        key_inner = make_cache_key(inner_function, (2, 3), {})
        self.assertIn(key_inner, self.fake_cache.store)
        self.assertEqual(_trusted_pickle_loads(self.fake_cache.store[key_inner]), 5)

        # second call: outer function: Cache miss
        res = outer_function(2, 3)
        self.assertEqual(res, 5)
        key_outer = make_cache_key(outer_function, (2, 3), {})

        # Result should be in the fake cache
        self.assertIn(key_outer, self.fake_cache.store)
        self.assertEqual(_trusted_pickle_loads(self.fake_cache.store[key_outer]), 5)

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


class TestCacheDecoratorScopes(SimpleTestCase):
    def test_timeout_scope_requires_timeout(self):
        with self.assertRaisesRegex(ValueError, 'scope="timeout" requires timeout'):
            cached(scope="timeout")

    def test_set_timeout_is_only_valid_for_timeout_scope(self):
        for scope in ("run", "dependency", "none"):
            with (
                self.subTest(scope=scope),
                self.assertRaisesRegex(
                    ValueError,
                    'timeout is only supported with scope="timeout"',
                ),
            ):
                cached(scope=scope, timeout=5)

    def test_timeout_scope_uses_backend_without_dependency_recording(self):
        fake_cache = FakeCacheBackend()
        record_calls = []
        calls = 0

        def record_fn(key, deps):
            record_calls.append((key, set(deps)))

        @cached(
            scope="timeout", timeout=5, cache_backend=fake_cache, record_fn=record_fn
        )
        def sample(value):
            nonlocal calls
            calls += 1
            DependencyTracker.track("User", "identification", str(value))
            return value * 2

        self.assertEqual(sample(3), 6)
        self.assertEqual(sample(3), 6)

        key = make_cache_key(sample, (3,), {})
        self.assertEqual(calls, 1)
        self.assertIn(key, fake_cache.store)
        self.assertEqual(fake_cache.timeouts[key], 5)
        self.assertNotIn(f"{key}:deps", fake_cache.store)
        self.assertEqual(record_calls, [])

    def test_default_scope_reuses_value_inside_context_only(self):
        fake_cache = FakeCacheBackend()
        record_calls = []
        calls = 0

        def record_fn(key, deps):
            record_calls.append((key, set(deps)))

        @cached(cache_backend=fake_cache, record_fn=record_fn)
        def sample(value):
            nonlocal calls
            calls += 1
            DependencyTracker.track("User", "identification", str(value))
            return value * 2

        with CalculationRunContext():
            self.assertEqual(sample(3), 6)
            self.assertEqual(sample(3), 6)

        self.assertEqual(sample(3), 6)
        self.assertEqual(calls, 2)
        self.assertEqual(fake_cache.store, {})
        self.assertEqual(record_calls, [])

    def test_run_scope_reuses_value_inside_context_only(self):
        calls = 0

        @cached(scope="run")
        def sample(value):
            nonlocal calls
            calls += 1
            return value * 2

        with CalculationRunContext():
            self.assertEqual(sample(3), 6)
            self.assertEqual(sample(3), 6)

        self.assertEqual(sample(3), 6)
        self.assertEqual(calls, 2)

    def test_run_scope_creates_context_when_missing_for_single_call(self):
        calls = 0

        @cached(scope="run")
        def sample(value):
            nonlocal calls
            calls += 1
            return value * 2

        self.assertEqual(sample(3), 6)
        self.assertEqual(sample(3), 6)
        self.assertEqual(calls, 2)

    def test_none_scope_never_uses_cache_backend(self):
        fake_cache = FakeCacheBackend()
        calls = 0

        @cached(scope="none", cache_backend=fake_cache)
        def sample(value):
            nonlocal calls
            calls += 1
            return value * 2

        self.assertEqual(sample(3), 6)
        self.assertEqual(sample(3), 6)
        self.assertEqual(calls, 2)
        self.assertEqual(fake_cache.store, {})
