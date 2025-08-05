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
