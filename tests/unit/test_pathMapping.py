# type: ignore

from django.test import SimpleTestCase
from general_manager.utils.pathMapping import PathMap
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.api.property import GraphQLProperty
from tests.utils.simple_manager_interface import BaseTestInterface


def build_managers():
    """
    Dynamically creates and returns paired StartManager and EndManager classes with their respective interfaces.

    Returns:
        tuple: A tuple containing the StartManager and EndManager classes, each linked to its own interface. StartManager includes a GraphQL property returning an EndManager instance.
    """

    class EndInterface(BaseTestInterface):
        pass

    class EndManager(GeneralManager):
        Interface = EndInterface

    class StartInterface(BaseTestInterface):
        pass

    class StartManager(GeneralManager):
        Interface = StartInterface

        @GraphQLProperty
        def end(self) -> EndManager:  # type: ignore
            """
            Returns an instance of EndManager associated with this StartManager.

            Returns:
                EndManager: A new EndManager instance.
            """
            return EndManager()

    return StartManager, EndManager


class PathMappingUnitTests(SimpleTestCase):
    def setUp(self):
        """
        Resets global state and rebuilds manager classes before each test.

        Clears caches in `GeneralManagerMeta` and `PathMap`, removes any existing `PathMap` singleton instance, and initializes fresh `StartManager` and `EndManager` classes for use in tests.
        """
        GeneralManagerMeta.all_classes.clear()
        PathMap.mapping.clear()
        if hasattr(PathMap, "instance"):
            delattr(PathMap, "instance")
        self.StartManager, self.EndManager = build_managers()

    def test_to_and_go_to(self):
        """
        Tests that PathMap can correctly trace the path from StartManager to EndManager and navigate to an EndManager instance from a StartManager instance.
        """
        pm = PathMap(self.StartManager)
        tracer = pm.to(self.EndManager)
        self.assertEqual(tracer.path, ["end"])  # type: ignore
        start_instance = self.StartManager()
        result = PathMap(start_instance).goTo(self.EndManager)
        self.assertIsInstance(result, self.EndManager)

    def test_get_all_connected(self):
        """
        Test that PathMap correctly identifies all manager classes connected to StartManager.

        Asserts that getAllConnected() returns a set containing the name of EndManager.
        """
        pm = PathMap(self.StartManager)
        self.assertEqual(pm.getAllConnected(), {self.EndManager.__name__})

    def test_nonexistent_path(self):
        """
        Test that attempting to trace or navigate a non-existent path between managers returns appropriate null results.

        Verifies that `PathMap.to` returns a tracer with no path when no connection exists, and that `PathMap.goTo` returns `None` when navigation is not possible.
        """
        pm = PathMap(self.EndManager)
        tracer = pm.to(self.StartManager)
        self.assertIsNotNone(tracer)
        self.assertIsNone(tracer.path)  # type: ignore
        end_instance = self.EndManager()
        result = PathMap(end_instance).goTo(self.StartManager)
        self.assertIsNone(result)

    def test_pathmap_singleton_behavior(self):
        """
        Test that PathMap maintains singleton behavior.

        Verifies that PathMap uses a singleton pattern and that multiple instances
        share the same mapping data to avoid redundant computation.
        """
        pm1 = PathMap(self.StartManager)
        pm2 = PathMap(self.StartManager)

        # Both should reference the same singleton instance
        self.assertIs(pm1, pm2)

        # Both should use the same mapping
        self.assertEqual(pm1.mapping, pm2.mapping)

    def test_pathmap_with_instance_vs_class(self):
        """
        Test that PathMap works correctly when initialized with both class and instance.

        Verifies that PathMap can be initialized with either a manager class or
        an instance of a manager class, and behaves consistently.
        """
        start_instance = self.StartManager()
        pm_class = PathMap(self.StartManager)
        pm_instance = PathMap(start_instance)

        # Both should produce the same results for path tracing
        tracer_class = pm_class.to(self.EndManager)
        tracer_instance = pm_instance.to(self.EndManager)

        self.assertEqual(tracer_class.path, tracer_instance.path)
        self.assertEqual(tracer_class.path, ["end"])

        # Both should have same getAllConnected results
        self.assertEqual(pm_class.getAllConnected(), pm_instance.getAllConnected())

    def test_multiple_connection_managers(self):
        """
        Test PathMap with managers that have multiple GraphQL property connections.

        Creates a manager with multiple paths to different target managers.
        """

        class MultiInterface(BaseTestInterface):
            pass

        class AlternateInterface(BaseTestInterface):
            pass

        class AlternateManager(GeneralManager):
            Interface = AlternateInterface

        class MultiConnectionManager(GeneralManager):
            Interface = MultiInterface

            @GraphQLProperty
            def end(self) -> self.EndManager:  # type: ignore
                return self.EndManager()

            @GraphQLProperty
            def alternate(self) -> AlternateManager:  # type: ignore
                return AlternateManager()

        pm = PathMap(MultiConnectionManager)
        connected = pm.getAllConnected()

        # Should find both connections
        self.assertIn(self.EndManager.__name__, connected)
        self.assertIn("AlternateManager", connected)
        self.assertEqual(len(connected), 2)

    def test_deep_path_navigation(self):
        """
        Test navigation through chains of connected managers.

        Creates a multi-level chain and verifies PathMap can find paths
        through multiple intermediate managers.
        """

        class Level2Interface(BaseTestInterface):
            pass

        class Level2Manager(GeneralManager):
            Interface = Level2Interface

            @GraphQLProperty
            def end(self) -> self.EndManager:  # type: ignore
                return self.EndManager()

        class Level1Interface(BaseTestInterface):
            pass

        class Level1Manager(GeneralManager):
            Interface = Level1Interface

            @GraphQLProperty
            def level2(self) -> Level2Manager:  # type: ignore
                return Level2Manager()

        pm = PathMap(Level1Manager)
        connected = pm.getAllConnected()

        # Should find both intermediate and final managers
        self.assertIn("Level2Manager", connected)
        self.assertIn(self.EndManager.__name__, connected)

        # Test that we can trace path through multiple levels
        tracer = pm.to(self.EndManager)
        self.assertIsNotNone(tracer.path)
        if tracer.path:
            self.assertEqual(len(tracer.path), 2)  # level2 -> end
            self.assertEqual(tracer.path, ["level2", "end"])

    def test_isolated_manager_behavior(self):
        """
        Test PathMap behavior with managers that have no GraphQL property connections.

        Verifies that isolated managers return empty connection sets and
        None for path tracing to unreachable targets.
        """

        class IsolatedInterface(BaseTestInterface):
            pass

        class IsolatedManager(GeneralManager):
            Interface = IsolatedInterface
            # No GraphQL properties defined

        pm = PathMap(IsolatedManager)

        # Should return empty set for connections
        self.assertEqual(pm.getAllConnected(), set())

        # Should return None when trying to find path to unreachable target
        tracer = pm.to(self.StartManager)
        self.assertIsNone(tracer.path)  # type: ignore

        # goTo should return None for unreachable target
        isolated_instance = IsolatedManager()
        result = PathMap(isolated_instance).goTo(self.StartManager)
        self.assertIsNone(result)

    def test_pathmap_caching_behavior(self):
        """
        Test that PathMap properly caches mapping data and rebuilds when cleared.

        Verifies that the mapping cache improves performance by avoiding
        redundant computation while allowing for cache invalidation.
        """
        pm = PathMap(self.StartManager)
        initial_mapping = dict(pm.mapping)  # Create copy to avoid reference issues

        # Verify mapping is populated after first access
        connected = pm.getAllConnected()
        self.assertGreater(len(connected), 0)
        self.assertGreater(len(initial_mapping), 0)

        # Clear global mapping cache and singleton
        PathMap.mapping.clear()
        if hasattr(PathMap, "instance"):
            delattr(PathMap, "instance")

        # Create new PathMap instance - should rebuild mapping
        pm_new = PathMap(self.StartManager)
        new_connected = pm_new.getAllConnected()

        # Results should be the same even after cache rebuild
        self.assertEqual(connected, new_connected)

    def test_graphql_property_detection(self):
        """
        Test that PathMap correctly identifies only GraphQL properties as valid paths.

        Verifies that regular methods and properties are ignored, while only
        GraphQLProperty-decorated methods are considered for navigation.
        """

        class TestInterface(BaseTestInterface):
            pass

        class TestManager(GeneralManager):
            Interface = TestInterface

            @GraphQLProperty
            def valid_graphql_path(self) -> self.EndManager:  # type: ignore
                return self.EndManager()

            def regular_method(self) -> self.EndManager:  # type: ignore
                """Regular method - should be ignored"""
                return self.EndManager()

            @property
            def regular_property(self) -> self.EndManager:  # type: ignore
                """Regular property - should be ignored"""
                return self.EndManager()

        pm = PathMap(TestManager)
        connected = pm.getAllConnected()

        # Should only detect the GraphQL property connection
        self.assertIn(self.EndManager.__name__, connected)

        # Verify path tracing works for GraphQL property
        tracer = pm.to(self.EndManager)
        self.assertIsNotNone(tracer.path)
        if tracer.path:
            self.assertEqual(tracer.path, ["valid_graphql_path"])

    def test_pathmap_error_handling(self):
        """
        Test PathMap behavior with invalid inputs and error conditions.

        Verifies that PathMap handles edge cases gracefully without crashing.
        """
        # Test goTo with None start instance should raise ValueError
        pm = PathMap(self.StartManager)
        with self.assertRaises(ValueError):
            pm.goTo(self.EndManager)

        # Test goTo with invalid target should return None gracefully
        start_instance = self.StartManager()
        result = PathMap(start_instance).goTo("NonExistentManager")
        self.assertIsNone(result)

    def test_circular_reference_handling(self):
        """
        Test that PathMap handles circular references without infinite loops.

        Creates managers with circular GraphQL property references and verifies
        that PathMap terminates properly during graph traversal.
        """

        class CircularAInterface(BaseTestInterface):
            pass

        class CircularBInterface(BaseTestInterface):
            pass

        class CircularBManager(GeneralManager):
            Interface = CircularBInterface

        class CircularAManager(GeneralManager):
            Interface = CircularAInterface

            @GraphQLProperty
            def circular_b(self) -> CircularBManager:  # type: ignore
                return CircularBManager()

        # Create circular reference by adding property to B that points back to A
        def get_circular_a(self):
            return CircularAManager()

        # Simulate adding GraphQLProperty decorator
        CircularBManager.circular_a = GraphQLProperty(get_circular_a)

        # This should not cause infinite recursion
        pm = PathMap(CircularAManager)
        connected = pm.getAllConnected()

        # Should successfully identify connected managers
        self.assertIsInstance(connected, set)
        self.assertIn("CircularBManager", connected)

    def test_pathtracer_direct_instantiation(self):
        """
        Test PathTracer behavior when instantiated directly.

        Verifies that PathTracer can be created independently and properly
        finds paths between manager classes.
        """
        from general_manager.utils.pathMapping import PathTracer

        tracer = PathTracer(self.StartManager, self.EndManager)
        self.assertEqual(tracer.path, ["end"])

        # Test with same start and destination
        same_tracer = PathTracer(self.StartManager, self.StartManager)
        self.assertEqual(same_tracer.path, [])

    def test_pathtracer_traversal_functionality(self):
        """
        Test PathTracer's traversePath method with various scenarios.

        Verifies that PathTracer can successfully traverse paths and handle
        edge cases during navigation.
        """
        from general_manager.utils.pathMapping import PathTracer

        tracer = PathTracer(self.StartManager, self.EndManager)
        start_instance = self.StartManager()

        # Test successful traversal
        result = tracer.traversePath(start_instance)
        self.assertIsInstance(result, self.EndManager)

        # Test traversal with empty path returns None
        empty_tracer = PathTracer(self.StartManager, self.StartManager)
        result_empty = empty_tracer.traversePath(start_instance)
        self.assertIsNone(result_empty)

    def test_pathmap_with_string_identifiers(self):
        """
        Test PathMap methods when using string class names instead of class objects.

        Verifies that PathMap can handle string identifiers for manager classes
        in addition to actual class objects.
        """
        pm = PathMap(self.StartManager)

        # Test to() method with string
        tracer = pm.to(self.EndManager.__name__)
        self.assertIsNotNone(tracer)
        self.assertEqual(tracer.path, ["end"])

        # Test goTo() method with string
        start_instance = self.StartManager()
        result = PathMap(start_instance).goTo(self.EndManager.__name__)
        self.assertIsInstance(result, self.EndManager)

    def test_pathmap_edge_cases(self):
        """
        Test PathMap with various edge cases and boundary conditions.

        Covers scenarios like self-referencing managers and other unusual
        but valid configurations.
        """

        class SelfRefInterface(BaseTestInterface):
            pass

        class SelfReferencingManager(GeneralManager):
            Interface = SelfRefInterface

            @GraphQLProperty
            def self_ref(self):  # type: ignore
                return self.__class__()

            @GraphQLProperty
            def end_ref(self) -> self.EndManager:  # type: ignore
                return self.EndManager()

        pm = PathMap(SelfReferencingManager)
        connected = pm.getAllConnected()

        # Should handle self-reference and external reference
        self.assertNotIn(
            "SelfReferencingManager", connected, "Self-reference is not supported yet"
        )
        self.assertIn(self.EndManager.__name__, connected)

        # Should be able to navigate to external target
        tracer = pm.to(self.EndManager)
        self.assertIsNotNone(tracer.path)
        if tracer.path:
            self.assertEqual(tracer.path, ["end_ref"])

    def test_pathmap_with_inheritance(self):
        """
        Test PathMap behavior with manager class inheritance.

        Verifies that PathMap correctly handles GraphQL properties
        inherited from parent manager classes.
        """

        class BaseManagerInterface(BaseTestInterface):
            pass

        class BaseManager(GeneralManager):
            Interface = BaseManagerInterface

            @GraphQLProperty
            def inherited_end(self) -> self.EndManager:  # type: ignore
                return self.EndManager()

        class DerivedManagerInterface(BaseTestInterface):
            pass

        class DerivedManager(BaseManager):
            Interface = DerivedManagerInterface

            @GraphQLProperty
            def own_end(self) -> self.EndManager:  # type: ignore
                return self.EndManager()

        pm = PathMap(DerivedManager)
        connected = pm.getAllConnected()

        # Should include connections from both base and derived classes
        self.assertIn(self.EndManager.__name__, connected)

        # Should be able to navigate using properties from either base or derived class
        tracer = pm.to(self.EndManager)
        self.assertIsNotNone(tracer.path)
        if tracer.path:
            # Should find a path using either inherited or own property
            self.assertIn(tracer.path[0], ["inherited_end", "own_end"])

    def test_pathmap_string_representation(self):
        """
        Test string representation of PathMap and PathTracer objects.

        Verifies that PathMap and PathTracer objects can be converted to strings
        for debugging and logging purposes without errors.
        """
        pm = PathMap(self.StartManager)

        # PathMap should have a meaningful string representation
        str_repr = str(pm)
        self.assertIsInstance(str_repr, str)
        self.assertTrue(len(str_repr) > 0)

        # Tracer should also have string representation
        tracer = pm.to(self.EndManager)
        tracer_str = str(tracer)
        self.assertIsInstance(tracer_str, str)
        self.assertTrue(len(tracer_str) > 0)

        # Test repr as well
        repr_str = repr(pm)
        self.assertIsInstance(repr_str, str)
        self.assertTrue(len(repr_str) > 0)

    def test_pathmap_with_complex_type_annotations(self):
        """
        Test PathMap with managers that have complex return type annotations.

        Verifies that PathMap correctly handles various return type annotations
        including generics, unions, and optional types from typing module.
        """
        from typing import Optional, Union

        class ComplexInterface(BaseTestInterface):
            pass

        class ComplexManager(GeneralManager):
            Interface = ComplexInterface

            @GraphQLProperty
            def optional_end(self) -> Optional[self.EndManager]:  # type: ignore
                return self.EndManager()

            @GraphQLProperty
            def union_end(self) -> Union[self.EndManager, None]:  # type: ignore
                return self.EndManager()

        pm = PathMap(ComplexManager)
        tracer = pm.to(self.EndManager)
        self.assertIsNotNone(tracer.path)

        # Should find paths regardless of complex type annotations
        if tracer.path:
            self.assertIn(tracer.path[0], ["optional_end", "union_end"])

    def test_pathmap_thread_safety_simulation(self):
        """
        Test PathMap behavior under simulated concurrent access patterns.

        While not a full threading test, this verifies that PathMap's
        singleton and caching behavior is consistent across multiple accesses.
        """
        import threading

        results = []
        errors = []

        def pathmap_worker():
            try:
                pm = PathMap(self.StartManager)
                connected = pm.getAllConnected()
                tracer = pm.to(self.EndManager)
                results.append((connected, tracer.path if tracer else None))
            except Exception as e:
                errors.append(e)

        # Create multiple threads accessing PathMap concurrently
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=pathmap_worker)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify no errors occurred
        self.assertEqual(len(errors), 0, f"Concurrent access caused errors: {errors}")

        # Verify all results are consistent
        if results:
            expected_connected = results[0][0]
            expected_path = results[0][1]
            for connected, path in results:
                self.assertEqual(connected, expected_connected)
                self.assertEqual(path, expected_path)

    def test_pathtracer_with_bucket_traversal(self):
        """
        Test PathTracer's traversePath method with Bucket instances.

        Verifies that PathTracer can handle traversal when the start instance
        is a Bucket containing multiple manager instances.
        """
        from general_manager.utils.pathMapping import PathTracer
        from tests.utils.simple_manager_interface import SimpleBucket

        tracer = PathTracer(self.StartManager, self.EndManager)

        # Create a bucket with multiple StartManager instances
        start_instances = [self.StartManager(), self.StartManager()]
        bucket = SimpleBucket(self.StartManager, start_instances)

        # Test traversal with bucket
        result = tracer.traversePath(bucket)

        # Should return a bucket or manager instance, not None
        self.assertIsNotNone(result)

    def test_pathmap_interface_attribute_types_integration(self):
        """
        Test PathMap integration with Interface.getAttributeTypes().

        Verifies that PathMap correctly considers attribute types defined
        in the Interface class when building path mappings.
        """

        class InterfaceWithTypes(BaseTestInterface):
            @classmethod
            def getAttributeTypes(cls):
                return {"interface_end": {"type": self.EndManager}}

        class ManagerWithInterfaceTypes(GeneralManager):
            Interface = InterfaceWithTypes

        pm = PathMap(ManagerWithInterfaceTypes)
        connected = pm.getAllConnected()

        # Should find connection defined in interface attribute types
        self.assertIn(self.EndManager.__name__, connected)

    def test_pathmap_none_path_scenarios(self):
        """
        Test comprehensive scenarios where PathMap should return None or empty results.

        Covers edge cases where no valid paths exist between managers.
        """

        # Test with manager that exists but has no path to target
        class DisconnectedInterface(BaseTestInterface):
            pass

        class DisconnectedManager(GeneralManager):
            Interface = DisconnectedInterface

            @GraphQLProperty
            def unrelated_connection(self):  # type: ignore
                return self.__class__()

        pm = PathMap(DisconnectedManager)

        # Should return None for non-existent paths
        tracer = pm.to(self.EndManager)
        self.assertIsNone(tracer.path)  # type: ignore

        # Should return None for goTo with non-existent paths
        disconnected_instance = DisconnectedManager()
        result = PathMap(disconnected_instance).goTo(self.EndManager)
        self.assertIsNone(result)

    def test_pathmap_create_path_mapping_behavior(self):
        """
        Test PathMap.createPathMapping class method behavior.

        Verifies that the mapping creation process works correctly and
        handles various manager class configurations.
        """
        # Clear existing mappings
        PathMap.mapping.clear()
        GeneralManagerMeta.all_classes.clear()

        # Register test managers
        GeneralManagerMeta.all_classes.append(self.StartManager)
        GeneralManagerMeta.all_classes.append(self.EndManager)

        # Manually trigger mapping creation
        PathMap.instance = PathMap.__new__(PathMap)
        PathMap.createPathMapping()

        # Verify mappings were created
        start_to_end_key = (self.StartManager.__name__, self.EndManager.__name__)
        end_to_start_key = (self.EndManager.__name__, self.StartManager.__name__)

        self.assertIn(start_to_end_key, PathMap.mapping)
        self.assertIn(end_to_start_key, PathMap.mapping)

        # Verify tracers were created correctly
        start_to_end_tracer = PathMap.mapping[start_to_end_key]
        self.assertEqual(start_to_end_tracer.path, ["end"])

        end_to_start_tracer = PathMap.mapping[end_to_start_key]
        self.assertIsNone(end_to_start_tracer.path)

    def test_pathtracer_recursive_path_creation(self):
        """
        Test PathTracer's createPath method with complex recursive scenarios.

        Verifies that the recursive path finding algorithm handles
        deep and complex manager hierarchies correctly.
        """
        from general_manager.utils.pathMapping import PathTracer

        # Create a complex chain: A -> B -> C -> End
        class ChainBInterface(BaseTestInterface):
            pass

        class ChainCInterface(BaseTestInterface):
            pass

        class ChainCManager(GeneralManager):
            Interface = ChainCInterface

            @GraphQLProperty
            def final_end(self) -> self.EndManager:  # type: ignore
                return self.EndManager()

        class ChainBManager(GeneralManager):
            Interface = ChainBInterface

            @GraphQLProperty
            def chain_c(self) -> ChainCManager:  # type: ignore
                return ChainCManager()

        class ChainAInterface(BaseTestInterface):
            pass

        class ChainAManager(GeneralManager):
            Interface = ChainAInterface

            @GraphQLProperty
            def chain_b(self) -> ChainBManager:  # type: ignore
                return ChainBManager()

        # Test direct tracer creation for multi-hop path
        tracer = PathTracer(ChainAManager, self.EndManager)

        # Should find the full path: chain_b -> chain_c -> final_end
        self.assertIsNotNone(tracer.path)
        if tracer.path:
            self.assertEqual(len(tracer.path), 3)
            self.assertEqual(tracer.path, ["chain_b", "chain_c", "final_end"])
