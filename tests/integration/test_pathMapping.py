from django.test import SimpleTestCase
from general_manager.utils.pathMapping import PathMap
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.interface.baseInterface import InterfaceBase
from general_manager.api.property import GraphQLProperty
from general_manager.bucket.baseBucket import Bucket


class SimpleBucket(Bucket):
    def __init__(self, manager_class, items=None):
        super().__init__(manager_class)
        self._data = list(items or [])

    def __or__(self, other):
        if isinstance(other, SimpleBucket):
            return SimpleBucket(self._manager_class, self._data + other._data)
        if isinstance(other, self._manager_class):
            return SimpleBucket(self._manager_class, self._data + [other])
        return SimpleBucket(self._manager_class, list(self._data))

    def __iter__(self):
        return iter(self._data)

    def filter(self, **kwargs):
        ids = kwargs.get("id__in", [])
        return SimpleBucket(self._manager_class, [self._manager_class() for _ in ids])

    def exclude(self, **kwargs):
        return SimpleBucket(self._manager_class, [])

    def first(self):
        return self._data[0] if self._data else None

    def last(self):
        return self._data[-1] if self._data else None

    def count(self):
        return len(self._data)

    def all(self):
        return SimpleBucket(self._manager_class, self._data)

    def get(self, **kwargs):
        if len(self._data) == 1:
            return self._data[0]
        raise ValueError("get() requires exactly one item")

    def __getitem__(self, item):
        if isinstance(item, slice):
            return SimpleBucket(self._manager_class, self._data[item])
        return self._data[item]

    def __len__(self):
        return len(self._data)

    def __contains__(self, item):
        return item in self._data

    def sort(self, key, reverse: bool = False):
        sorted_data = sorted(self._data, key=key, reverse=reverse)
        return SimpleBucket(self._manager_class, sorted_data)


class BaseTestInterface(InterfaceBase):
    input_fields: dict = {}

    @classmethod
    def create(cls, *args, **kwargs):
        raise NotImplementedError

    def update(self, *args, **kwargs):
        raise NotImplementedError

    def deactivate(self, *args, **kwargs):
        raise NotImplementedError

    def getData(self, search_date=None):
        raise NotImplementedError

    @classmethod
    def getAttributeTypes(cls):
        return {}

    @classmethod
    def getAttributes(cls):
        return {}

    @classmethod
    def filter(cls, **kwargs):
        ids = kwargs.get("id__in", [])
        return SimpleBucket(cls._parent_class, [cls._parent_class() for _ in ids])

    @classmethod
    def exclude(cls, **kwargs):
        return SimpleBucket(cls._parent_class, [])

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        return str

    @classmethod
    def handleInterface(cls):
        def pre(name, attrs, interface):
            attrs["Interface"] = interface
            return attrs, interface, None

        def post(new_cls, interface_cls, model):
            interface_cls._parent_class = new_cls

        return pre, post


def build_bucket_managers():
    class EndInterface(BaseTestInterface):
        pass

    class EndManager(GeneralManager):
        Interface = EndInterface

    class MiddleInterface(BaseTestInterface):
        @classmethod
        def getAttributeTypes(cls):
            return {}

        @classmethod
        def getAttributes(cls):
            return {}

    class MiddleManager(GeneralManager):
        Interface = MiddleInterface

        @GraphQLProperty
        def end(self) -> EndManager:  # type: ignore
            return EndManager()

    class StartInterface(BaseTestInterface):
        @classmethod
        def getAttributeTypes(cls):
            return {"middles": {"type": MiddleManager}}

        @classmethod
        def getAttributes(cls):
            return {"middles": {}}

    class StartManager(GeneralManager):
        Interface = StartInterface

        @property
        def middles(self) -> SimpleBucket:  # type: ignore
            return SimpleBucket(MiddleManager, [MiddleManager(), MiddleManager()])

    return StartManager, MiddleManager, EndManager


class PathMappingIntegrationTests(SimpleTestCase):
    def setUp(self):
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
        pm = PathMap(self.StartManager)
        tracer = pm.to(self.EndManager)
        self.assertEqual(tracer.path, ["middles", "end"])
        start_instance = self.StartManager()
        result = PathMap(start_instance).goTo(self.EndManager)
        self.assertIsInstance(result, SimpleBucket)
        self.assertEqual(result.count(), 2)

    def test_get_all_connected(self):
        pm = PathMap(self.StartManager)
        self.assertSetEqual(
            pm.getAllConnected(),
            {self.MiddleManager.__name__, self.EndManager.__name__},
        )
