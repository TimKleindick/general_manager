from __future__ import annotations

from decimal import Decimal
from typing import Any, ClassVar

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

    def test_partial_request_page_rejects_global_grouping(self) -> None:
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
                groups { keys { rootKey } }
              }
            }
            """
        )

        self.assertResponseHasErrors(response)
        self.assertIn(
            "global grouping",
            response.json()["errors"][0]["message"],
        )

    def test_empty_request_source_rejects_unknown_group_key_before_transport(
        self,
    ) -> None:
        """Invalid group keys cannot trigger a request fetch on an empty source."""
        self.transport.payload = []

        response = self.query(
            """
            query {
              requestSortItemGroups(groupBy: ["unknownKey"]) {
                groups { count }
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

    def test_request_groups_sum_optional_numeric_fields_and_keep_all_null_none(
        self,
    ) -> None:
        """Generated grouped sums unwrap Optional[T] without admitting unions or bools."""
        project_id = self.transport.payload[0]["projectId"]
        self.transport.payload = [
            {
                "identifier": 1,
                "rootKey": "mixed",
                "projectId": project_id,
                "optionalAmount": 2,
                "decimalAmount": "1.5",
                "distance": "1",
            },
            {
                "identifier": 2,
                "rootKey": "mixed",
                "projectId": project_id,
                "optionalAmount": None,
                "decimalAmount": "2.5",
                "distance": None,
            },
            {
                "identifier": 3,
                "rootKey": "nulls",
                "projectId": project_id,
                "optionalAmount": None,
                "decimalAmount": None,
                "distance": None,
            },
        ]

        response = self.query(
            """
            query {
              requestSortItemGroups(groupBy: ["rootKey"]) {
                groups {
                  keys { rootKey }
                  sums {
                    optionalAmount
                    decimalAmount
                    distance(targetUnit: "centimeter") { value unit }
                  }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        groups = response.json()["data"]["requestSortItemGroups"]["groups"]
        sums_by_key = {group["keys"]["rootKey"]: group["sums"] for group in groups}
        self.assertEqual(
            sums_by_key["mixed"],
            {
                "optionalAmount": 2,
                "decimalAmount": 4.0,
                "distance": {"value": 100.0, "unit": "centimeter"},
            },
        )
        self.assertEqual(
            sums_by_key["nulls"],
            {
                "optionalAmount": None,
                "decimalAmount": None,
                "distance": None,
            },
        )

    def test_remote_request_forwards_order_and_page_once(self) -> None:
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
        self.assertEqual(len(self.remote_transport.requests), 1)
        body = self.remote_transport.requests[0].body
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

    def test_disjunctive_permission_request_rejects_global_pagination(
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

        self.assertResponseHasErrors(response)
        self.assertIn("global pagination", response.json()["errors"][0]["message"])
        self.assertEqual(self.remote_transport.requests, [])

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
