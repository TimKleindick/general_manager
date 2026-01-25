from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase

from general_manager.api.graphql import GraphQL
from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.permission.base_permission import BasePermission
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search import backend_registry
from general_manager.search.backend_registry import configure_search_backend
from general_manager.search.config import IndexConfig
from general_manager.search.indexer import SearchIndexer
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket


class ProjectInterface(BaseTestInterface):
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    data_store: ClassVar[dict[int, dict[str, str]]] = {
        1: {"name": "Alpha", "status": "public"},
        2: {"name": "Beta", "status": "private"},
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


class ProjectPermission(BasePermission):
    def check_permission(self, action, attribute):
        return True

    def get_permission_filter(self):
        return [{"filter": {"status": "public"}, "exclude": {}}]


class Project(GeneralManager):
    Interface = ProjectInterface
    Permission = ProjectPermission

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"], filters=["status"])
        ]


class GraphQLSearchTests(SimpleTestCase):
    def setUp(self) -> None:
        self._orig_gm_classes = GeneralManagerMeta.all_classes
        self._orig_backend = backend_registry._backend
        self._orig_query_fields = GraphQL._query_fields
        self._orig_type_registry = GraphQL.graphql_type_registry
        self._orig_manager_registry = GraphQL.manager_registry
        self._orig_search_union = GraphQL._search_union
        self._orig_search_result_type = GraphQL._search_result_type

        GeneralManagerMeta.all_classes = [Project]
        GeneralmanagerConfig.initialize_general_manager_classes([Project], [Project])
        GraphQL._query_fields = {}
        GraphQL.graphql_type_registry = {}
        GraphQL.manager_registry = {}
        GraphQL._search_union = None
        GraphQL._search_result_type = None

        GraphQL.create_graphql_interface(Project)
        GraphQL.register_search_query()

        backend = DevSearchBackend()
        configure_search_backend(backend)
        indexer = SearchIndexer(backend)
        indexer.index_instance(Project(id=1))
        indexer.index_instance(Project(id=2))

    def tearDown(self) -> None:
        configure_search_backend(self._orig_backend)
        GeneralManagerMeta.all_classes = self._orig_gm_classes
        safe_classes = [
            manager_class
            for manager_class in self._orig_gm_classes
            if hasattr(manager_class, "Interface")
        ]
        GeneralmanagerConfig.initialize_general_manager_classes([], safe_classes)
        GraphQL._query_fields = self._orig_query_fields
        GraphQL.graphql_type_registry = self._orig_type_registry
        GraphQL.manager_registry = self._orig_manager_registry
        GraphQL._search_union = self._orig_search_union
        GraphQL._search_result_type = self._orig_search_result_type
        super().tearDown()

    def test_graphql_search_filters_by_permission(self) -> None:
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        response = field.resolver(
            None,
            info,
            query="",
            index="global",
            types=None,
            filters=None,
            page=1,
            page_size=10,
        )

        assert response["total"] == 1
        assert len(response["results"]) == 1
        assert response["results"][0].identification == {"id": 1}

    def test_graphql_search_filters_list(self) -> None:
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        response = field.resolver(
            None,
            info,
            query="",
            index="global",
            types=None,
            filters=[
                {"field": "status", "value": "public"},
            ],
            page=1,
            page_size=10,
        )

        assert len(response["results"]) == 1

    def test_graphql_search_total_counts_authorized(self) -> None:
        ProjectInterface.data_store[3] = {"name": "Gamma", "status": "public"}
        ProjectInterface.data_store[4] = {"name": "Delta", "status": "public"}
        indexer = SearchIndexer(backend_registry.get_search_backend())
        indexer.index_instance(Project(id=3))
        indexer.index_instance(Project(id=4))

        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        response = field.resolver(
            None,
            info,
            query="",
            index="global",
            types=None,
            filters=None,
            page=1,
            page_size=1,
        )

        assert response["total"] == 3
        assert len(response["results"]) == 1
        ProjectInterface.data_store.pop(3, None)
        ProjectInterface.data_store.pop(4, None)

    def test_graphql_search_permission_filters_override_user_filters(self) -> None:
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        response = field.resolver(
            None,
            info,
            query="",
            index="global",
            types=None,
            filters={"status": "private"},
            page=1,
            page_size=10,
        )

        assert response["total"] == 1
        assert response["results"][0].identification == {"id": 1}

    def test_graphql_search_sorting_numeric_and_dates(self) -> None:
        class RankedInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
            data_store: ClassVar[dict[int, dict[str, object]]] = {
                1: {"name": "Alpha", "rank": 10, "start_date": "2024-01-02"},
                2: {"name": "Beta", "rank": 2, "start_date": "2023-12-31"},
                3: {"name": "Gamma", "rank": 3, "start_date": "2024-01-01"},
            }

            def get_data(self, search_date=None):
                return self.data_store[self.identification["id"]]

            @classmethod
            def get_attribute_types(cls):
                return {"name": {"type": str}, "rank": {"type": int}}

            @classmethod
            def get_attributes(cls):
                return {
                    "name": lambda interface: interface.get_data()["name"],
                    "rank": lambda interface: interface.get_data()["rank"],
                    "start_date": lambda interface: interface.get_data()["start_date"],
                }

            @classmethod
            def filter(cls, **kwargs):
                ids = kwargs.get("id__in") or list(cls.data_store.keys())
                return SimpleBucket(
                    cls._parent_class, [cls._parent_class(id=val) for val in ids]
                )

        class RankedPermission(BasePermission):
            def check_permission(self, action, attribute):
                return True

            def get_permission_filter(self):
                return [{"filter": {}, "exclude": {}}]

        class RankedProject(GeneralManager):
            Interface = RankedInterface
            Permission = RankedPermission

            class SearchConfig:
                indexes: ClassVar[list[IndexConfig]] = [
                    IndexConfig(name="global", fields=["name", "rank", "start_date"])
                ]

        GeneralManagerMeta.all_classes = [RankedProject]
        GeneralmanagerConfig.initialize_general_manager_classes(
            [RankedProject], [RankedProject]
        )
        GraphQL._query_fields = {}
        GraphQL.graphql_type_registry = {}
        GraphQL.manager_registry = {}
        GraphQL._search_union = None
        GraphQL._search_result_type = None
        GraphQL.create_graphql_interface(RankedProject)
        GraphQL.register_search_query()

        backend = DevSearchBackend()
        configure_search_backend(backend)
        indexer = SearchIndexer(backend)
        indexer.index_instance(RankedProject(id=1))
        indexer.index_instance(RankedProject(id=2))
        indexer.index_instance(RankedProject(id=3))

        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        response = field.resolver(
            None,
            info,
            query="",
            index="global",
            sort_by="rank",
            sort_desc=False,
            page=1,
            page_size=10,
        )
        names = [item.identification["id"] for item in response["results"]]
        assert names == [2, 3, 1]

        response = field.resolver(
            None,
            info,
            query="",
            index="global",
            sort_by="start_date",
            sort_desc=False,
            page=1,
            page_size=10,
        )
        names = [item.identification["id"] for item in response["results"]]
        assert names == [2, 3, 1]
