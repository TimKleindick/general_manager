from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, override_settings
from graphql import GraphQLError

from general_manager.api import graphql_search as graphql_search_module
from general_manager.api.graphql import GraphQL
from general_manager.apps import GeneralmanagerConfig
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.measurement.measurement import Measurement
from general_manager.permission.base_permission import BasePermission
from general_manager.search.backends.dev import DevSearchBackend
from general_manager.search.backend import SearchHit, SearchResult
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
        """
        Retrieve the data dictionary for the current interface id.

        Parameters:
            search_date (optional): Ignored; accepted for API compatibility.

        Returns:
            dict: The data dictionary from this interface's in-memory store for the current id.
        """
        return self.data_store[self.identification["id"]]

    @classmethod
    def get_attribute_types(cls):
        """
        Provide attribute metadata for the interface.

        Returns:
            dict: A mapping from attribute name to a metadata dictionary describing the attribute. For this class the mapping includes:
                - "name": {"type": str}
                - "status": {"type": str}
        """
        return {
            "name": {"type": str},
            "status": {"type": str},
        }

    @classmethod
    def get_attributes(cls):
        """
        Provide attribute accessors for the interface.

        Returns:
            dict: Mapping of attribute names to callables that take an interface instance and return the attribute's value. Includes keys "name" and "status".
        """
        return {
            "name": lambda interface: interface.get_data()["name"],
            "status": lambda interface: interface.get_data()["status"],
        }

    @classmethod
    def filter(cls, **kwargs):
        """
        Return a SimpleBucket of parent-class instances for the requested ids or for all stored ids when no id__in is provided.

        Parameters:
            id__in (iterable[int], optional): Iterable of ids to filter by. If omitted, all ids from the class data_store are used.

        Returns:
            SimpleBucket: A bucket containing instances of the parent class for each selected id.
        """
        ids = kwargs.get("id__in")
        if ids is None:
            ids = list(cls.data_store.keys())
        return SimpleBucket(
            cls._parent_class, [cls._parent_class(id=val) for val in ids]
        )


class ProjectPermission(BasePermission):
    def check_permission(self, action, attribute):
        """
        Unconditionally grant permission for any action and attribute.

        Parameters:
            action: Identifier or name of the attempted action (for example, 'read' or 'write').
            attribute: The attribute or field being accessed.

        Returns:
            True if the action is permitted (always True), False otherwise.
        """
        return True

    def check_operation_permission(self, action):
        return True

    def describe_operation_permissions(self, action):
        return ()

    def get_permission_filter(self):
        """
        Provide permission filters that restrict results to items with status "public".

        Returns:
            permission_filters (list[dict]): A list of permission filter objects. Each object contains a "filter" dict mapping field names to required values (here {"status": "public"}) and an "exclude" dict of fields to exclude.
        """
        return [{"filter": {"status": "public"}, "exclude": {}}]


class Project(GeneralManager):
    Interface = ProjectInterface
    Permission = ProjectPermission

    class SearchConfig:
        indexes: ClassVar[list[IndexConfig]] = [
            IndexConfig(name="global", fields=["name"], filters=["status"])
        ]


class CountingDevSearchBackend(DevSearchBackend):
    def __init__(self) -> None:
        super().__init__()
        self.search_calls: list[dict[str, Any]] = []

    def search(self, *args: Any, **kwargs: Any):
        self.search_calls.append({"args": args, "kwargs": dict(kwargs)})
        return super().search(*args, **kwargs)


class BestEffortTotalSearchBackend(CountingDevSearchBackend):
    def search(self, *args: Any, **kwargs: Any) -> SearchResult:
        result = super().search(*args, **kwargs)
        return SearchResult(
            hits=result.hits,
            total=len(result.hits),
            took_ms=result.took_ms,
            raw=result.raw,
        )


