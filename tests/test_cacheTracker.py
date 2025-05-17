from unittest import TestCase
from general_manager.cache.cacheTracker import DependencyTracker


class TestDependencyTracker(TestCase):
    def test_dependency_tracker(self):
        with DependencyTracker() as dependencies:
            dependencies.add(("TestClass", "identification", "TestIdentifier"))
            self.assertIn(
                ("TestClass", "identification", "TestIdentifier"), dependencies
            )

        # Ensure that the dependencies are cleared after exiting the context
        self.assertFalse(hasattr(dependencies, "dependencies"))

    def test_dependency_tracker_no_context(self):
        # Ensure that no dependencies are tracked outside the context
        dependencies = DependencyTracker()
        self.assertFalse(hasattr(dependencies, "dependencies"))

    def test_dependency_tracker_with_exception(self):
        with self.assertRaises(Exception):
            with DependencyTracker() as dependencies:
                dependencies.add(("TestClass", "identification", "TestIdentifier"))
                raise Exception("Test Exception")

        # Ensure that the dependencies are cleared after the exception
        self.assertFalse(hasattr(dependencies, "dependencies"))  # type: ignore

    def test_dependency_tracker_with_multiple_dependencies(self):
        with DependencyTracker() as dependencies:
            dependencies.add(("TestClass1", "identification", "TestIdentifier1"))
            dependencies.add(("TestClass2", "filter", "TestIdentifier2"))
            self.assertIn(
                ("TestClass1", "identification", "TestIdentifier1"), dependencies
            )
            self.assertIn(("TestClass2", "filter", "TestIdentifier2"), dependencies)

        # Ensure that the dependencies are cleared after exiting the context
        self.assertFalse(hasattr(dependencies, "dependencies"))

    def test_dependency_tracker_with_empty_dependencies(self):
        with DependencyTracker() as dependencies:
            self.assertEqual(len(dependencies), 0)

        # Ensure that the dependencies are cleared after exiting the context
        self.assertFalse(hasattr(dependencies, "dependencies"))

    def test_dependency_tracker_track(self):
        with DependencyTracker() as dependencies:
            DependencyTracker.track("TestClass", "identification", "TestIdentifier")
            self.assertIn(
                ("TestClass", "identification", "TestIdentifier"), dependencies
            )

        # Ensure that the dependencies are cleared after exiting the context
        self.assertFalse(hasattr(dependencies, "dependencies"))
