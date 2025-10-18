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


class GraphQLSubscriptionConsumer(AsyncJsonWebsocketConsumer):
    """
    Websocket consumer implementing the ``graphql-transport-ws`` protocol for GraphQL subscriptions.

    The consumer streams results produced by the dynamically generated GeneralManager GraphQL schema so
    clients such as GraphiQL can subscribe to live updates.
    """

    connection_acknowledged: bool
    connection_params: dict[str, Any]

    async def connect(self) -> None:
        self.connection_acknowledged = False
        self.connection_params = {}
        self.active_subscriptions: dict[str, asyncio.Task[None]] = {}
        subprotocols = self.scope.get("subprotocols", [])
        selected_subprotocol = (
            "graphql-transport-ws" if "graphql-transport-ws" in subprotocols else None
        )
        await self.accept(subprotocol=selected_subprotocol)

    async def disconnect(self, code: int) -> None:
        tasks = list(self.active_subscriptions.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.active_subscriptions.clear()

    async def receive_json(self, content: dict[str, Any], **_: Any) -> None:
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
        payload = content.get("payload")
        response: dict[str, Any] = {"type": "pong"}
        if payload is not None:
            response["payload"] = payload
        await self._send_protocol_message(response)

    async def _handle_subscribe(self, content: dict[str, Any]) -> None:
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
            await self._send_protocol_message(
                {"type": "complete", "id": operation_id}
            )
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
            await self._send_protocol_message(
                {"type": "complete", "id": operation_id}
            )
            return

        variables = payload.get("variables")
        if variables is not None and not isinstance(variables, dict):
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [{"message": "Variables must be provided as an object."}],
                }
            )
            await self._send_protocol_message(
                {"type": "complete", "id": operation_id}
            )
            return

        operation_name = payload.get("operationName")
        if operation_name is not None and not isinstance(operation_name, str):
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [
                        {"message": "The operation name must be a string when provided."}
                    ],
                }
            )
            await self._send_protocol_message(
                {"type": "complete", "id": operation_id}
            )
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
            await self._send_protocol_message(
                {"type": "complete", "id": operation_id}
            )
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
            await self._send_protocol_message(
                {"type": "complete", "id": operation_id}
            )
            return
        except Exception as error:  # pragma: no cover - defensive safeguard
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [{"message": str(error)}],
                }
            )
            await self._send_protocol_message(
                {"type": "complete", "id": operation_id}
            )
            return

        if isinstance(subscription, ExecutionResult):
            await self._send_execution_result(operation_id, subscription)
            await self._send_protocol_message(
                {"type": "complete", "id": operation_id}
            )
            return

        if operation_id in self.active_subscriptions:
            await self._stop_subscription(operation_id)

        self.active_subscriptions[operation_id] = asyncio.create_task(
            self._stream_subscription(operation_id, subscription)
        )

    async def _handle_complete(self, content: dict[str, Any]) -> None:
        operation_id = content.get("id")
        if isinstance(operation_id, str):
            await self._stop_subscription(operation_id)

    async def _stream_subscription(
        self, operation_id: str, async_iterator: Any
    ) -> None:
        try:
            async for result in async_iterator:
                await self._send_execution_result(operation_id, result)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # pragma: no cover - defensive safeguard
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [{"message": str(error)}],
                }
            )
        finally:
            await self._close_iterator(async_iterator)
            await self._send_protocol_message(
                {"type": "complete", "id": operation_id}
            )
            self.active_subscriptions.pop(operation_id, None)

    async def _stop_subscription(self, operation_id: str) -> None:
        task = self.active_subscriptions.pop(operation_id, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _send_execution_result(
        self, operation_id: str, result: ExecutionResult
    ) -> None:
        payload: dict[str, Any] = {}
        if result.data is not None:
            payload["data"] = result.data
        if result.errors:
            payload["errors"] = [self._format_error(error) for error in result.errors]
        await self._send_protocol_message(
            {"type": "next", "id": operation_id, "payload": payload}
        )

    async def _send_protocol_message(self, message: dict[str, Any]) -> None:
        try:
            await self.send_json(message)
        except RuntimeError:
            # The connection has already been closed. There is nothing else to send.
            pass

    def _build_context(self) -> Any:
        user = self.scope.get("user")
        raw_headers = self.scope.get("headers") or []
        headers = {
            (key.decode("latin1") if isinstance(key, (bytes, bytearray)) else key): (
                value.decode("latin1") if isinstance(value, (bytes, bytearray)) else value
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
        return schema.subscription_type is None

    @staticmethod
    def _format_error(error: Exception) -> dict[str, Any]:
        if isinstance(error, GraphQLError):
            return cast(dict[str, Any], error.formatted)
        return {"message": str(error)}

    @staticmethod
    async def _close_iterator(async_iterator: Any) -> None:
        close = getattr(async_iterator, "aclose", None)
        if close is None:
            return
        await close()