class GraphQLSearchTests(SimpleTestCase):
    def setUp(self) -> None:
        """
        Prepare test environment for GraphQL search tests.

        Sets GeneralManager to include only Project, initializes general manager configuration, clears and reinitializes GraphQL registries and search types, creates the GraphQL interface and search query for Project, configures a development search backend, and indexes two Project instances (ids 1 and 2) for use by the tests.
        """
        self._orig_gm_classes = GeneralManagerMeta.all_classes
        self._orig_backend = backend_registry._backend
        self._orig_query_fields = GraphQL._query_fields
        self._orig_type_registry = GraphQL.graphql_type_registry
        self._orig_manager_registry = GraphQL.manager_registry
        self._orig_search_union = GraphQL._search_union
        self._orig_search_result_type = GraphQL._search_result_type
        self._orig_project_data_store = ProjectInterface.data_store.copy()

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
        """
        Restore global search backend, general-manager registration, and GraphQL registries to their original state.

        This reverses mutations performed in setUp by restoring the previously saved search backend, GeneralManagerMeta class list and safe-class initialization, and GraphQL query/type/manager/search-related registries and caches, then delegates to the superclass tearDown.
        """
        configure_search_backend(self._orig_backend)
        ProjectInterface.data_store = self._orig_project_data_store.copy()
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

    def _configure_counting_backend_with_public_rows(
        self,
        public_count: int,
        backend: CountingDevSearchBackend | None = None,
    ) -> CountingDevSearchBackend:
        ProjectInterface.data_store = {
            row_id: {"name": f"Project {row_id}", "status": "public"}
            for row_id in range(1, public_count + 1)
        }
        backend = backend or CountingDevSearchBackend()
        configure_search_backend(backend)
        indexer = SearchIndexer(backend)
        for row_id in ProjectInterface.data_store:
            indexer.index_instance(Project(id=row_id))
        return backend

    def test_graphql_search_filters_by_permission(self) -> None:
        """
        Verify that the GraphQL search applies permission filters for an anonymous user.

        Executes the registered GraphQL search resolver against the "global" index as an anonymous user and asserts that only items allowed by the permission filter are counted and returned (expected single result with id 1).
        """
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
        """
        Verifies GraphQL search respects an explicit filter list and returns only matching items.

        Asserts that executing the registered 'search' query with a filter of status == "public" yields exactly one result.
        """
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

    def test_graphql_search_omitted_pagination_uses_first_page_default_size(
        self,
    ) -> None:
        added_ids = range(3, 14)
        for item_id in added_ids:
            self.addCleanup(ProjectInterface.data_store.pop, item_id, None)
            ProjectInterface.data_store[item_id] = {
                "name": f"Project {item_id}",
                "status": "public",
            }
        indexer = SearchIndexer(backend_registry.get_search_backend())
        for item_id in added_ids:
            indexer.index_instance(Project(id=item_id))

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
            page=None,
            page_size=None,
        )

        assert response["total"] == 12
        assert len(response["results"]) == 10

    def test_graphql_search_positive_pagination_returns_requested_page(self) -> None:
        added_rows = {
            3: {"name": "Gamma", "status": "public"},
            4: {"name": "Delta", "status": "public"},
        }
        for item_id, row in added_rows.items():
            self.addCleanup(ProjectInterface.data_store.pop, item_id, None)
            ProjectInterface.data_store[item_id] = row
        indexer = SearchIndexer(backend_registry.get_search_backend())
        for item_id in added_rows:
            indexer.index_instance(Project(id=item_id))

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
            sort_by="name",
            page=2,
            page_size=2,
        )

        assert response["total"] == 3
        assert [item.identification for item in response["results"]] == [{"id": 3}]

    def test_graphql_search_exact_total_scans_all_matches_by_default(self) -> None:
        backend = self._configure_counting_backend_with_public_rows(public_count=5)
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

        assert response["total"] == 5
        assert response["total_is_exact"] is True
        assert len(response["results"]) == 1
        assert len(backend.search_calls) > 1

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_SEARCH_TOTAL_MODE": "bounded",
            "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 1,
        }
    )
    def test_graphql_search_bounded_total_stops_after_scan_limit(self) -> None:
        backend = self._configure_counting_backend_with_public_rows(public_count=5)
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

        assert response["total"] == 1
        assert response["total_is_exact"] is False
        assert len(response["results"]) == 1
        assert len(backend.search_calls) == 1

    def test_graphql_search_exact_total_ignores_best_effort_backend_total(self) -> None:
        backend = self._configure_counting_backend_with_public_rows(
            public_count=5,
            backend=BestEffortTotalSearchBackend(),
        )
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

        assert response["total"] == 5
        assert response["total_is_exact"] is True
        assert len(response["results"]) == 1
        assert len(backend.search_calls) == 6

    def test_graphql_search_exact_total_trims_materialized_page_window(self) -> None:
        self._configure_counting_backend_with_public_rows(public_count=12)
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()
        trimmed_lengths: list[int] = []
        real_trim = graphql_search_module.trim_search_hit_entries_to_window

        def tracking_trim(*args: Any, **kwargs: Any) -> None:
            real_trim(*args, **kwargs)
            trimmed_lengths.append(len(args[0]))

        with patch.object(
            graphql_search_module,
            "trim_search_hit_entries_to_window",
            side_effect=tracking_trim,
        ):
            response = field.resolver(
                None,
                info,
                query="",
                index="global",
                types=None,
                filters=None,
                page=1,
                page_size=2,
            )

        assert response["total"] == 12
        assert len(response["results"]) == 2
        assert trimmed_lengths
        assert max(trimmed_lengths) == 2

    def test_search_hit_window_trims_sorted_candidates(self) -> None:
        entries = [
            (
                None,
                SearchHit(
                    id=str(row_id),
                    type="Project",
                    identification={"id": row_id},
                    data={"name": name},
                ),
                Project(id=row_id),
            )
            for row_id, name in enumerate(
                ["Zulu Project", "Alpha Project", "Beta Project"],
                start=1,
            )
        ]

        graphql_search_module.trim_search_hit_entries_to_window(
            entries,
            requested_count=2,
            sort_by="name",
            sort_desc=False,
        )

        assert [entry[1].data["name"] for entry in entries] == [
            "Alpha Project",
            "Beta Project",
        ]

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_SEARCH_TOTAL_MODE": "bounded",
            "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 5,
        }
    )
    def test_graphql_search_bounded_total_marks_best_effort_limit_inexact(
        self,
    ) -> None:
        backend = self._configure_counting_backend_with_public_rows(
            public_count=5,
            backend=BestEffortTotalSearchBackend(),
        )
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

        assert response["total"] == 5
        assert response["total_is_exact"] is False
        assert len(response["results"]) == 1
        assert len(backend.search_calls) == 5

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_SEARCH_TOTAL_MODE": "bounded",
            "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 5,
        }
    )
    def test_graphql_search_bounded_total_is_conservative_at_scan_boundary(
        self,
    ) -> None:
        backend = self._configure_counting_backend_with_public_rows(public_count=5)
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

        assert response["total"] == 5
        assert response["total_is_exact"] is False
        assert len(response["results"]) == 1
        assert len(backend.search_calls) == 5

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_SEARCH_TOTAL_MODE": "bounded",
            "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 1,
        }
    )
    def test_graphql_search_total_mode_exact_overrides_bounded_setting(self) -> None:
        backend = self._configure_counting_backend_with_public_rows(public_count=5)
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
            total_mode="exact",
            page=1,
            page_size=1,
        )

        assert response["total"] == 5
        assert response["total_is_exact"] is True
        assert len(response["results"]) == 1
        assert len(backend.search_calls) > 1

    def test_graphql_search_rejects_invalid_total_mode(self) -> None:
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        with self.assertRaisesRegex(
            GraphQLError,
            "totalMode must be one of",
        ) as ctx:
            field.resolver(
                None,
                info,
                query="",
                index="global",
                types=None,
                filters=None,
                total_mode="fast",
                page=1,
                page_size=1,
            )
        assert ctx.exception.extensions["code"] == "BAD_USER_INPUT"

    @override_settings(
        GENERAL_MANAGER={
            "GRAPHQL_SEARCH_TOTAL_MODE": "bounded",
            "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT": 0,
        }
    )
    def test_graphql_search_rejects_invalid_total_scan_limit(self) -> None:
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        with self.assertRaisesRegex(
            GraphQLError,
            "GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT must be a positive integer",
        ) as ctx:
            field.resolver(
                None,
                info,
                query="",
                index="global",
                types=None,
                filters=None,
                page=1,
                page_size=1,
            )
        assert ctx.exception.extensions["code"] == "BAD_USER_INPUT"

    def test_graphql_search_rejects_zero_page(self) -> None:
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        with self.assertRaisesRegex(
            GraphQLError,
            "page must be a positive integer",
        ) as ctx:
            field.resolver(
                None,
                info,
                query="",
                index="global",
                types=None,
                filters=None,
                page=0,
                page_size=10,
            )
        assert ctx.exception.extensions["code"] == "BAD_USER_INPUT"

    def test_graphql_search_rejects_zero_page_size(self) -> None:
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        with self.assertRaisesRegex(
            GraphQLError,
            "pageSize must be a positive integer",
        ) as ctx:
            field.resolver(
                None,
                info,
                query="",
                index="global",
                types=None,
                filters=None,
                page=1,
                page_size=0,
            )
        assert ctx.exception.extensions["code"] == "BAD_USER_INPUT"

    def test_graphql_search_rejects_negative_page(self) -> None:
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        with self.assertRaisesRegex(
            GraphQLError,
            "page must be a positive integer",
        ) as ctx:
            field.resolver(
                None,
                info,
                query="",
                index="global",
                types=None,
                filters=None,
                page=-1,
                page_size=10,
            )
        assert ctx.exception.extensions["code"] == "BAD_USER_INPUT"

    def test_graphql_search_rejects_negative_page_size(self) -> None:
        field = GraphQL._query_fields["search"]
        info = MagicMock()
        info.context.user = AnonymousUser()

        with self.assertRaisesRegex(
            GraphQLError,
            "pageSize must be a positive integer",
        ) as ctx:
            field.resolver(
                None,
                info,
                query="",
                index="global",
                types=None,
                filters=None,
                page=1,
                page_size=-1,
            )
        assert ctx.exception.extensions["code"] == "BAD_USER_INPUT"

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
                """
                Retrieve the stored data dictionary for this interface instance's id.

                Parameters:
                    search_date (optional): Ignored by this implementation; present for API compatibility.

                Returns:
                    dict: The data dictionary from the class-level `data_store` keyed by this instance's `id`.
                """
                return self.data_store[self.identification["id"]]

            @classmethod
            def get_attribute_types(cls):
                """
                Provide attribute type definitions for the interface.

                Returns:
                    dict: Mapping of attribute names to their type configuration. Contains:
                        - "name": {"type": str}
                        - "rank": {"type": int}
                """
                return {"name": {"type": str}, "rank": {"type": int}}

            @classmethod
            def get_attributes(cls):
                """
                Provide attribute accessors for interfaces.

                Returns:
                    dict: Mapping of attribute names ("name", "rank", "start_date") to callables that accept an interface instance and return that attribute's value.
                """
                return {
                    "name": lambda interface: interface.get_data()["name"],
                    "rank": lambda interface: interface.get_data()["rank"],
                    "start_date": lambda interface: interface.get_data()["start_date"],
                }

            @classmethod
            def filter(cls, **kwargs):
                """
                Return a SimpleBucket of parent-class instances corresponding to the selected ids.

                Parameters:
                    id__in (Optional[Iterable]): Optional iterable of ids to include; if omitted, all ids from the interface's data_store are used.

                Returns:
                    SimpleBucket: A SimpleBucket containing instances of the interface's parent class created with each selected id.
                """
                ids = kwargs.get("id__in") or list(cls.data_store.keys())
                return SimpleBucket(
                    cls._parent_class, [cls._parent_class(id=val) for val in ids]
                )

        class RankedPermission(BasePermission):
            def check_permission(self, action, attribute):
                """
                Allow any action on any attribute.

                Parameters:
                        action: Identifier or descriptor of the action being checked.
                        attribute: Name or descriptor of the attribute the action targets.

                Returns:
                        `True` if the action is permitted (this permission always grants access), `False` otherwise.
                """
                return True

            def check_operation_permission(self, action):
                return True

            def describe_operation_permissions(self, action):
                return ()

            def get_permission_filter(self):
                """
                Return the permission filter specifications applied to searches for this permission.

                By default returns a list containing a single specification with empty "filter" and "exclude" dictionaries.

                Returns:
                    list[dict]: A list of permission filter specs where each spec is a dict with keys "filter" (dict) and "exclude" (dict).
                """
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


