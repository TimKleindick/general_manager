from __future__ import annotations

from typing import ClassVar

from django.test import SimpleTestCase

from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket


class ProjectInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    data_store: ClassVar[dict[int, dict[str, str]]] = {
        1: {"name": "Alpha", "status": "public", "secret": "hidden"},
        2: {"name": "Beta", "status": "private", "secret": "hidden"},
    }

    def get_data(self, search_date=None):
        """
        Return the stored data dictionary for this instance's id.
        
        Parameters:
            search_date (optional): Ignored and kept for interface compatibility.
        
        Returns:
            dict: Attribute dictionary for the instance identified by self.identification["id"].
        """
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        """
        Return the attribute type descriptors used for indexing and schema generation.
        
        Returns:
            dict: A mapping from attribute name to a descriptor dict containing a "type" key with the Python type for that attribute (e.g., {"name": {"type": str}}).
        """
        return {
            "name": {"type": str},
            "status": {"type": str},
        }

    @classmethod
    def get_attributes(cls):
        """
        Return a mapping of attribute names to callables that extract those attributes from an interface instance.
        
        Each callable accepts an interface instance and returns the corresponding attribute value. The mapping includes:
        - "name": returns the project's name
        - "status": returns the project's status
        
        Returns:
            dict[str, Callable[[object], object]]: Mapping of attribute name to extractor callable.
        """
        return {
            "name": lambda interface: interface.get_data()["name"],
            "status": lambda interface: interface.get_data()["status"],
        }

    @classmethod
    def filter(cls, **kwargs):
        """
        Return a SimpleBucket of Project instances filtered by the optional `id__in` keyword.
        
        Parameters:
            **kwargs: Optional keyword arguments controlling the filter.
                id__in (iterable[int], optional): Iterable of IDs to include; if omitted, all stored IDs are returned.
        
        Returns:
            SimpleBucket: A bucket of manager instances corresponding to the requested IDs.
        """
        ids = kwargs.get("id__in")
        if ids is None:
            ids = list(cls.data_store.keys())
        return SimpleBucket(
            cls._parent_class, [cls._parent_class(id=val) for val in ids]
        )


class Project(GeneralManager):
    Interface = ProjectInterface

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"], filters=["status"])
        ]

        @staticmethod
        def to_document(instance: "Project") -> dict:
            """
            Convert a Project instance into a dictionary document for indexing.
            
            Returns:
                dict: A mapping with keys "name", "status", and "secret" extracted from the instance's interface data.
            """
            data = instance._interface.get_data()
            return {
                "name": data["name"],
                "status": data["status"],
                "secret": data["secret"],
            }


class SearchIndexerTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Initialize general manager classes required by the test suite.
        
        Registers the Project class with GeneralmanagerConfig so manager and model metadata are prepared before each test.
        """
        GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])

    def test_indexer_indexes_configured_fields(self) -> None:
        """
        Verify that SearchIndexer indexes only the fields configured for the index and respects search filters.
        
        Indexes a Project instance into a DevSearchBackend, searches the "global" index for "Alpha" with filter status="public", and asserts that exactly one hit is returned, the hit identifies the instance by {"id": 1}, and the indexed document does not include the "secret" field.
        """
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)

        instance = Project(id=1)
        indexer.index_instance(instance)

        result = backend.search("global", "Alpha", filters={"status": "public"})
        assert result.total == 1
        hit = result.hits[0]
        assert hit.identification == {"id": 1}
        assert "secret" not in hit.data

    def test_indexer_delete_instance(self) -> None:
        """
        Verifies that deleting a previously indexed instance removes it from the search index.
        
        Indexes a Project(id=1), deletes that indexed instance, and asserts that searching the "global" index for "Alpha" with filter status="public" returns no results.
        """
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)

        instance = Project(id=1)
        indexer.index_instance(instance)
        indexer.delete_instance(instance)

        result = backend.search("global", "Alpha", filters={"status": "public"})
        assert result.total == 0

    def test_indexer_reindex_manager(self) -> None:
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)

        indexer.reindex_manager(Project)
        result = backend.search("global", "Alpha", filters={"status": "public"})
        assert result.total == 1