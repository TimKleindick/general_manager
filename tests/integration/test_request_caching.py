from __future__ import annotations

from typing import Any, ClassVar

from general_manager.cache.cache_decorator import cached
from general_manager.cache.dependency_index import (
    get_full_index,
    parse_dependency_identifier,
    generic_cache_invalidation,
)
from general_manager.interface import RequestInterface
from general_manager.interface.requests import (
    RequestField,
    RequestFilter,
    RequestQueryOperation,
    RequestTransportConfig,
    RequestTransportRequest,
    RequestTransportResponse,
    SharedRequestTransport,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class FakeCachingTransport(SharedRequestTransport):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.projects: list[dict[str, Any]] = []

    def send(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: RequestQueryOperation,
        plan: Any,
        identification: dict[str, Any] | None,
    ) -> RequestTransportResponse:
        self.calls.append({"operation": operation.name, "request": request})
        projects = list(self.projects)

        if operation.name == "detail":
            project_id = identification["id"] if identification is not None else None
            for item in projects:
                if item["id"] == project_id:
                    return RequestTransportResponse(payload=item, status_code=200)
            return RequestTransportResponse(payload={}, status_code=200)

        if operation.name == "search":
            query = str((request.body or {}).get("query", "")).lower()
            items = [item for item in projects if query in str(item["name"]).lower()]
            return RequestTransportResponse(payload=items, status_code=200)

        status = request.query_params.get("state")
        items = projects
        if status is not None:
            items = [item for item in items if item["status"] == status]
        search = request.query_params.get("search")
        if search is not None:
            needle = str(search).lower()
            items = [item for item in items if needle in str(item["name"]).lower()]
        return RequestTransportResponse(payload=items, status_code=200)


class RequestCachingIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class RemoteProject(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)

                name = RequestField(str)
                status = RequestField(str)

                class Meta:
                    filters: ClassVar[dict[str, RequestFilter]] = {
                        "status": RequestFilter(remote_name="state", value_type=str),
                        "name__icontains": RequestFilter(
                            remote_name="search",
                            value_type=str,
                        ),
                    }
                    query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                        "detail": RequestQueryOperation(
                            name="detail",
                            method="GET",
                            path="/projects/{id}",
                        ),
                        "list": RequestQueryOperation(
                            name="list",
                            method="GET",
                            path="/projects",
                        ),
                        "search": RequestQueryOperation(
                            name="search",
                            method="POST",
                            path="/projects/search",
                            filters={
                                "query": RequestFilter(
                                    remote_name="query",
                                    location="body",
                                    value_type=str,
                                )
                            },
                        ),
                    }
                    transport = FakeCachingTransport()
                    transport_config = RequestTransportConfig(
                        base_url="https://cache.example.test",
                        timeout=5,
                    )

        cls.RemoteProject = RemoteProject
        cls.general_manager_classes = [RemoteProject]
        cls.RemoteProject._attributes = cls.RemoteProject.Interface.get_attributes()
        GeneralManagerMeta.all_classes = cls.general_manager_classes
        super().setUpClass()
        GeneralManagerMeta.create_at_properties_for_attributes(
            cls.RemoteProject._attributes.keys(),
            cls.RemoteProject,
        )

    def setUp(self) -> None:
        super().setUp()
        self.RemoteProject.Interface.transport.calls.clear()
        self.RemoteProject.Interface.transport.projects = [
            {"id": 1, "name": "Alpha", "status": "active"},
            {"id": 2, "name": "Beta", "status": "inactive"},
        ]

    def test_request_query_dependencies_are_recorded_per_operation(self) -> None:
        @cached()
        def active_count() -> int:
            return self.RemoteProject.filter(status="active").count()

        @cached()
        def search_count() -> int:
            return self.RemoteProject.Interface.query_operation(
                "search",
                query="alpha",
            ).count()

        self.assertEqual(active_count(), 1)
        self.assert_cache_miss()
        self.assertEqual(search_count(), 1)
        self.assert_cache_miss()

        idx = get_full_index()
        request_section = idx["request_query"][self.RemoteProject.__name__]
        parsed_identifiers = [
            parse_dependency_identifier(identifier)
            for identifier in request_section.keys()
        ]
        operations = set()
        for identifier, entry in zip(
            request_section.keys(),
            parsed_identifiers,
            strict=False,
        ):
            self.assertIsNotNone(
                entry,
                msg=f"Expected dependency identifier to parse as JSON: {identifier!r}",
            )
            assert entry is not None
            operations.add(entry["operation"])
        self.assertEqual(operations, {"list", "search"})

    def test_request_query_invalidation_is_safe_and_does_not_trigger_detail_reads(
        self,
    ) -> None:
        @cached()
        def active_count() -> int:
            return self.RemoteProject.filter(status="active").count()

        self.assertEqual(active_count(), 1)
        self.assert_cache_miss()
        self.assertEqual(active_count(), 1)
        self.assert_cache_hit()

        self.RemoteProject.Interface.transport.calls.clear()
        self.RemoteProject.Interface.transport.projects.append(
            {"id": 3, "name": "Gamma", "status": "active"}
        )

        generic_cache_invalidation(
            sender=self.RemoteProject,
            instance=self.RemoteProject(id=1),
            old_relevant_values={},
        )

        self.assertEqual(active_count(), 2)
        self.assert_cache_miss()
        self.assertNotIn(
            "detail",
            [
                call["operation"]
                for call in self.RemoteProject.Interface.transport.calls
            ],
        )
