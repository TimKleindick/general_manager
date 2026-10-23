from typing import ClassVar
from types import SimpleNamespace

import graphene
from django.contrib.auth import get_user_model

from general_manager.api.graphql import GraphQL
from general_manager.interface import RequestInterface
from general_manager.interface.requests import (
    RequestField,
    RequestNotFoundError,
    RequestQueryOperation,
    RequestQueryPlan,
    RequestQueryResult,
    RequestTransportError,
)
from general_manager.manager import GeneralManager, Input
from general_manager.permission import AdditiveManagerPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestRequestDetailReadSecurity(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class RemoteConfidentialItem(GeneralManager):
            class Interface(RequestInterface):
                id = Input(int)
                name = RequestField(str)

                class Meta:
                    query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                        "detail": RequestQueryOperation(
                            name="detail", method="GET", path="/items/{id}"
                        ),
                        "list": RequestQueryOperation(
                            name="list", method="GET", path="/items"
                        ),
                    }

                @classmethod
                def execute_request_plan(
                    cls, plan: RequestQueryPlan
                ) -> RequestQueryResult:
                    identifier = plan.path_params["id"]
                    if identifier == 0:
                        raise RequestNotFoundError
                    if identifier == 3:
                        raise RequestTransportError
                    return RequestQueryResult(
                        items=(
                            {
                                "id": identifier,
                                "name": "Hidden" if identifier == 1 else "Visible",
                            },
                        )
                    )

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

                def can_read_instance(self) -> bool:
                    return self.instance.name != "Hidden"

        cls.general_manager_classes = [RemoteConfidentialItem]

    def test_remote_missing_and_denied_details_have_same_response(self) -> None:
        user = get_user_model().objects.create_user(username="remote-detail-reader")
        schema = graphene.Schema(query=GraphQL._query_class)
        responses = [
            schema.execute(
                "{ remoteConfidentialItem(id: "
                + str(identifier)
                + ") { __typename } }",
                context_value=SimpleNamespace(user=user),
            ).formatted
            for identifier in (0, 1, 2, 3)
        ]
        self.assertEqual(responses[0], {"data": {"remoteConfidentialItem": None}})
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(
            responses[2]["data"]["remoteConfidentialItem"],
            {"__typename": "RemoteConfidentialItemType"},
        )
        self.assertIsNone(responses[3]["data"]["remoteConfidentialItem"])
        self.assertIn("errors", responses[3])
