import asyncio
import json
from contextlib import suppress
from unittest.mock import patch

from asgiref.testing import ApplicationCommunicator
from django.db.models import CharField
from graphql import ExecutionResult

from example_project.website.asgi import application
from general_manager.api.graphql import GraphQL
from general_manager.interface.databaseInterface import DatabaseInterface
# NOTE: Circular import is fine in tests.
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
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
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
            await self._send_json(
                communicator,
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
                },
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

            await self._send_json(communicator, {"type": "complete", "id": "sub-1"})
            completion_message = await communicator.receive_output()
            assert completion_message["type"] == "websocket.send"
            completion = json.loads(completion_message["text"])
            assert completion == {"type": "complete", "id": "sub-1"}

            await self._disconnect(communicator)

        asyncio.run(run_subscription_test())

    def test_connection_init_without_subprotocol(self) -> None:
        async def run_test() -> None:
            communicator, accept = await self._connect(subprotocols=[])
            assert accept["type"] == "websocket.accept"
            assert accept.get("subprotocol") is None

            await self._send_json(communicator, {"type": "connection_init"})
            ack = await communicator.receive_output()
            payload = json.loads(ack["text"])
            assert payload["type"] == "connection_ack"

            await self._disconnect(communicator)

        asyncio.run(run_test())

    def test_connection_init_twice_closes(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()  # connection_ack

            await self._send_json(communicator, {"type": "connection_init"})
            close = await communicator.receive_output()
            assert close["type"] == "websocket.close"
            assert close.get("code") == 4429
            await communicator.wait()

        asyncio.run(run_test())

    def test_subscribe_without_connection_init(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(
                communicator,
                {"id": "sub-unauth", "type": "subscribe", "payload": {}},
            )
            close = await communicator.receive_output()
            assert close["type"] == "websocket.close"
            assert close.get("code") == 4401
            await communicator.wait()

        asyncio.run(run_test())

    def test_subscribe_invalid_identifier(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()

            await self._send_json(
                communicator,
                {"id": 123, "type": "subscribe", "payload": {}},
            )
            close = await communicator.receive_output()
            assert close["type"] == "websocket.close"
            assert close.get("code") == 4403
            await communicator.wait()

        asyncio.run(run_test())

    def test_subscribe_without_schema(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()

            with patch.object(GraphQL, "get_schema", return_value=None):
                await self._send_json(
                    communicator,
                    {
                        "id": "sub-none",
                        "type": "subscribe",
                        "payload": {"query": "subscription { dummy }"},
                    },
                )
                error_msg = await communicator.receive_output()
                assert error_msg["type"] == "websocket.send"
                error_payload = json.loads(error_msg["text"])
                assert error_payload["type"] == "error"
                assert error_payload["id"] == "sub-none"
                assert error_payload["payload"][0]["message"].startswith(
                    "GraphQL subscriptions are not configured."
                )
                complete_msg = await communicator.receive_output()
                assert json.loads(complete_msg["text"]) == {
                    "type": "complete",
                    "id": "sub-none",
                }

            await self._disconnect(communicator)

        asyncio.run(run_test())

    def test_subscribe_without_subscription_type(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()

            dummy_schema = type(
                "DummySchema",
                (),
                {"graphql_schema": type("SchemaObj", (), {"subscription_type": None})()},
            )()

            with patch.object(GraphQL, "get_schema", return_value=dummy_schema):
                await self._send_json(
                    communicator,
                    {
                        "id": "sub-nosub",
                        "type": "subscribe",
                        "payload": {"query": "subscription { dummy }"},
                    },
                )
                error_msg = await communicator.receive_output()
                error_payload = json.loads(error_msg["text"])
                assert error_payload["type"] == "error"
                complete_msg = await communicator.receive_output()
                assert json.loads(complete_msg["text"]) == {
                    "type": "complete",
                    "id": "sub-nosub",
                }

            await self._disconnect(communicator)

        asyncio.run(run_test())

    def test_subscribe_query_not_string(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()

            await self._send_json(
                communicator,
                {"id": "sub-noquery", "type": "subscribe", "payload": {"query": 10}},
            )
            error_msg = await communicator.receive_output()
            assert json.loads(error_msg["text"])["payload"][0]["message"].startswith(
                "A GraphQL query string is required."
            )
            complete_msg = await communicator.receive_output()
            assert json.loads(complete_msg["text"]) == {
                "type": "complete",
                "id": "sub-noquery",
            }

            await self._disconnect(communicator)

        asyncio.run(run_test())

    def test_subscribe_invalid_variables(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()

            await self._send_json(
                communicator,
                {
                    "id": "sub-vars",
                    "type": "subscribe",
                    "payload": {"query": "subscription { dummy }", "variables": "x"},
                },
            )
            error_msg = await communicator.receive_output()
            assert json.loads(error_msg["text"])["payload"][0]["message"].startswith(
                "Variables must be provided as an object."
            )
            complete_msg = await communicator.receive_output()
            assert json.loads(complete_msg["text"]) == {
                "type": "complete",
                "id": "sub-vars",
            }

            await self._disconnect(communicator)

        asyncio.run(run_test())

    def test_subscribe_invalid_operation_name(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()

            await self._send_json(
                communicator,
                {
                    "id": "sub-opname",
                    "type": "subscribe",
                    "payload": {
                        "query": "subscription Test { onProjectChange(id: \"1\") { action } }",
                        "operationName": 999,
                        "variables": {"id": "1"},
                    },
                },
            )
            error_msg = await communicator.receive_output()
            assert json.loads(error_msg["text"])["payload"][0]["message"].startswith(
                "The operation name must be a string"
            )
            complete_msg = await communicator.receive_output()
            assert json.loads(complete_msg["text"]) == {
                "type": "complete",
                "id": "sub-opname",
            }

            await self._disconnect(communicator)

        asyncio.run(run_test())

    def test_subscribe_parse_error(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()

            await self._send_json(
                communicator,
                {"id": "sub-parse", "type": "subscribe", "payload": {"query": "subscription {"}},
            )
            error_msg = await communicator.receive_output()
            payload = json.loads(error_msg["text"])
            assert payload["type"] == "error"
            assert payload["id"] == "sub-parse"
            assert payload["payload"][0]["message"].startswith("Syntax Error")
            complete_msg = await communicator.receive_output()
            assert json.loads(complete_msg["text"]) == {
                "type": "complete",
                "id": "sub-parse",
            }

            await self._disconnect(communicator)

        asyncio.run(run_test())

    def test_subscribe_returns_execution_result(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()

            execution_result = ExecutionResult(data={"ok": True}, errors=None)
            with patch(
                "general_manager.api.graphql_subscription_consumer.subscribe",
                return_value=execution_result,
            ):
                await self._send_json(
                    communicator,
                    {
                        "id": "sub-exec",
                        "type": "subscribe",
                        "payload": {"query": "subscription { dummy }"},
                    },
                )
                next_msg = await communicator.receive_output()
                next_payload = json.loads(next_msg["text"])
                assert next_payload["type"] == "next"
                assert next_payload["payload"]["data"] == {"ok": True}
                complete_msg = await communicator.receive_output()
                assert json.loads(complete_msg["text"]) == {
                    "type": "complete",
                    "id": "sub-exec",
                }

            await self._disconnect(communicator)

        asyncio.run(run_test())

    def test_ping_pong(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(communicator, {"type": "connection_init"})
            await communicator.receive_output()

            await self._send_json(
                communicator,
                {"type": "ping", "payload": {"trace": "123"}},
            )
            pong_message = await communicator.receive_output()
            assert pong_message["type"] == "websocket.send"
            payload = json.loads(pong_message["text"])
            assert payload == {"type": "pong", "payload": {"trace": "123"}}

            await self._disconnect(communicator)

        asyncio.run(run_test())

    def test_connection_init_non_dict_payload(self) -> None:
        async def run_test() -> None:
            communicator, _ = await self._connect()
            await self._send_json(
                communicator, {"type": "connection_init", "payload": "ignored"}
            )
            ack_message = await communicator.receive_output()
            ack_payload = json.loads(ack_message["text"])
            assert ack_payload["type"] == "connection_ack"

            await self._disconnect(communicator)

        asyncio.run(run_test())

    async def _connect(
        self, subprotocols: list[str] | None = None
    ) -> tuple[ApplicationCommunicator, dict[str, object]]:
        selected_subprotocols = (
            ["graphql-transport-ws"] if subprotocols is None else subprotocols
        )
        communicator = ApplicationCommunicator(
            application,
            {
                "type": "websocket",
                "path": "/graphql/",
                "headers": [],
                "query_string": b"",
                "client": ("testserver", 80),
                "server": ("testserver", 80),
                "subprotocols": selected_subprotocols,
            },
        )
        await communicator.send_input({"type": "websocket.connect"})
        accept = await communicator.receive_output()
        return communicator, accept

    async def _send_json(
        self, communicator: ApplicationCommunicator, message: dict[str, object]
    ) -> None:
        await communicator.send_input(
            {"type": "websocket.receive", "text": json.dumps(message)}
        )

    async def _disconnect(self, communicator: ApplicationCommunicator) -> None:
        with suppress(Exception):
            await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait()
