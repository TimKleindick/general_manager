import asyncio
import json

from asgiref.testing import ApplicationCommunicator
from django.db.models import CharField

from example_project.website.asgi import application
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager.generalManager import GeneralManager
from general_manager.permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLSubscriptionTransport(GeneralManagerTransactionTestCase):
    """
    Validate that the websocket consumer streams subscription updates using the graphql-transport-ws protocol.
    """

    @classmethod
    def setUpClass(cls) -> None:
        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

            class Permission(ManagerBasedPermission):
                __read__ = ["public"]
                __create__ = ["public"]
                __update__ = ["public"]
                __delete__ = ["public"]

        cls.general_manager_classes = [Project]
        cls.read_only_classes = []
        cls.Project = Project
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.project = self.Project.create(ignore_permission=True, name="Demo")

    def test_subscription_snapshot_event(self) -> None:
        """
        Establish a websocket subscription and assert that the initial snapshot event is delivered.
        """

        async def run_subscription_test() -> None:
            communicator = ApplicationCommunicator(
                application,
                {
                    "type": "websocket",
                    "path": "/graphql/",
                    "headers": [],
                    "query_string": b"",
                    "client": ("testserver", 80),
                    "server": ("testserver", 80),
                    "subprotocols": ["graphql-transport-ws"],
                },
            )

            await communicator.send_input({"type": "websocket.connect"})
            accept = await communicator.receive_output()
            assert accept["type"] == "websocket.accept"
            assert accept.get("subprotocol") == "graphql-transport-ws"

            await communicator.send_input(
                {"type": "websocket.receive", "text": json.dumps({"type": "connection_init"})}
            )
            ack_message = await communicator.receive_output()
            assert ack_message["type"] == "websocket.send"
            ack = json.loads(ack_message["text"])
            assert ack["type"] == "connection_ack"

            query = """
            subscription ProjectChanges($id: ID!) {
                onProjectChange(id: $id) {
                    action
                }
            }
            """
            await communicator.send_input(
                {
                    "type": "websocket.receive",
                    "text": json.dumps(
                        {
                            "id": "sub-1",
                            "type": "subscribe",
                            "payload": {
                                "query": query,
                                "operationName": "ProjectChanges",
                                "variables": {
                                    "id": str(self.project.identification["id"])
                                },
                            },
                        }
                    ),
                }
            )

            next_message = await communicator.receive_output()
            assert next_message["type"] == "websocket.send"
            next_payload = json.loads(next_message["text"])
            assert next_payload["type"] == "next"
            assert next_payload["id"] == "sub-1"
            assert (
                next_payload["payload"]["data"]["onProjectChange"]["action"]
                == "snapshot"
            )

            await communicator.send_input(
                {"type": "websocket.receive", "text": json.dumps({"type": "complete", "id": "sub-1"})}
            )
            completion_message = await communicator.receive_output()
            assert completion_message["type"] == "websocket.send"
            completion = json.loads(completion_message["text"])
            assert completion == {"type": "complete", "id": "sub-1"}

            await communicator.send_input(
                {"type": "websocket.disconnect", "code": 1000}
            )
            await communicator.wait()

        asyncio.run(run_subscription_test())
