from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any, cast

from channels.generic.websocket import AsyncJsonWebsocketConsumer  # type: ignore[import-untyped]
from graphql import (
    ExecutionResult,
    GraphQLError,
    GraphQLSchema,
    parse,
    subscribe,
)

from general_manager.api.graphql import GraphQL


RECOVERABLE_SUBSCRIPTION_ERRORS: tuple[type[Exception], ...] = (
    RuntimeError,
    ValueError,
    TypeError,
    LookupError,
    ConnectionError,
    KeyError,
    asyncio.TimeoutError,
)


class GraphQLSubscriptionConsumer(AsyncJsonWebsocketConsumer):
    """
    Websocket consumer implementing the ``graphql-transport-ws`` protocol for GraphQL subscriptions.

    The consumer streams results produced by the dynamically generated GeneralManager GraphQL schema so
    clients such as GraphiQL can subscribe to live updates.
    """

    connection_acknowledged: bool
    connection_params: dict[str, Any]

    async def connect(self) -> None:
        """
        Initialize connection state and accept the WebSocket, preferring the "graphql-transport-ws" subprotocol when offered.

        Sets up initial flags and containers used for subscription management (connection_acknowledged, connection_params, active_subscriptions) and accepts the WebSocket connection with the selected subprotocol.
        """
        self.connection_acknowledged = False
        self.connection_params = {}
        self.active_subscriptions: dict[str, asyncio.Task[None]] = {}
        subprotocols = self.scope.get("subprotocols", [])
        selected_subprotocol = (
            "graphql-transport-ws" if "graphql-transport-ws" in subprotocols else None
        )
        await self.accept(subprotocol=selected_subprotocol)

    async def disconnect(self, code: int) -> None:
        """
        Perform cleanup on WebSocket disconnect by cancelling and awaiting active subscription tasks and clearing the subscription registry.

        Parameters:
            code (int): WebSocket close code received from the connection.

        Notes:
            Awaiting cancelled tasks suppresses asyncio.CancelledError so task cancellation completes silently.
        """
        tasks = list(self.active_subscriptions.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.active_subscriptions.clear()

    async def receive_json(self, content: dict[str, Any], **_: Any) -> None:
        """
        Route an incoming graphql-transport-ws protocol message to the corresponding handler based on its "type" field.

        Valid message types: "connection_init", "ping", "subscribe", and "complete". Messages with an unrecognized or missing "type" cause the connection to be closed with code 4400.

        Parameters:
            content (dict[str, Any]): The received JSON message; expected to include a "type" key indicating the protocol action.
        """
        message_type = content.get("type")
        if message_type == "connection_init":
            await self._handle_connection_init(content)
        elif message_type == "ping":
            await self._handle_ping(content)
        elif message_type == "subscribe":
            await self._handle_subscribe(content)
        elif message_type == "complete":
            await self._handle_complete(content)
        else:
            await self.close(code=4400)

    async def _handle_connection_init(self, content: dict[str, Any]) -> None:
        """
        Handle a client's "connection_init" message and send a protocol acknowledgment.

        If the connection has already been acknowledged, closes the WebSocket with code 4429.
        If the incoming message contains a "payload" that is a dict, stores it on
        self.connection_params; otherwise clears connection_params. Marks the connection
        as acknowledged and sends a "connection_ack" protocol message.

        Parameters:
            content (dict[str, Any]): The received WebSocket message for "connection_init".
        """
        if self.connection_acknowledged:
            await self.close(code=4429)
            return
        payload = content.get("payload")
        if isinstance(payload, dict):
            self.connection_params = payload
        else:
            self.connection_params = {}
        self.connection_acknowledged = True
        await self._send_protocol_message({"type": "connection_ack"})

    async def _handle_ping(self, content: dict[str, Any]) -> None:
        """
        Responds to an incoming ping by sending a pong protocol message.

        If the incoming `content` contains a `"payload"` key, its value is included in the sent pong message under the same key.

        Parameters:
            content (dict[str, Any]): The received message object; may include an optional `"payload"` to echo.
        """
        payload = content.get("payload")
        response: dict[str, Any] = {"type": "pong"}
        if payload is not None:
            response["payload"] = payload
        await self._send_protocol_message(response)

    async def _handle_subscribe(self, content: dict[str, Any]) -> None:
        """
        Handle an incoming GraphQL "subscribe" protocol message and initiate or deliver the corresponding subscription results.

        Parameters:
            content (dict[str, Any]): The incoming protocol message. Expected keys:
                - "id" (str): Operation identifier.
                - "payload" (dict): Operation payload containing:
                    - "query" (str): GraphQL query string (required).
                    - "variables" (dict, optional): Operation variables.
                    - "operationName" (str, optional): Named operation to execute.
        """
        if not self.connection_acknowledged:
            await self.close(code=4401)
            return

        operation_id = content.get("id")
        payload = content.get("payload", {})
        if not isinstance(operation_id, str) or not isinstance(payload, dict):
            await self.close(code=4403)
            return

        schema = GraphQL.get_schema()
        if schema is None or self._schema_has_no_subscription(schema.graphql_schema):
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [
                        {"message": "GraphQL subscriptions are not configured."}
                    ],
                }
            )
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            return

        query = payload.get("query")
        if not isinstance(query, str):
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [{"message": "A GraphQL query string is required."}],
                }
            )
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            return

        variables = payload.get("variables")
        if variables is not None and not isinstance(variables, dict):
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [
                        {"message": "Variables must be provided as an object."}
                    ],
                }
            )
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            return

        operation_name = payload.get("operationName")
        if operation_name is not None and not isinstance(operation_name, str):
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [
                        {
                            "message": "The operation name must be a string when provided."
                        }
                    ],
                }
            )
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            return

        try:
            document = parse(query)
        except GraphQLError as error:
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [error.formatted],
                }
            )
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            return

        context = self._build_context()

        try:
            subscription = await subscribe(
                schema.graphql_schema,
                document,
                variable_values=variables,
                operation_name=operation_name,
                context_value=context,
            )
        except GraphQLError as error:
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [error.formatted],
                }
            )
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            return
        except (
            RECOVERABLE_SUBSCRIPTION_ERRORS
        ) as error:  # pragma: no cover - defensive safeguard
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [{"message": str(error)}],
                }
            )
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            return

        if isinstance(subscription, ExecutionResult):
            await self._send_execution_result(operation_id, subscription)
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            return

        if operation_id in self.active_subscriptions:
            await self._stop_subscription(operation_id)

        self.active_subscriptions[operation_id] = asyncio.create_task(
            self._stream_subscription(operation_id, subscription)
        )

    async def _handle_complete(self, content: dict[str, Any]) -> None:
        """
        Handle an incoming "complete" protocol message by stopping the subscription for the specified operation.

        If the message payload contains an "id" field that is a string, the corresponding active subscription task is cancelled and cleaned up; otherwise the message is ignored.

        Parameters:
                content (dict[str, Any]): The received protocol message payload. Expected to contain an "id" key with the operation identifier.
        """
        operation_id = content.get("id")
        if isinstance(operation_id, str):
            await self._stop_subscription(operation_id)

    async def _stream_subscription(
        self, operation_id: str, async_iterator: Any
    ) -> None:
        """
        Stream execution results from an async iterator to the client for a subscription operation.

        Sends each yielded execution result for the given operation_id to the client. If a recoverable error occurs while iterating, sends an error payload for the operation. In all cases, attempts to close the iterator, sends a completion message for the operation, and removes the operation from active_subscriptions.

        Parameters:
            operation_id (str): The subscription operation identifier used in protocol messages.
            async_iterator (Any): An asynchronous iterator that yields execution result objects to be sent to the client.

        Raises:
            asyncio.CancelledError: Propagated when the surrounding subscription task is cancelled.
        """
        try:
            async for result in async_iterator:
                await self._send_execution_result(operation_id, result)
        except asyncio.CancelledError:
            raise
        except (
            RECOVERABLE_SUBSCRIPTION_ERRORS
        ) as error:  # pragma: no cover - defensive safeguard
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [{"message": str(error)}],
                }
            )
        finally:
            await self._close_iterator(async_iterator)
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            self.active_subscriptions.pop(operation_id, None)

    async def _stop_subscription(self, operation_id: str) -> None:
        """
        Cancel and await the active subscription task for the given operation id, if one exists.

        If a task is found for operation_id it is cancelled and awaited; CancelledError raised during awaiting is suppressed. If no task exists this is a no-op.
        """
        task = self.active_subscriptions.pop(operation_id, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _send_execution_result(
        self, operation_id: str, result: ExecutionResult
    ) -> None:
        """
        Send a GraphQL execution result to the client as a "next" protocol message.

        The message payload includes a "data" field when result.data is present and an "errors" field when result.errors is non-empty; errors are converted to serializable dictionaries.

        Parameters:
            operation_id (str): The operation identifier to include as the message `id`.
            result (ExecutionResult): The GraphQL execution result to serialize and send.
        """
        payload: dict[str, Any] = {}
        if result.data is not None:
            payload["data"] = result.data
        if result.errors:
            payload["errors"] = [self._format_error(error) for error in result.errors]
        await self._send_protocol_message(
            {"type": "next", "id": operation_id, "payload": payload}
        )

    async def _send_protocol_message(self, message: dict[str, Any]) -> None:
        """
        Send a JSON-serializable GraphQL transport protocol message over the WebSocket.

        Parameters:
            message (dict[str, Any]): The protocol message to send. If the connection is already closed, the message is discarded silently.
        """
        try:
            await self.send_json(message)
        except RuntimeError:
            # The connection has already been closed. There is nothing else to send.
            pass

    def _build_context(self) -> Any:
        """
        Builds a request context object for GraphQL execution containing the current user, decoded headers, scope, and connection parameters.

        Returns:
            context (SimpleNamespace): An object with attributes:
                - `user`: the value of `scope["user"]` (may be None).
                - `headers`: a dict mapping header names to decoded string values.
                - `scope`: the consumer's `scope`.
                - `connection_params`: the connection parameters provided during `connection_init`.
        """
        user = self.scope.get("user")
        raw_headers = self.scope.get("headers") or []
        headers = {
            (key.decode("latin1") if isinstance(key, (bytes, bytearray)) else key): (
                value.decode("latin1")
                if isinstance(value, (bytes, bytearray))
                else value
            )
            for key, value in raw_headers
        }
        return SimpleNamespace(
            user=user,
            headers=headers,
            scope=self.scope,
            connection_params=self.connection_params,
        )

    @staticmethod
    def _schema_has_no_subscription(schema: GraphQLSchema) -> bool:
        """
        Check whether the provided GraphQL schema defines no subscription root type.

        Parameters:
            schema (GraphQLSchema): The schema to inspect.

        Returns:
            bool: `True` if the schema has no subscription type, `False` otherwise.
        """
        return schema.subscription_type is None

    @staticmethod
    def _format_error(error: Exception) -> dict[str, Any]:
        """
        Format an exception as a GraphQL-compatible error dictionary.

        Parameters:
            error (Exception): The exception to format; if a `GraphQLError`, its `.formatted` representation is used.

        Returns:
            dict[str, Any]: The error payload: the `GraphQLError.formatted` mapping for GraphQLError instances, otherwise `{"message": str(error)}`.
        """
        if isinstance(error, GraphQLError):
            return cast(dict[str, Any], error.formatted)
        return {"message": str(error)}

    @staticmethod
    async def _close_iterator(async_iterator: Any) -> None:
        """
        Close an asynchronous iterator by awaiting its `aclose` coroutine if present.

        Parameters:
            async_iterator (Any): The iterator to close; if it defines an `aclose` coroutine method, that coroutine will be awaited.
        """
        close = getattr(async_iterator, "aclose", None)
        if close is None:
            return
        await close()
