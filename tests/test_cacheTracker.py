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
        """
        Tests that DependencyTracker does not track dependencies when not used as a context manager.
        """
        dependencies = DependencyTracker()
        self.assertFalse(hasattr(dependencies, "dependencies"))

    def test_dependency_tracker_with_exception(self):
        """
        Tests that DependencyTracker clears dependencies after an exception within its context.
        
        Verifies that dependencies added inside the context are not retained after a RuntimeError is raised and the context is exited.
        """
        with self.assertRaises(RuntimeError), DependencyTracker() as dependencies:
            dependencies.add(("TestClass", "identification", "TestIdentifier"))
            raise RuntimeError

        # Ensure that the dependencies are cleared after the exception
        self.assertFalse(hasattr(dependencies, "dependencies"))  # type: ignore

    def test_dependency_tracker_with_multiple_dependencies(self):
        """
        Tests that multiple dependencies can be added and tracked within a DependencyTracker context, and verifies that all dependencies are cleared after exiting the context.
        """
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
        """
        Tests that DependencyTracker handles empty dependencies correctly.
        
        Enters a DependencyTracker context without adding any dependencies and asserts that the dependency set is empty. Verifies that dependencies are cleared after exiting the context.
        """
        with DependencyTracker() as dependencies:
            self.assertEqual(len(dependencies), 0)

        # Ensure that the dependencies are cleared after exiting the context
        self.assertFalse(hasattr(dependencies, "dependencies"))

    def test_dependency_tracker_track(self):
        """
        Tests that the DependencyTracker.track method adds a dependency within the context
        and that dependencies are cleared after exiting the context.
        """
        with DependencyTracker() as dependencies:
            DependencyTracker.track("TestClass", "identification", "TestIdentifier")
            self.assertIn(
                ("TestClass", "identification", "TestIdentifier"), dependencies
            )

        # Ensure that the dependencies are cleared after exiting the context
        self.assertFalse(hasattr(dependencies, "dependencies"))