class GraphQLSearchHelperCoverageTests(SimpleTestCase):
    def test_total_mode_and_sort_value_edge_cases(self) -> None:
        with self.assertRaises(GraphQLError):
            graphql_search_module.normalize_search_total_mode(object())  # type: ignore[arg-type]

        assert graphql_search_module.normalize_search_sort_value(None) is None
        assert graphql_search_module.normalize_search_sort_value(Decimal("1.5")) == 1.5

        naive = datetime(2024, 1, 2, 3, 4, 5)
        aware = graphql_search_module.normalize_search_sort_value(naive)
        assert isinstance(aware, datetime)
        assert aware.tzinfo is not None

        date_value = graphql_search_module.normalize_search_sort_value(date(2024, 1, 2))
        assert isinstance(date_value, datetime)
        assert date_value.tzinfo is not None
        assert graphql_search_module.normalize_search_sort_value(object()).startswith(
            "<object object at"
        )

    def test_parse_filters_accepts_json_and_skips_malformed_items(self) -> None:
        parsed = graphql_search_module.parse_search_filters(
            '[{"field": "status", "values": ["public"]}, "bad", {"op": "exact"}]'
        )

        assert parsed == {"status__in": ["public"]}
        assert graphql_search_module.parse_search_filters("{bad json") == {}

    def test_permission_and_match_helpers_cover_empty_and_denied_paths(self) -> None:
        assert graphql_search_module.merge_permission_filters(
            {"status": "public"}, []
        ) == {"status": "public"}

        instance = SimpleNamespace(status="private")
        assert not graphql_search_module.matches_filters(instance, {"status": "public"})

        info = MagicMock()
        project = Project(id=1)
        permission_plan = SimpleNamespace(filters=[], requires_instance_check=True)
        with patch.object(
            graphql_search_module,
            "get_read_permission_filter",
            return_value=permission_plan,
        ) as get_plan:
            with patch.object(
                graphql_search_module,
                "can_read_instance",
                return_value=False,
            ) as can_read:
                assert not graphql_search_module.passes_permission_filters(
                    project,
                    info,
                )

        get_plan.assert_called_once_with(Project, info)
        can_read.assert_called_once_with(project, info)

        denied_plan = SimpleNamespace(
            filters=[{"filter": {"status": "public"}, "exclude": {}}],
            requires_instance_check=False,
        )
        assert not graphql_search_module.passes_permission_filters(
            instance, info, permission_plan=denied_plan
        )

    def test_search_union_empty_and_non_manager_resolution(self) -> None:
        assert (
            graphql_search_module.create_search_union({"Project": Project}, {}) is None
        )

        union = graphql_search_module.create_search_union(
            {"Project": Project},
            {"Project": MagicMock()},
        )

        assert union is not None
        assert union.resolve_type(object(), MagicMock()) is None

    def test_filter_options_cover_relation_measurement_and_relation_guards(
        self,
    ) -> None:
        mapper = MagicMock(return_value=graphql_search_module.graphene.String())
        relation_options = list(
            graphql_search_module.get_filter_options(Project, "project", mapper)
        )
        assert relation_options == [("project", None)]

        measurement_options = list(
            graphql_search_module.get_filter_options(Measurement, "cost", mapper)
        )
        assert [name for name, _field in measurement_options] == [
            "cost",
            "cost__exact",
            "cost__gt",
            "cost__gte",
            "cost__lt",
            "cost__lte",
        ]

        registry: dict[str, type[graphql_search_module.graphene.InputObjectType]] = {}
        assert (
            graphql_search_module.get_relation_filter_option(
                str,
                "owner",
                {"relation_kind": "direct"},
                registry,
                mapper,
                remaining_depth=1,
            )
            is None
        )
        assert (
            graphql_search_module.get_relation_filter_option(
                Project,
                "owner",
                {"relation_kind": "unsupported"},
                registry,
                mapper,
                remaining_depth=1,
            )
            is None
        )
        with patch.object(
            graphql_search_module, "create_filter_options", return_value=None
        ):
            assert (
                graphql_search_module.get_relation_filter_option(
                    Project,
                    "owner",
                    {"relation_kind": "direct"},
                    registry,
                    mapper,
                    remaining_depth=1,
                )
                is None
            )

    def test_create_filter_options_skips_unfilterable_props_and_empty_types(
        self,
    ) -> None:
        class EmptyInterface(BaseTestInterface):
            @classmethod
            def get_attribute_types(cls):
                """Return no filterable attributes."""
                return {}

            @classmethod
            def get_graph_ql_properties(cls):
                """Return no filterable GraphQL properties."""
                return {}

        class EmptyManager(GeneralManager):
            Interface = EmptyInterface

        EmptyInterface._parent_class = EmptyManager
        mapper = MagicMock(return_value=graphql_search_module.graphene.String())
        assert (
            graphql_search_module.create_filter_options(EmptyManager, {}, mapper)
            is None
        )

        prop = SimpleNamespace(filterable=False, graphql_type_hint=str)
        with patch.object(
            Project.Interface, "get_graph_ql_properties", return_value={"x": prop}
        ):
            filter_type = graphql_search_module.create_filter_options(
                Project, {}, mapper
            )

        assert filter_type is not None
        assert "x" not in filter_type._meta.fields

    def test_normalize_id_and_relation_filter_edges(self) -> None:
        assert (
            graphql_search_module.normalize_id_filter_value(Project, "name", "A") == "A"
        )

        class NoIdInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, Input]] = {}

        class NoIdManager(GeneralManager):
            Interface = NoIdInterface

        NoIdInterface._parent_class = NoIdManager
        assert (
            graphql_search_module.normalize_id_filter_value(NoIdManager, "id", "1")
            == "1"
        )
        assert (
            graphql_search_module.normalize_id_filter_value(Project, "id__in", "1")
            == "1"
        )

        class NoAttributeInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

        class NoAttributeManager(GeneralManager):
            Interface = NoAttributeInterface

        NoAttributeInterface._parent_class = NoAttributeManager
        assert graphql_search_module.normalize_filter_input(
            NoAttributeManager,
            {"status": "public"},
        ) == {"filter": {"status": "public"}, "exclude": {}}

        class LeafInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

            @classmethod
            def get_attribute_types(cls):
                """Return simple leaf attributes for nested relation filters."""
                return {"id": {"type": int}, "status": {"type": str}}

        class LeafManager(GeneralManager):
            Interface = LeafInterface

        LeafInterface._parent_class = LeafManager

        class RelationInterface(BaseTestInterface):
            input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}

            @classmethod
            def get_attribute_types(cls):
                """Return direct, collection, and unsupported relation metadata."""
                return {
                    "owner": {
                        "type": LeafManager,
                        "relation_kind": "direct",
                        "filter_lookup": "owner",
                    },
                    "members": {
                        "type": LeafManager,
                        "relation_kind": "collection",
                        "filter_lookup": "members",
                    },
                    "legacy": {
                        "type": LeafManager,
                        "relation_kind": "legacy",
                    },
                    "plain_relation": {"type": LeafManager},
                }

        class RelationManager(GeneralManager):
            Interface = RelationInterface

        RelationInterface._parent_class = RelationManager

        normalized = graphql_search_module.normalize_filter_input(
            RelationManager,
            {
                "owner": {"status": "public", "id": "2"},
                "members": {
                    "any": {"status": "active"},
                    "none": {"status": "inactive"},
                },
                "legacy": {"status": "ignored"},
                "plain_relation": {"status": "plain"},
            },
        )

        assert normalized == {
            "filter": {
                "owner__status": "public",
                "owner__id": 2,
                "members__status": "active",
                "legacy": {"status": "ignored"},
                "plain_relation": {"status": "plain"},
            },
            "exclude": {"members__status": "inactive"},
        }

    def test_register_search_query_guard_paths_and_resolver_errors(self) -> None:
        existing_union = MagicMock()
        existing_result = MagicMock()
        query_fields = {"search": object()}

        assert graphql_search_module.register_search_query(
            query_fields,
            {},
            {},
            existing_union,
            existing_result,
        ) == (existing_union, existing_result)

        with patch.object(
            graphql_search_module, "create_search_union", return_value=None
        ):
            assert graphql_search_module.register_search_query(
                {},
                {"Project": Project},
                {"Project": MagicMock()},
                None,
                existing_result,
            ) == (None, existing_result)

        with patch.object(
            graphql_search_module,
            "validate_filter_keys",
            side_effect=ValueError("bad filter"),
        ):
            query_fields = {}
            graphql_search_module.register_search_query(
                query_fields,
                {"Project": Project},
                {"Project": MagicMock()},
                None,
                None,
            )
            with self.assertRaises(GraphQLError):
                query_fields["search"].resolver(
                    None,
                    MagicMock(),
                    query="",
                    filters={"status": "public"},
                )

    def test_resolver_logs_instantiation_failures(self) -> None:
        query_fields = {}
        graphql_search_module.register_search_query(
            query_fields,
            {"Project": Project},
            {"Project": MagicMock()},
            None,
            None,
        )
        backend = MagicMock()
        backend.search.side_effect = [
            SearchResult(
                hits=[
                    SearchHit(
                        id="bad",
                        type="Project",
                        identification={},
                        score=1.0,
                        data={"name": "Bad"},
                    )
                ],
                total=1,
                took_ms=1,
                raw={},
            ),
            SearchResult(hits=[], total=0, took_ms=1, raw={}),
        ]
        info = MagicMock()
        with patch.object(
            graphql_search_module, "get_search_backend", return_value=backend
        ):
            with patch.object(graphql_search_module.logger, "debug") as debug:
                response = query_fields["search"].resolver(None, info, query="")

        assert response["results"] == []
        debug.assert_called_once()
