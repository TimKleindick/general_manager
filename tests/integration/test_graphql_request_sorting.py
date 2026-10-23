from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar
from types import SimpleNamespace
from unittest.mock import patch

from graphql import GraphQLError

from general_manager.api.graphql import GraphQL

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.utils.crypto import get_random_string

from general_manager.interface import (
    DatabaseInterface,
    RemoteManagerInterface,
    RequestInterface,
)
from general_manager.interface.requests import (
    RequestField,
    RequestFilter,
    RequestPayload,
    RequestPlan,
    RequestQueryOperation,
    RequestQueryResult,
    RequestTransportConfig,
    RequestTransportRequest,
    RequestTransportResponse,
    SharedRequestTransport,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.measurement import Measurement
from general_manager.permission.base_permission import ReadPermissionPlan
from general_manager.permission.manager_based_permission import (
    AdditiveManagerPermission,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class FakeRequestRelationSortTransport(SharedRequestTransport):
    def __init__(self) -> None:
        self.requests: list[RequestTransportRequest] = []
        self.payload: list[dict[str, object]] = []
        self.execution_count = 0
        self.result_page: int | None = None
        self.result_page_size: int | None = None
        self.result_total_count: int | None = None

    def execute(
        self,
        *,
        interface_cls: type[Any],
        operation: RequestQueryOperation,
        plan: RequestPlan,
        identification: dict[str, Any] | None = None,
    ) -> RequestQueryResult:
        self.execution_count += 1
        if self.result_page is not None:
            del interface_cls, operation, plan, identification
            return RequestQueryResult(
                items=tuple(self.payload),
                total_count=self.result_total_count,
                page=self.result_page,
                page_size=self.result_page_size,
            )
        result = super().execute(
            interface_cls=interface_cls,
            operation=operation,
            plan=plan,
            identification=identification,
        )
        return RequestQueryResult(items=result.items, total_count=len(result.items))

    def send(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: RequestQueryOperation,
        plan: RequestPlan,
        identification: dict[str, Any] | None,
    ) -> RequestTransportResponse:
        del interface_cls, operation, plan, identification
        self.requests.append(request)
        payload = list(self.payload)
        if request.query_params and all(
            key in payload[0] for key in request.query_params
        ):
            payload = [
                item
                for item in payload
                if all(
                    item.get(key) == value
                    for key, value in request.query_params.items()
                )
            ]
        return RequestTransportResponse(payload=payload, status_code=200)


class FakeRemoteRequestSortTransport(SharedRequestTransport):
    def __init__(self) -> None:
        self.requests: list[RequestTransportRequest] = []
        self.payload: list[dict[str, object]] = []

    def send(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: RequestQueryOperation,
        plan: RequestPlan,
        identification: dict[str, Any] | None,
    ) -> RequestTransportResponse:
        del interface_cls, operation, plan, identification
        self.requests.append(request)
        body = request.body or {}
        filters = body.get("filters")
        ordering = body.get("ordering")
        page = body.get("page")
        page_size = body.get("page_size")
        items = list(self.payload)
        if isinstance(filters, dict) and filters.get("root_key") is not None:
            items = [item for item in items if item["root_key"] == filters["root_key"]]
        total_count = len(items)
        if ordering == ["-root_key"]:
            items.sort(key=lambda item: str(item["root_key"]), reverse=True)
        if isinstance(page, int) and isinstance(page_size, int):
            start = (page - 1) * page_size
            items = items[start : start + page_size]
        return RequestTransportResponse(
            payload={
                "items": items,
                "total_count": total_count,
                "metadata": {"page": page, "page_size": page_size},
            },
            status_code=200,
        )


class TestGraphQLRequestRelationSorting(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        transport = FakeRequestRelationSortTransport()
        remote_transport = FakeRemoteRequestSortTransport()

        class RequestSortProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        def normalize_request_sort_item(payload: RequestPayload) -> dict[str, object]:
            return {
                "id": payload["identifier"],
                "root_key": payload["rootKey"],
                "project": RequestSortProject(id=payload["projectId"]),
                "optional_amount": payload.get("optionalAmount"),
                "decimal_amount": (
                    Decimal(str(payload["decimalAmount"]))
                    if payload.get("decimalAmount") is not None
                    else None
                ),
                "distance": (
                    Measurement(Decimal(str(payload["distance"])), "meter")
                    if payload.get("distance") is not None
                    else None
                ),
            }

        class RequestSortItem(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)
                root_key = RequestField(str)
                project = RequestField(RequestSortProject)
                optional_amount = RequestField(int | None)
                decimal_amount = RequestField(Decimal | None)
                distance = RequestField(Measurement | None)

                class Meta:
                    filters: ClassVar[dict[str, RequestFilter]] = {
                        "root_key": RequestFilter(
                            remote_name="rootKey",
                            value_type=str,
                        )
                    }
                    query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                        "detail": RequestQueryOperation(
                            name="detail",
                            method="GET",
                            path="/request-sort-items/{id}",
                        ),
                        "list": RequestQueryOperation(
                            name="list",
                            method="GET",
                            path="/request-sort-items",
                        ),
                    }
                    transport_config = RequestTransportConfig(
                        base_url="https://sorting.example.test",
                        timeout=1,
                    )
                    response_serializer = normalize_request_sort_item

            class Permission(AdditiveManagerPermission):
                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    if self.request_user.is_superuser:
                        return ReadPermissionPlan(
                            filters=[],
                            requires_instance_check=False,
                            decision="allow_all",
                        )
                    return ReadPermissionPlan(
                        filters=[],
                        requires_instance_check=True,
                    )

                def can_read_instance(self) -> bool:
                    return self.request_user.is_superuser or self.instance.id != 4

        class RemoteRequestSortItem(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                root_key = RequestField(str)

                class Meta:
                    base_url = "https://sorting.example.test"
                    remote_manager = "remote-request-sort-items"
                    protocol_version = "v1"

        class FilteredRemoteRequestSortItem(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                root_key = RequestField(str)

                class Meta:
                    base_url = "https://sorting.example.test"
                    remote_manager = "remote-request-sort-items"
                    protocol_version = "v1"

            class Permission(AdditiveManagerPermission):
                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    return ReadPermissionPlan(
                        filters=[{"filter": {"root_key": "Beta"}}],
                        requires_instance_check=False,
                    )

        class DisjunctiveRemoteRequestSortItem(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                root_key = RequestField(str)

                class Meta:
                    base_url = "https://sorting.example.test"
                    remote_manager = "remote-request-sort-items"
                    protocol_version = "v1"

            class Permission(AdditiveManagerPermission):
                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    return ReadPermissionPlan(
                        filters=[
                            {"filter": {"root_key": "Alpha"}},
                            {"filter": {"root_key": "Beta"}},
                        ],
                        requires_instance_check=False,
                    )

        RequestSortItem.Interface.transport = transport
        RemoteRequestSortItem.Interface.transport = remote_transport
        FilteredRemoteRequestSortItem.Interface.transport = remote_transport
        DisjunctiveRemoteRequestSortItem.Interface.transport = remote_transport
        cls.general_manager_classes = [
            RequestSortProject,
            RequestSortItem,
            RemoteRequestSortItem,
            FilteredRemoteRequestSortItem,
            DisjunctiveRemoteRequestSortItem,
        ]
        cls.request_sort_project = RequestSortProject
        cls.request_sort_item = RequestSortItem
        cls.transport = transport
        cls.remote_transport = remote_transport

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="request-sort-user",
            password=password,
        )
        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])
        self.client.login(username="request-sort-user", password=password)

        alpha = self.request_sort_project.create(
            creator_id=self.user.id,
            name="Alpha",
        )
        zulu = self.request_sort_project.create(
            creator_id=self.user.id,
            name="Zulu",
        )
        self.transport.requests.clear()
        self.transport.execution_count = 0
        self.transport.result_page = None
        self.transport.result_page_size = None
        self.transport.result_total_count = None
        self.transport.payload = [
            {"identifier": 3, "rootKey": "Able", "projectId": alpha.id},
            {"identifier": 1, "rootKey": "Beta", "projectId": zulu.id},
            {"identifier": 2, "rootKey": "Zed", "projectId": alpha.id},
        ]
        self.remote_transport.requests.clear()
        self.remote_transport.payload = [
            {"id": 1, "root_key": "Alpha"},
            {"id": 2, "root_key": "Beta"},
            {"id": 3, "root_key": "Zed"},
        ]

    def test_chat_schema_details_and_queries_share_request_interface_contract(
        self,
    ) -> None:
        from general_manager.api.graphql import GraphQL
        from general_manager.chat.graphql_contract import clear_contract_cache
        from general_manager.chat.tools import (
            ScopeChatContext,
            execute_chat_tool,
            get_manager_schema,
        )

        context = ScopeChatContext.from_scope({"user": self.user})
        managers = [
            self.request_sort_item,
            GraphQL.manager_registry["RemoteRequestSortItem"],
        ]
        for manager in managers:
            old = getattr(manager, "chat_exposed", False)
            manager.chat_exposed = True
            clear_contract_cache()
            try:
                overview = execute_chat_tool(
                    "get_manager_schema", {"manager": manager.__name__}, context
                )
                full = get_manager_schema(manager.__name__)
                names = [
                    name
                    for name, info in overview["type_manifest"].items()
                    if info["kind"] in {"input", "enum"}
                ]
                detail = execute_chat_tool(
                    "get_manager_schema",
                    {
                        "manager": manager.__name__,
                        "view": "detail",
                        "types": names,
                        "snapshot": overview["snapshot"],
                    },
                    context,
                )
                self.assertEqual(
                    detail["types"],
                    {name: full["types"][name] for name in sorted(names)},
                )
                self.assertIn("rootKey", overview["output_fields"])
                result = execute_chat_tool(
                    "query",
                    {
                        "manager": manager.__name__,
                        "root": overview["roots"][0],
                        "fields": ["rootKey"],
                    },
                    context,
                )
                self.assertNotEqual(result.get("status"), "error", result)
                self.assertTrue(result["data"])
            finally:
                manager.chat_exposed = old
                clear_contract_cache()

    def test_generated_request_list_hydrates_relation_and_sorts_compound_keys(
        self,
    ) -> None:
        query = """
        query {
          requestSortItemList(
            orderBy: [
              {field: project__name, direction: DESC}
              {field: rootKey, direction: DESC}
            ]
          ) {
            items {
              id
              rootKey
              project { name }
            }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["requestSortItemList"]["items"]
        self.assertEqual([int(item["id"]) for item in items], [1, 2, 3])
        self.assertEqual(
            [item["rootKey"] for item in items],
            ["Beta", "Zed", "Able"],
        )
        self.assertEqual(
            [item["project"]["name"] for item in items],
            ["Zulu", "Alpha", "Alpha"],
        )
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(self.transport.requests[0].operation_name, "list")

    def test_request_page_matching_upstream_coordinates_is_not_sliced_twice(
        self,
    ) -> None:
        project_id = self.transport.payload[0]["projectId"]
        self.transport.payload = [
            {"identifier": 3, "rootKey": "Third", "projectId": project_id},
            {"identifier": 4, "rootKey": "Fourth", "projectId": project_id},
        ]
        self.transport.result_page = 2
        self.transport.result_page_size = 2
        self.transport.result_total_count = 4

        response = self.query(
            """
            query {
              requestSortItemList(page: 2, pageSize: 2) {
                items { id rootKey }
                pageInfo { totalCount pageSize currentPage totalPages }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["requestSortItemList"]
        self.assertEqual(
            [item["rootKey"] for item in payload["items"]], ["Third", "Fourth"]
        )
        self.assertEqual(
            payload["pageInfo"],
            {"totalCount": 4, "pageSize": 2, "currentPage": 2, "totalPages": 2},
        )
        self.assertEqual(self.transport.execution_count, 1)

    def test_partial_request_page_with_denied_member_keeps_unknown_total(self) -> None:
        project_id = self.transport.payload[0]["projectId"]
        self.transport.payload = [
            {"identifier": 3, "rootKey": "Allowed", "projectId": project_id},
            {"identifier": 4, "rootKey": "Denied", "projectId": project_id},
        ]
        self.transport.result_page = 2
        self.transport.result_page_size = 2
        self.transport.result_total_count = 4
        self.user.is_superuser = False
        self.user.save(update_fields=["is_superuser"])

        response = self.query(
            """
            query {
              requestSortItemList(page: 2, pageSize: 2) {
                items { id rootKey }
                pageInfo { totalCount pageSize currentPage totalPages }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["requestSortItemList"]
        self.assertEqual([item["rootKey"] for item in payload["items"]], ["Allowed"])
        self.assertEqual(
            payload["pageInfo"],
            {"totalCount": None, "pageSize": 2, "currentPage": 2, "totalPages": None},
        )
        self.assertEqual(self.transport.execution_count, 1)

    def test_remote_group_order_and_pagination_stay_local(self) -> None:
        """Group controls must not page or order the upstream source records."""
        response = self.query(
            """
            query {
              paged: remoteRequestSortItemGroups(
                groupBy: ["rootKey"]
                orderBy: [{field: rootKey, direction: DESC}]
                page: 2
                pageSize: 1
              ) {
                items { rootKey }
                pageInfo { totalCount currentPage totalPages pageSize }
              }
              ordered: remoteRequestSortItemGroups(
                groupBy: ["id"]
                orderBy: [{field: rootKey, direction: DESC}]
              ) { items { rootKey } }
            }
            """
        )

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]
        self.assertEqual(
            payload["paged"],
            {
                "items": [{"rootKey": "Beta"}],
                "pageInfo": {
                    "totalCount": 3,
                    "currentPage": 2,
                    "totalPages": 3,
                    "pageSize": 1,
                },
            },
        )
        self.assertEqual(
            payload["ordered"]["items"],
            [{"rootKey": "Zed"}, {"rootKey": "Beta"}, {"rootKey": "Alpha"}],
        )
        self.assertEqual(len(self.remote_transport.requests), 2)
        for request in self.remote_transport.requests:
            body = request.body or {}
            self.assertIsNone(body.get("page"))
            self.assertIsNone(body.get("page_size"))
            self.assertFalse(body.get("ordering"))

    def test_request_group_key_needs_no_upstream_filter_declaration(self) -> None:
        """Internal group membership uses local values, not remote filter support."""
        for record, amount in zip(self.transport.payload, (2, 2, 3), strict=True):
            record["optionalAmount"] = amount
        response = self.query(
            """
            query {
              requestSortItemGroups(
                groupBy: ["optionalAmount"]
                orderBy: [{field: optionalAmount}]
              ) {
                items { optionalAmount rootKey }
                pageInfo { totalCount }
              }
            }
            """
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["requestSortItemGroups"],
            {
                "items": [
                    {"optionalAmount": "2", "rootKey": "Able, Beta"},
                    {"optionalAmount": "3", "rootKey": "Zed"},
                ],
                "pageInfo": {"totalCount": 2},
            },
        )
        self.assertEqual(self.transport.execution_count, 1)

    def test_partial_request_list_rejects_global_grouping(self) -> None:
        """Grouped endpoints cannot aggregate only an upstream page."""
        project_id = self.transport.payload[0]["projectId"]
        self.transport.payload = [
            {"identifier": 3, "rootKey": "Third", "projectId": project_id},
            {"identifier": 4, "rootKey": "Fourth", "projectId": project_id},
        ]
        self.transport.result_page = 2
        self.transport.result_page_size = 2
        self.transport.result_total_count = 4

        response = self.query(
            """
            query {
              requestSortItemGroups(groupBy: ["rootKey"]) {
                items { rootKey }
                pageInfo { totalCount }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn("global grouping", response.json()["errors"][0]["message"])

    def test_grouped_relation_rejects_partial_request_before_union(self) -> None:
        """Materializing related records must not discard incomplete provenance."""
        self.transport.result_page = 2
        self.transport.result_page_size = 2
        self.transport.result_total_count = 4
        source = self.request_sort_item.all()
        group = SimpleNamespace(members=[SimpleNamespace(remote_list=source)])

        with self.assertRaisesMessage(GraphQLError, "incomplete response"):
            GraphQL._grouped_relation_bucket(
                group, "remote_list", self.request_sort_item
            )

        self.assertEqual(self.transport.execution_count, 1)

    def test_request_list_rejects_unknown_group_key_before_transport(self) -> None:
        """Group-key validation precedes fetching the source."""
        self.transport.payload = []
        response = self.query(
            """
            query {
              requestSortItemGroups(groupBy: ["unknownKey"]) {
                items { rootKey }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "not an eligible grouping key", response.json()["errors"][0]["message"]
        )
        self.assertEqual(self.transport.execution_count, 0)

    def test_empty_request_list_rejects_unknown_group_key_before_transport(
        self,
    ) -> None:
        """Invalid group keys cannot trigger a request fetch on an empty source."""
        self.transport.payload = []

        response = self.query(
            """
            query {
              requestSortItemGroups(groupBy: ["unknownKey"]) {
                items { rootKey }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "not an eligible grouping key",
            response.json()["errors"][0]["message"],
        )
        self.assertEqual(self.transport.execution_count, 0)

    def test_remote_request_forwards_finite_default_page(self) -> None:
        response = self.query(
            "{ remoteRequestSortItemList { items { id } pageInfo { pageSize currentPage } } }"
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["remoteRequestSortItemList"]
        self.assertEqual(payload["pageInfo"], {"pageSize": 10, "currentPage": 1})
        self.assertEqual(self.remote_transport.requests[0].body["page"], 1)
        self.assertEqual(self.remote_transport.requests[0].body["page_size"], 10)

    def test_upstream_page_metadata_cannot_bypass_output_page_size(self) -> None:
        self.transport.result_page = 1
        self.transport.result_page_size = 2
        self.transport.result_total_count = 10
        # The fake provider deliberately overdelivers three rows for a two-row page.
        response = self.query(
            "{ requestSortItemList(pageSize: 2) { items { id } pageInfo { totalCount pageSize currentPage totalPages } } }"
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["requestSortItemList"]
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(
            payload["pageInfo"],
            {"totalCount": 10, "pageSize": 2, "currentPage": 1, "totalPages": 5},
        )

    def test_incomplete_upstream_page_must_match_default_coordinates(self) -> None:
        self.transport.result_page = 2
        self.transport.result_page_size = 3
        self.transport.result_total_count = 10
        response = self.query("{ requestSortItemList { items { id } } }")
        self.assertResponseHasErrors(response)
        self.assertIn("global pagination", response.json()["errors"][0]["message"])

    def test_denied_request_operands_never_fetch_upstream(self) -> None:
        for total_count in [2, 10]:
            with self.subTest(total_count=total_count):
                self.transport.result_page = 1
                self.transport.result_page_size = 2
                self.transport.result_total_count = total_count
                with patch.object(
                    self.request_sort_item.Permission,
                    "get_read_permission_plan",
                    return_value=ReadPermissionPlan(
                        filters=[], requires_instance_check=False, decision="deny_all"
                    ),
                ):
                    response = self.query(
                        '{ requestSortItemList(filter: {rootKey: "Alpha"}) '
                        "{ items { id } pageInfo { totalCount } } }"
                    )
                self.assertEqual(self.transport.execution_count, 0)
                self.assertResponseNoErrors(response)
                self.assertEqual(
                    response.json()["data"]["requestSortItemList"],
                    {"items": [], "pageInfo": {"totalCount": 0}},
                )

    def test_operand_provenance_fetch_keeps_permission_filter(self) -> None:
        response = self.query(
            '{ filteredRemoteRequestSortItemList(filter: {rootKey: "Beta"}) '
            "{ items { rootKey } pageInfo { totalCount } } }"
        )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["filteredRemoteRequestSortItemList"],
            {"items": [{"rootKey": "Beta"}], "pageInfo": {"totalCount": 1}},
        )
        self.assertEqual(len(self.remote_transport.requests), 2)
        for request in self.remote_transport.requests:
            self.assertEqual(request.body["filters"], {"root_key": "Beta"})

    def test_remote_request_authorizes_full_scope_before_forwarding_order_and_page(
        self,
    ) -> None:
        response = self.query(
            """
            query {
              remoteRequestSortItemList(
                orderBy: [{field: rootKey, direction: DESC}]
                page: 2
                pageSize: 1
              ) {
                items { id rootKey }
                pageInfo { totalCount pageSize currentPage totalPages }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["remoteRequestSortItemList"]
        self.assertEqual(payload["items"], [{"id": 2, "rootKey": "Beta"}])
        self.assertEqual(
            payload["pageInfo"],
            {"totalCount": 3, "pageSize": 1, "currentPage": 2, "totalPages": 3},
        )
        self.assertEqual(len(self.remote_transport.requests), 2)
        self.assertFalse(self.remote_transport.requests[0].body.get("ordering"))
        self.assertNotIn("page", self.remote_transport.requests[0].body)
        self.assertNotIn("page_size", self.remote_transport.requests[0].body)
        body = self.remote_transport.requests[1].body
        self.assertEqual(body["page"], 2)
        self.assertEqual(body["page_size"], 1)
        self.assertEqual(body["ordering"], ["-root_key"])

    def test_remote_request_pushes_permission_filter_before_provenance_fetch(
        self,
    ) -> None:
        response = self.query(
            """
            query {
              filteredRemoteRequestSortItemList(page: 1, pageSize: 1) {
                items { id rootKey }
                pageInfo { totalCount pageSize currentPage totalPages }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["filteredRemoteRequestSortItemList"]
        self.assertEqual(payload["items"], [{"id": 2, "rootKey": "Beta"}])
        self.assertEqual(
            payload["pageInfo"],
            {"totalCount": 1, "pageSize": 1, "currentPage": 1, "totalPages": 1},
        )
        self.assertEqual(len(self.remote_transport.requests), 1)
        body = self.remote_transport.requests[0].body
        self.assertEqual(body["filters"], {"root_key": "Beta"})
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["page_size"], 1)

    def test_complete_disjunctive_permission_request_allows_local_pagination(
        self,
    ) -> None:
        response = self.query(
            """
            query {
              disjunctiveRemoteRequestSortItemList(page: 1, pageSize: 1) {
                items { id }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        self.assertEqual(
            len(
                response.json()["data"]["disjunctiveRemoteRequestSortItemList"]["items"]
            ),
            1,
        )
        self.assertEqual(len(self.remote_transport.requests), 2)

    def test_generated_request_list_applies_independent_order_directions(
        self,
    ) -> None:
        query = """
        query {
          requestSortItemList(
            orderBy: [
              {field: project__name, direction: ASC}
              {field: rootKey, direction: DESC}
            ]
          ) {
            items { id rootKey project { name } }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["requestSortItemList"]["items"]
        self.assertEqual(
            [(item["project"]["name"], item["rootKey"]) for item in items],
            [("Alpha", "Zed"), ("Alpha", "Able"), ("Zulu", "Beta")],
        )

    def test_generated_request_list_accepts_singleton_relation_sort(self) -> None:
        query = """
        query {
          requestSortItemList(orderBy: [{field: project__name}]) {
            items { id project { name } }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["requestSortItemList"]["items"]
        self.assertEqual([int(item["id"]) for item in items], [2, 3, 1])
        self.assertEqual(
            [item["project"]["name"] for item in items],
            ["Alpha", "Alpha", "Zulu"],
        )
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(self.transport.requests[0].operation_name, "list")

    def test_generated_request_list_reverses_compound_root_keys(self) -> None:
        alpha_id = self.transport.payload[0]["projectId"]
        self.transport.payload = [
            {"identifier": 3, "rootKey": "Alpha", "projectId": alpha_id},
            {"identifier": 1, "rootKey": "Beta", "projectId": alpha_id},
            {"identifier": 2, "rootKey": "Alpha", "projectId": alpha_id},
        ]
        query = """
        query {
          requestSortItemList(
            orderBy: [{field: rootKey, direction: DESC}, {field: id, direction: DESC}]
          ) {
            items { id rootKey }
          }
        }
        """

        response = self.query(query)

        self.assertResponseNoErrors(response)
        items = response.json()["data"]["requestSortItemList"]["items"]
        self.assertEqual([int(item["id"]) for item in items], [1, 3, 2])
        self.assertEqual(
            [item["rootKey"] for item in items],
            ["Beta", "Alpha", "Alpha"],
        )
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(self.transport.requests[0].operation_name, "list")

    def test_partial_disjunctive_request_still_rejects_pagination(self):
        original_send = self.remote_transport.send

        def partial(*args, **kwargs):
            response = original_send(*args, **kwargs)
            response.payload["total_count"] = 100
            return response

        with patch.object(self.remote_transport, "send", side_effect=partial):
            response = self.query(
                "{ disjunctiveRemoteRequestSortItemList { items { id } } }"
            )
        self.assertResponseHasErrors(response)
        self.assertIn("global pagination", response.json()["errors"][0]["message"])

    def test_complete_permission_union_does_not_reapply_remote_predicates_locally(self):
        from general_manager.bucket._materialized_bucket import MaterializedBucket

        with patch.object(
            MaterializedBucket,
            "filter",
            side_effect=AssertionError(
                "remote predicates must not be reinterpreted locally"
            ),
        ):
            response = self.query(
                "{ disjunctiveRemoteRequestSortItemList { items { id } pageInfo { totalCount } } }"
            )
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["disjunctiveRemoteRequestSortItemList"]["pageInfo"][
                "totalCount"
            ],
            2,
        )
