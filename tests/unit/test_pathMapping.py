from django.test import SimpleTestCase
from general_manager.utils.pathMapping import PathMap
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.api.property import GraphQLProperty
from general_manager.bucket.baseBucket import Bucket


class SimpleBucket(Bucket):
    def __init__(self, manager_class, items=None):
        """
        Initialize a SimpleBucket with a manager class and optional items.
        
        Parameters:
            manager_class: The class associated with the bucket's items.
            items (optional): An iterable of items to populate the bucket.
        """
        super().__init__(manager_class)
        self._data = list(items or [])

    def __or__(self, other):
        """
        Return a new SimpleBucket containing items from this bucket combined with another SimpleBucket or a single manager instance.
        
        If `other` is a SimpleBucket, its items are appended. If `other` is a manager instance, it is added to the current items. If `other` is neither, returns a copy of the current bucket.
        """
        if isinstance(other, SimpleBucket):
            return SimpleBucket(self._manager_class, self._data + other._data)
        if isinstance(other, self._manager_class):
            return SimpleBucket(self._manager_class, self._data + [other])
        return SimpleBucket(self._manager_class, list(self._data))

    def __iter__(self):
        """
        Return an iterator over the items contained in the bucket.
        """
        return iter(self._data)

    def filter(self, **kwargs):
        """
        Return a SimpleBucket containing new instances of the manager class for each ID in the provided `id__in` list.
        
        Parameters:
        	id__in (list, optional): List of IDs to filter by. Defaults to an empty list if not provided.
        
        Returns:
        	SimpleBucket: A bucket with one new manager class instance per ID in `id__in`.
        """
        ids = kwargs.get("id__in", [])
        return SimpleBucket(self._manager_class, [self._manager_class() for _ in ids])

    def exclude(self, **kwargs):
        """
        Return an empty SimpleBucket, effectively excluding all items regardless of the provided criteria.
        """
        return SimpleBucket(self._manager_class, [])

    def first(self):
        """
        Return the first item in the bucket, or None if the bucket is empty.
        """
        return self._data[0] if self._data else None

    def last(self):
        """
        Return the last item in the bucket, or None if the bucket is empty.
        """
        return self._data[-1] if self._data else None

    def count(self):
        """
        Return the number of items contained in the bucket.
        
        Returns:
        	int: The count of items in the bucket.
        """
        return len(self._data)

    def all(self):
        """
        Return a new SimpleBucket containing all items in the current bucket.
        """
        return SimpleBucket(self._manager_class, self._data)

    def get(self, **kwargs):
        """
        Return the single item in the bucket if exactly one exists.
        
        Raises:
            ValueError: If the bucket does not contain exactly one item.
        Returns:
            The single item contained in the bucket.
        """
        if len(self._data) == 1:
            return self._data[0]
        raise ValueError("get() requires exactly one item")

    def __getitem__(self, item):
        """
        Return a sliced or indexed item from the bucket.
        
        If a slice is provided, returns a new SimpleBucket containing the sliced data. If an integer index is provided, returns the corresponding item.
        """
        if isinstance(item, slice):
            return SimpleBucket(self._manager_class, self._data[item])
        return self._data[item]

    def __len__(self):
        """
        Return the number of items contained in the bucket.
        """
        return len(self._data)

    def __contains__(self, item):
        """
        Return True if the specified item exists in the bucket's data.
        """
        return item in self._data

    def sort(self, key, reverse: bool = False):
        """
        Return a new SimpleBucket with items sorted by the given key function.
        
        Parameters:
        	key (callable): Function used to extract a comparison key from each item.
        	reverse (bool): If True, sort in descending order. Defaults to False.
        
        Returns:
        	SimpleBucket: A new bucket containing the sorted items.
        """
        sorted_data = sorted(self._data, key=key, reverse=reverse)
        return SimpleBucket(self._manager_class, sorted_data)


class BaseTestInterface(InterfaceBase):
    input_fields: dict = {}

    @classmethod
    def create(cls, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that the create operation must be implemented by subclasses.
        """
        raise NotImplementedError

    def update(self, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that the update operation must be implemented by subclasses.
        """
        raise NotImplementedError

    def deactivate(self, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that deactivation is not implemented for this interface.
        """
        raise NotImplementedError

    def getData(self, search_date=None):
        """
        Raises NotImplementedError to indicate that subclasses must implement data retrieval logic.
        
        Parameters:
            search_date: Optional parameter for filtering data by date.
        """
        raise NotImplementedError

    @classmethod
    def getAttributeTypes(cls):
        """
        Return an empty dictionary representing attribute types for the class.
        """
        return {}

    @classmethod
    def getAttributes(cls):
        """
        Return an empty dictionary representing the attributes of the class.
        """
        return {}

    @classmethod
    def filter(cls, **kwargs):
        """
        Return a SimpleBucket containing new instances of the parent class for each ID in the provided `id__in` list.
        
        Parameters:
        	id__in (list, optional): List of IDs to generate instances for. If not provided, returns an empty SimpleBucket.
        
        Returns:
        	SimpleBucket: A bucket with one new parent class instance per ID in `id__in`.
        """
        ids = kwargs.get("id__in", [])
        return SimpleBucket(cls._parent_class, [cls._parent_class() for _ in ids])

    @classmethod
    def exclude(cls, **kwargs):
        """
        Return an empty SimpleBucket for the parent class, ignoring any filter criteria.
        """
        return SimpleBucket(cls._parent_class, [])

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        Return the type associated with a given field name.
        
        Parameters:
            field_name (str): The name of the field to query.
        
        Returns:
            type: The type assigned to the specified field (always `str`).
        """
        return str

    @classmethod
    def handleInterface(cls):
        """
        Return pre- and post-processing hooks for integrating an interface with a class.
        
        The returned `pre` function injects the interface into the class attributes before class creation. The `post` function sets the `_parent_class` attribute of the interface class to the newly created class.
        
        Returns:
            tuple: A pair of functions (`pre`, `post`) for use in class construction workflows.
        """
        def pre(name, attrs, interface):
            attrs["Interface"] = interface
            return attrs, interface, None

        def post(new_cls, interface_cls, model):
            """
            Sets the `_parent_class` attribute of the interface class to the newly created class.
            """
            interface_cls._parent_class = new_cls

        return pre, post


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
        self.assertEqual(tracer.path, ["end"])
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
        self.assertIsNone(tracer.path)
        end_instance = self.EndManager()
        result = PathMap(end_instance).goTo(self.StartManager)
        self.assertIsNone(result)
