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
            manager_class: The class used to manage items within the bucket.
            items (optional): An iterable of items to populate the bucket; defaults to an empty list if not provided.
        """
        super().__init__(manager_class)
        self._data = list(items or [])

    def __or__(self, other):
        """
        Return a new SimpleBucket containing the combined items from this bucket and another bucket or manager instance.
        
        If `other` is a SimpleBucket, its items are appended. If `other` is a manager instance, it is added to the bucket. If `other` is neither, returns a copy of the current bucket.
         
        Returns:
            SimpleBucket: A new bucket with the combined contents.
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
        Return a new SimpleBucket containing manager instances for each ID specified in the 'id__in' filter.
        
        Parameters:
        	id__in (list, optional): List of IDs to generate manager instances for.
        
        Returns:
        	SimpleBucket: A bucket containing one manager instance per provided ID.
        """
        ids = kwargs.get("id__in", [])
        return SimpleBucket(self._manager_class, [self._manager_class() for _ in ids])

    def exclude(self, **kwargs):
        """
        Return an empty SimpleBucket of the same manager class, effectively excluding all items.
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
        """
        return len(self._data)

    def all(self):
        """
        Return a SimpleBucket containing all items managed by this instance.
        
        Returns:
        	SimpleBucket: A bucket with all current items.
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
        Retrieve an item or a slice from the bucket.
        
        Returns:
            If `item` is a slice, returns a new SimpleBucket containing the sliced data; otherwise, returns the item at the specified index.
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
        Return True if the specified item exists in the bucket; otherwise, return False.
        """
        return item in self._data

    def sort(self, key, reverse: bool = False):
        """
        Return a new SimpleBucket with items sorted by the specified key function.
        
        Parameters:
            key: A function that extracts a comparison key from each item.
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
        Raises NotImplementedError to indicate that the update operation is not implemented.
        """
        raise NotImplementedError

    def deactivate(self, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that deactivation is not implemented for this interface.
        """
        raise NotImplementedError

    def getData(self, search_date=None):
        """
        Raises NotImplementedError to indicate that data retrieval must be implemented by subclasses.
        
        Parameters:
            search_date: Optional parameter for filtering data by date.
        """
        raise NotImplementedError

    @classmethod
    def getAttributeTypes(cls):
        """
        Return a dictionary mapping attribute names to their types for the class.
        
        Returns:
            dict: An empty dictionary, indicating no attribute types are defined by default.
        """
        return {}

    @classmethod
    def getAttributes(cls):
        """
        Return a dictionary of attribute definitions for the class.
        
        Returns:
            dict: An empty dictionary, indicating no attributes are defined by default.
        """
        return {}

    @classmethod
    def filter(cls, **kwargs):
        """
        Return a SimpleBucket containing instances of the parent class for each provided ID.
        
        Parameters:
            id__in (list, optional): List of IDs to generate instances for. If not provided, returns an empty bucket.
        
        Returns:
            SimpleBucket: A bucket containing one instance of the parent class for each ID in `id__in`.
        """
        ids = kwargs.get("id__in", [])
        return SimpleBucket(cls._parent_class, [cls._parent_class() for _ in ids])

    @classmethod
    def exclude(cls, **kwargs):
        """
        Return an empty SimpleBucket for the parent class, effectively excluding all items.
        """
        return SimpleBucket(cls._parent_class, [])

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        Return the type of the specified field, always as `str`.
        
        Parameters:
            field_name (str): The name of the field to query.
        
        Returns:
            type: The type of the field, which is always `str`.
        """
        return str

    @classmethod
    def handleInterface(cls):
        """
        Return pre- and post-processing hooks for interface class construction.
        
        The returned `pre` function injects the interface into the class attributes before class creation. The `post` function sets the `_parent_class` attribute on the interface class after class creation.
        
        Returns:
            tuple: A pair of functions `(pre, post)` for use in class construction workflows.
        """
        def pre(name, attrs, interface):
            attrs["Interface"] = interface
            return attrs, interface, None

        def post(new_cls, interface_cls, model):
            """
            Sets the `_parent_class` attribute of the interface class to the provided class.
            
            This links the interface class to its parent class in the hierarchy.
            """
            interface_cls._parent_class = new_cls

        return pre, post


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
        self.assertEqual(tracer.path, ["middles", "end"])
        start_instance = self.StartManager()
        result = PathMap(start_instance).goTo(self.EndManager)
        self.assertIsInstance(result, SimpleBucket)
        self.assertEqual(result.count(), 2)

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
