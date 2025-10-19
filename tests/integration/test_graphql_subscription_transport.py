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
        """
        Prepare the test class by registering a temporary Project GeneralManager and invoking superclass setup.
        
        Defines an inner Project GeneralManager with a `name` CharField and permissive ManagerBasedPermission, assigns it to `general_manager_classes`, `read_only_classes`, and `Project` on the test class, then calls the superclass `setUpClass`.
        """
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
        """
        Prepare test state by running base setup and creating a Demo Project instance.
        
        This calls the superclass setup and creates a Project stored on self.project using ignore_permission=True with the name "Demo".
        """
        super().setUp()
        self.project = self.Project.create(ignore_permission=True, name="Demo")

    def test_subscription_snapshot_event(self) -> None:
        """
        Establish a websocket subscription and assert that the initial snapshot event is delivered.
        """

        async def run_subscription_test() -> None:
            """
            Run a GraphQL subscription flow and assert the initial "snapshot" event is received.
            
            Connects to the test WebSocket, performs the GraphQL Transport WS handshake, sends a subscription for ProjectChanges using the test project ID, verifies a `next` message whose payload's `onProjectChange.action` equals "snapshot", sends a complete for the subscription, and then disconnects. This function performs assertions on the protocol messages and payloads as part of the test.
            """
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
        """
        Verifies that a WebSocket connection opened without the graphql-transport-ws subprotocol is accepted and that a subsequent connection_init receives a connection_ack.
        
        This test connects with no subprotocol, asserts the accept message contains no subprotocol, sends a connection_init message, and asserts the server responds with a connection_ack before disconnecting.
        """
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
        """
        Verify that sending `connection_init` twice causes the server to close the WebSocket with code 4429.
        
        Establishes a GraphQL transport WebSocket, sends an initial `connection_init` and expects a `connection_ack`, then sends a second `connection_init` and asserts the connection is closed with code 4429.
        """
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
        """
        Verify that sending a `subscribe` message before a `connection_init` causes the server to close the WebSocket.
        
        Asserts that the connection is closed with code `4401`.
        """
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
        """
        Verify that sending a `subscribe` message with a non-string `id` causes the server to close the WebSocket with close code 4403.
        
        This test establishes a connection, performs `connection_init`, then sends a `subscribe` payload whose `id` is a number and asserts the connector responds with a `websocket.close` event with code 4403.
        """
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
            """
            Verify that attempting to subscribe when GraphQL subscriptions are not configured results in an error message for the subscription id followed by a completion message.
            
            This test establishes a websocket connection, sends a subscription request while GraphQL.get_schema is patched to return None, and asserts that the consumer sends an `error` payload whose message begins with "GraphQL subscriptions are not configured." and then sends a `complete` message for the same subscription id.
            """
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
        """
        Verify that attempting to subscribe when the GraphQL schema has no Subscription type yields an error and a following completion for the subscription id.
        
        The test:
        - Establishes a websocket connection and sends `connection_init`.
        - Patches GraphQL.get_schema to return a schema whose `subscription_type` is `None`.
        - Sends a `subscribe` message and asserts an `error` message is received for the subscription.
        - Asserts a `complete` message with the same subscription id follows.
        """
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
            """
            Verifies that sending a subscribe message with a non-string `query` results in a GraphQL query string error and a following complete message.
            
            Sends a `connection_init`, issues a `subscribe` where `payload.query` is not a string, and asserts the server first returns an error whose message starts with "A GraphQL query string is required." and then sends a `complete` message with the same subscription id before disconnecting.
            """
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
        """
        Verify that a subscription request whose `variables` value is not an object produces an error and a completion.
        
        Establishes a websocket connection, sends a subscribe payload with `"variables"` set to a non-object, asserts the server responds with an error message beginning with "Variables must be provided as an object." for the subscription id `"sub-vars"`, and then asserts a `"complete"` message for that id before disconnecting.
        """
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
            """
            Validate that sending a subscribe message with a non-string operationName yields an error and a subsequent complete message.
            
            Connects to the websocket, performs the GraphQL `connection_init` handshake, sends a `subscribe` payload whose `operationName` is not a string, asserts that the server returns an error message beginning with "The operation name must be a string", verifies a following `complete` message for the same subscription id, and then disconnects.
            """
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
            """
            Verify that sending an invalid GraphQL subscription query produces a syntax error payload for the subscription id and a following complete message.
            
            Opens a websocket connection, performs connection initialization, sends a subscribe message with an unterminated query, asserts the error payload contains a GraphQL "Syntax Error" message and the original subscription id, then asserts a subsequent complete message for that id.
            """
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
        """
        Verifies that a GraphQL subscription that yields an ExecutionResult sends a `next` message containing the result data and then a `complete` message for the subscription id.
        
        The test patches the subscription handler to return an `ExecutionResult(data={"ok": True})`, performs a subscribe exchange, and asserts the consumer emits a `next` payload with `{"ok": True}` followed by a `complete` for the same id.
        """
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
        """
        Verify that the server responds to a ping with a matching pong payload.
        
        Sends a ping message containing a payload and asserts the consumer returns a pong message with an identical payload.
        """
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
            """
            Perform a WebSocket connection, send a `connection_init` with a non-dictionary payload, and assert the server responds with `connection_ack`.
            
            This test verifies that the server accepts a `connection_init` message whose `payload` is not a mapping and still returns a `connection_ack` before closing the connection.
            """
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
        """
        Establishes a test WebSocket connection to the GraphQL ASGI application and returns the communicator and the server's accept message.
        
        Parameters:
            subprotocols (list[str] | None): Subprotocols to request during the WebSocket handshake. If None, defaults to ["graphql-transport-ws"].
        
        Returns:
            tuple[ApplicationCommunicator, dict[str, object]]: A tuple containing the ApplicationCommunicator connected to the ASGI app and the accept event (the server's websocket.accept message).
        """
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
        """
        Send a JSON-encoded websocket.receive event to the given ASGI test communicator.
        
        Parameters:
            communicator (ApplicationCommunicator): The ASGI test communicator to receive the input.
            message (dict[str, object]): JSON-serializable payload to send as the websocket text frame.
        """
        await communicator.send_input(
            {"type": "websocket.receive", "text": json.dumps(message)}
        )

    async def _disconnect(self, communicator: ApplicationCommunicator) -> None:
        """
        Gracefully close the test ASGI websocket connection and wait for its shutdown.
        
        Sends a websocket.disconnect event with code 1000 to the provided ApplicationCommunicator, suppresses any exceptions raised while sending the disconnect, and then waits for the communicator to finish processing and close.
        
        Parameters:
            communicator (ApplicationCommunicator): The ASGI test communicator for the websocket connection.
        """
        with suppress(Exception):
            await communicator.send_input({"type": "websocket.disconnect", "code": 1000})
        await communicator.wait()