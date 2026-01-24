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
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        return {
            "name": {"type": str},
            "status": {"type": str},
        }

    @classmethod
    def get_attributes(cls):
        return {
            "name": lambda interface: interface.get_data()["name"],
            "status": lambda interface: interface.get_data()["status"],
        }

    @classmethod
    def filter(cls, **kwargs):
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
            data = instance._interface.get_data()
            return {
                "name": data["name"],
                "status": data["status"],
                "secret": data["secret"],
            }


class SearchIndexerTests(SimpleTestCase):
    def setUp(self) -> None:
        GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])

    def test_indexer_indexes_configured_fields(self) -> None:
        backend = DevSearchBackend()
        indexer = SearchIndexer(backend)

        instance = Project(id=1)
        indexer.index_instance(instance)

        result = backend.search("global", "Alpha", filters={"status": "public"})
        assert result.total == 1
        hit = result.hits[0]
        assert hit.identification == {"id": 1}
        assert "secret" not in hit.data
