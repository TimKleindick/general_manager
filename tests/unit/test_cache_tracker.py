import threading

from general_manager.cache.cache_tracker import DependencyTracker
from unittest import TestCase


class TestDependencyTracker(TestCase):
    def tearDown(self):
        """Clear thread-local tracker state between tests."""
        DependencyTracker.reset_thread_local_storage()

    def test_dependency_tracker(self):
        """Return a mutable dependency set for the active context."""
        with DependencyTracker() as dependencies:
            dependencies.add(("TestClass", "identification", "TestIdentifier"))
            self.assertIn(
                ("TestClass", "identification", "TestIdentifier"), dependencies
            )

        self.assertEqual(
            dependencies,
            {("TestClass", "identification", "TestIdentifier")},
        )

    def test_dependency_tracker_no_context(self):
        """Ignore tracked dependencies when no context is active."""
        DependencyTracker.track("TestClass", "identification", "TestIdentifier")

        with DependencyTracker() as dependencies:
            self.assertEqual(dependencies, set())

    def test_dependency_tracker_with_exception(self):
        """Clear active state after an exception within the outer context."""
        with self.assertRaises(RuntimeError), DependencyTracker() as dependencies:
            dependencies.add(("TestClass", "identification", "TestIdentifier"))
            raise RuntimeError

        self.assertEqual(
            dependencies,
            {("TestClass", "identification", "TestIdentifier")},
        )
        with DependencyTracker() as fresh_dependencies:
            self.assertEqual(fresh_dependencies, set())

    def test_dependency_tracker_with_multiple_dependencies(self):
        """Collect multiple unique dependencies inside one context."""
        with DependencyTracker() as dependencies:
            dependencies.add(("TestClass1", "identification", "TestIdentifier1"))
            dependencies.add(("TestClass2", "filter", "TestIdentifier2"))
            self.assertIn(
                ("TestClass1", "identification", "TestIdentifier1"), dependencies
            )
            self.assertIn(("TestClass2", "filter", "TestIdentifier2"), dependencies)

    def test_dependency_tracker_with_empty_dependencies(self):
        """Return an empty set when no dependencies are tracked."""
        with DependencyTracker() as dependencies:
            self.assertEqual(len(dependencies), 0)

    def test_dependency_tracker_track(self):
        """Track adds a dependency within the active context."""
        with DependencyTracker() as dependencies:
            DependencyTracker.track("TestClass", "identification", "TestIdentifier")
            self.assertIn(
                ("TestClass", "identification", "TestIdentifier"), dependencies
            )

    def test_dependency_tracker_is_active_reflects_context_depth(self):
        """Report active tracking only while at least one context is open."""
        DependencyTracker.reset_thread_local_storage()
        self.assertFalse(DependencyTracker.is_active())

        with DependencyTracker() as dependencies:
            self.assertTrue(DependencyTracker.is_active())
            DependencyTracker.track("Example", "identification", '{"id": 1}')

            with DependencyTracker():
                self.assertTrue(DependencyTracker.is_active())

            self.assertTrue(DependencyTracker.is_active())

        self.assertEqual(dependencies, {("Example", "identification", '{"id": 1}')})
        self.assertFalse(DependencyTracker.is_active())

    def test_dependency_tracker_rejects_invalid_track_values(self):
        """Reject malformed dependency tuple values before tracking."""
        with self.assertRaises(TypeError):
            DependencyTracker.track(1, "identification", "id")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            DependencyTracker.track("TestClass", "identification", 1)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            DependencyTracker.track(
                "TestClass",
                "fetch",  # type: ignore[arg-type]
                "id",
            )

    def test_dependency_tracker_collapses_duplicates(self):
        """Store tracked dependencies in a set, so duplicates collapse."""
        with DependencyTracker() as dependencies:
            DependencyTracker.track("TestClass", "identification", "TestIdentifier")
            DependencyTracker.track("TestClass", "identification", "TestIdentifier")

            self.assertEqual(
                dependencies,
                {("TestClass", "identification", "TestIdentifier")},
            )

    def test_dependency_tracker_propagates_nested_tracks_to_outer_context(self):
        """Record nested dependencies in both nested and enclosing collectors."""
        with DependencyTracker() as outer_dependencies:
            DependencyTracker.track("Outer", "filter", "one")

            with DependencyTracker() as inner_dependencies:
                DependencyTracker.track("Inner", "exclude", "two")

                self.assertEqual(
                    inner_dependencies,
                    {("Inner", "exclude", "two")},
                )

            self.assertEqual(
                outer_dependencies,
                {
                    ("Outer", "filter", "one"),
                    ("Inner", "exclude", "two"),
                },
            )
            self.assertEqual(
                inner_dependencies,
                {("Inner", "exclude", "two")},
            )

    def test_reset_thread_local_storage_clears_active_context(self):
        """Explicit reset clears the active context stack for the thread."""
        with DependencyTracker() as dependencies:
            DependencyTracker.track("TestClass", "identification", "TestIdentifier")
            DependencyTracker.reset_thread_local_storage()
            DependencyTracker.track("OtherClass", "identification", "OtherIdentifier")

        self.assertEqual(
            dependencies,
            {("TestClass", "identification", "TestIdentifier")},
        )

    def test_reset_thread_local_storage_without_active_context_is_noop(self):
        """Reset can be called when no context is active."""
        DependencyTracker.reset_thread_local_storage()
        DependencyTracker.reset_thread_local_storage()

        with DependencyTracker() as dependencies:
            self.assertEqual(dependencies, set())

    def test_reset_thread_local_storage_inside_nested_context_clears_stack(self):
        """Reset inside nested contexts leaves existing collector snapshots intact."""
        with DependencyTracker() as outer_dependencies:
            DependencyTracker.track("Outer", "filter", "one")
            with DependencyTracker() as inner_dependencies:
                DependencyTracker.track("Inner", "exclude", "two")
                DependencyTracker.reset_thread_local_storage()
                DependencyTracker.track("Ignored", "all", "")

        self.assertEqual(
            outer_dependencies,
            {
                ("Outer", "filter", "one"),
                ("Inner", "exclude", "two"),
            },
        )
        self.assertEqual(
            inner_dependencies,
            {("Inner", "exclude", "two")},
        )

    def test_dependency_tracker_uses_thread_local_storage(self):
        """Track dependencies independently in separate threads."""
        result: list[set[tuple[str, str, str]]] = []

        def worker() -> None:
            with DependencyTracker() as dependencies:
                DependencyTracker.track("Worker", "identification", "id")
                result.append(set(dependencies))

        with DependencyTracker() as dependencies:
            DependencyTracker.track("Main", "identification", "id")
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()

        self.assertEqual(dependencies, {("Main", "identification", "id")})
        self.assertEqual(result, [{("Worker", "identification", "id")}])

    def test_dependency_type_is_public_from_cache_module(self):
        """Dependency is exported from the public cache module."""
        from general_manager import cache

        dependency: cache.Dependency = ("TestClass", "identification", "id")

        self.assertEqual(dependency, ("TestClass", "identification", "id"))
