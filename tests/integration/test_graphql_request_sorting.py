from __future__ import annotations

from typing import Any, ClassVar

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.utils.crypto import get_random_string

from general_manager.interface import DatabaseInterface, RequestInterface
from general_manager.interface.requests import (
    RequestField,
    RequestPayload,
    RequestPlan,
    RequestQueryOperation,
    RequestTransportConfig,
    RequestTransportRequest,
    RequestTransportResponse,
    SharedRequestTransport,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class FakeRequestRelationSortTransport(SharedRequestTransport):
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
        return RequestTransportResponse(payload=list(self.payload), status_code=200)


class TestGraphQLRequestRelationSorting(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        transport = FakeRequestRelationSortTransport()

        class RequestSortProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        def normalize_request_sort_item(payload: RequestPayload) -> dict[str, object]:
            return {
                "id": payload["identifier"],
                "root_key": payload["rootKey"],
                "project": RequestSortProject(id=payload["projectId"]),
            }

        class RequestSortItem(GeneralManager):
            class Interface(RequestInterface):
                id = Input(type=int)
                root_key = RequestField(str)
                project = RequestField(RequestSortProject)

                class Meta:
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

        RequestSortItem.Interface.transport = transport
        cls.general_manager_classes = [RequestSortProject, RequestSortItem]
        cls.request_sort_project = RequestSortProject
        cls.request_sort_item = RequestSortItem
        cls.transport = transport

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
        self.transport.payload = [
            {"identifier": 3, "rootKey": "Able", "projectId": alpha.id},
            {"identifier": 1, "rootKey": "Beta", "projectId": zulu.id},
            {"identifier": 2, "rootKey": "Zed", "projectId": alpha.id},
        ]

    def test_generated_request_list_hydrates_relation_and_sorts_compound_keys(
        self,
    ) -> None:
        query = """
        query {
          requestSortItemList(
            sortBy: [project__name, root_key]
            reverse: true
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
