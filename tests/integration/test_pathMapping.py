from django.test import SimpleTestCase
from general_manager.utils.pathMapping import PathMap
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.generalManager import GeneralManager
from general_manager.api.property import GraphQLProperty

from tests.utils.simple_manager_interface import BaseTestInterface
from tests.utils.simple_manager_interface import SimpleBucket


def build_bucket_managers():
    """
    Constructs and returns a hierarchy of manager classes with associated interfaces and relationships for testing.

    Returns:
        tuple: A tuple containing the StartManager, MiddleManager, and EndManager classes, each linked via interface and property relationships to simulate a nested manager structure.
    """

    class EndInterface(BaseTestInterface):
        pass

    class EndManager(GeneralManager):
        Interface = EndInterface

    class MiddleInterface(BaseTestInterface):
        @classmethod
        def getAttributeTypes(cls):
            """
            Return an empty dictionary representing the attribute types for the class.
            """
            return {}

        @classmethod
        def getAttributes(cls):
            """
            Return an empty dictionary representing the class's attributes.

            Intended to be overridden by subclasses to provide attribute metadata.
            """
            return {}

    class MiddleManager(GeneralManager):
        Interface = MiddleInterface

        @GraphQLProperty
        def end(self) -> EndManager:  # type: ignore
            """
            Returns an instance of EndManager associated with this MiddleManager.
            """
            return EndManager()

    class StartInterface(BaseTestInterface):
        @classmethod
        def getAttributeTypes(cls):
            """
            Return a dictionary describing the attribute types for the class, specifying that 'middles' is of type MiddleManager.
            """
            return {"middles": {"type": MiddleManager}}

        @classmethod
        def getAttributes(cls):
            """
            Return a dictionary describing the attributes of the class, with 'middles' as an attribute.

            Returns:
                dict: A dictionary with the key 'middles' mapped to an empty dictionary.
            """
            return {"middles": {}}

    class StartManager(GeneralManager):
        Interface = StartInterface

        @property
        def middles(self) -> SimpleBucket:  # type: ignore
            """
            Return a SimpleBucket containing two instances of MiddleManager.
            """
            return SimpleBucket(MiddleManager, [MiddleManager(), MiddleManager()])

    return StartManager, MiddleManager, EndManager


class PathMappingIntegrationTests(SimpleTestCase):
    def setUp(self):
        """
        Reset global registries and rebuild manager classes before each test.

        Clears all registered manager classes and path mappings, removes any singleton instance of `PathMap`, and initializes the `StartManager`, `MiddleManager`, and `EndManager` classes for use in tests.
        """
        GeneralManagerMeta.all_classes.clear()
        PathMap.mapping.clear()
        if hasattr(PathMap, "instance"):
            delattr(PathMap, "instance")
        (
            self.StartManager,
            self.MiddleManager,
            self.EndManager,
        ) = build_bucket_managers()

    def test_traverse_bucket_path(self):
        """
        Test that PathMap correctly identifies and traverses the path from StartManager to EndManager.

        Verifies that the path is accurately mapped as ["middles", "end"], and that traversing from a StartManager instance to EndManager yields a SimpleBucket containing two items.
        """
        pm = PathMap(self.StartManager)
        tracer = pm.to(self.EndManager)
        self.assertEqual(tracer.path, ["middles", "end"])  # type: ignore
        start_instance = self.StartManager()
        result = PathMap(start_instance).goTo(self.EndManager)
        self.assertIsInstance(result, SimpleBucket)
        self.assertEqual(result.count(), 2)  # type: ignore

    def test_get_all_connected(self):
        """
        Tests that the PathMap correctly identifies all manager classes connected to StartManager.

        Asserts that the set of connected manager class names includes both MiddleManager and EndManager.
        """
        pm = PathMap(self.StartManager)
        self.assertSetEqual(
            pm.getAllConnected(),
            {self.MiddleManager.__name__, self.EndManager.__name__},
        )
