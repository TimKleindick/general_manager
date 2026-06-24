from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Protocol, TypeGuard, cast

if TYPE_CHECKING:

    class AsyncJsonWebsocketConsumer:
        """Typed subset of Channels' async JSON websocket consumer."""

        scope: dict[str, object]

        @classmethod
        def as_asgi(
            cls,
            **initkwargs: object,
        ) -> Callable[
            [
                Mapping[str, object],
                Callable[[], Awaitable[object]],
                Callable[[Mapping[str, object]], Awaitable[None]],
            ],
            Awaitable[None],
        ]: ...

        async def accept(self, subprotocol: str | None = None) -> None: ...

        async def close(self, code: int | None = None) -> None: ...

        async def send_json(self, content: Mapping[str, object]) -> None: ...

else:
    from channels.generic.websocket import AsyncJsonWebsocketConsumer

from graphql import (
    ExecutionResult,
    GraphQLError,
    GraphQLSchema,
    parse,
    subscribe,
)

from general_manager.api.graphql import GraphQL
from general_manager.cache.run_context import ensure_calculation_run_context


RECOVERABLE_SUBSCRIPTION_ERRORS: tuple[type[Exception], ...] = (
    RuntimeError,
    ValueError,
    TypeError,
    LookupError,
    ConnectionError,
    KeyError,
    asyncio.TimeoutError,
)


@dataclass(slots=True)
class GraphQLSubscriptionContext:
    """
    Request context passed to GraphQL subscription resolvers.

    Attributes:
        user: Authenticated user from the Channels scope, or ``None``.
        headers: ASGI headers decoded as Latin-1 text. Duplicate header names
            keep the last value seen in the scope.
        scope: Original Channels scope for callers that need transport details.
        connection_params: Object payload from ``connection_init``. The mapping
            is shared with the consumer state and is not copied.
    """

    user: object | None
    headers: dict[str, str]
    scope: Mapping[str, object]
    connection_params: Mapping[str, object]


class _AsyncClosable(Protocol):
    """Object exposing an async close hook."""

    async def aclose(self) -> None: ...


def _supports_async_close(value: object) -> TypeGuard[_AsyncClosable]:
    """Return whether ``value`` exposes a callable ``aclose`` attribute.

    This check does not verify that calling ``aclose`` returns an awaitable; a
    callable that returns a non-awaitable fails later when ``_close_iterator``
    awaits it.
    """
    return callable(getattr(value, "aclose", None))


class GraphQLSubscriptionConsumer(AsyncJsonWebsocketConsumer):
    """
    Websocket consumer implementing the ``graphql-transport-ws`` protocol for GraphQL subscriptions.

    The consumer streams results produced by the dynamically generated
    GeneralManager GraphQL schema. It accepts ``connection_init``, ``ping``,
    ``subscribe``, and ``complete`` messages, sends protocol envelopes
    (``connection_ack``, ``pong``, ``next``, ``error``, and ``complete``), and
    closes malformed protocol messages without custom close reason text and
    with the close codes documented by the public GraphQL subscription guide:
    4400 for invalid message shape or type, 4401 for ``subscribe`` before
    acknowledgement, 4403 for invalid subscribe id/payload shape, and 4429 for
    repeated ``connection_init``.
    """

    connection_acknowledged: bool
    connection_params: dict[str, object]

    async def connect(self) -> None:
        """
        Initialize connection state and accept the WebSocket, preferring the "graphql-transport-ws" subprotocol when offered.

        Sets up initial flags and containers used for subscription management
        (connection_acknowledged, connection_params, active_subscriptions).
        The connection is accepted with ``"graphql-transport-ws"`` when the
        client offers it, otherwise it is still accepted without a selected
        subprotocol.
        """
        self.connection_acknowledged = False
        self.connection_params = {}
        self.active_subscriptions: dict[str, asyncio.Task[None]] = {}
        subprotocols = self.scope.get("subprotocols", ())
        selected_subprotocol = (
            "graphql-transport-ws"
            if isinstance(subprotocols, (list, tuple))
            and "graphql-transport-ws" in subprotocols
            else None
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

    async def receive_json(self, content: object, **_: object) -> None:
        """
        Route an incoming graphql-transport-ws protocol message to the corresponding handler based on its "type" field.

        Valid message types: "connection_init", "ping", "subscribe", and "complete". Messages with an unrecognized or missing "type" cause the connection to be closed with code 4400.
        Non-object messages also close with 4400. JSON parse failures are handled
        by the Channels JSON consumer before this method receives ``content``.

        Parameters:
            content: The received JSON message. It must be an object with a
                string ``type`` field. Non-object messages and messages with an
                unknown or missing ``type`` close the socket with code 4400.
        """
        if not isinstance(content, Mapping):
            await self.close(code=4400)
            return
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

    async def _handle_connection_init(self, content: Mapping[str, object]) -> None:
        """
        Handle a client's "connection_init" message and send a protocol acknowledgment.

        If the connection has already been acknowledged, closes the WebSocket
        with code 4429 and sends no additional acknowledgement.
        If the incoming message contains a "payload" that is a dict, stores it on
        self.connection_params; omitted, null, and non-object payloads clear
        connection_params. Marks the connection as acknowledged before sending
        exactly ``{"type": "connection_ack"}``; unsuppressed send failures
        therefore propagate after the connection has been marked acknowledged.

        Parameters:
            content: The received WebSocket message for ``connection_init``.
        """
        if self.connection_acknowledged:
            await self.close(code=4429)
            return
        payload = content.get("payload")
        if isinstance(payload, dict):
            self.connection_params = cast(dict[str, object], payload)
        else:
            self.connection_params = {}
        self.connection_acknowledged = True
        await self._send_protocol_message({"type": "connection_ack"})

    async def _handle_ping(self, content: Mapping[str, object]) -> None:
        """
        Responds to an incoming ping by sending a pong protocol message.

        If the incoming `content` contains a non-null `"payload"` key, its value
        is included in the sent pong message under the same key. Missing and
        null payloads produce ``{"type": "pong"}``.

        Parameters:
            content: The received message object; may include an optional
                ``payload`` to echo.
        """
        payload = content.get("payload")
        response: dict[str, object] = {"type": "pong"}
        if payload is not None:
            response["payload"] = payload
        await self._send_protocol_message(response)

    async def _handle_subscribe(self, content: Mapping[str, object]) -> None:
        """
        Handle an incoming GraphQL "subscribe" protocol message and initiate or deliver the corresponding subscription results.

        The connection acknowledgement check runs before id or payload
        validation; any ``subscribe`` received before acknowledgement closes the
        socket with code 4401 and sends no protocol envelope. After
        acknowledgement, invalid operation ids or non-object payloads close the
        socket with code 4403 and also send no protocol envelope. An operation
        id is valid when it is a string; empty strings are accepted, while
        missing, ``null``, and non-string ids are invalid. The payload must be
        an object containing string ``query``, optional object-or-null
        ``variables``, and optional string-or-null ``operationName``. Empty
        query strings are treated as provided strings and are passed to GraphQL
        parsing. Null ``variables`` and ``operationName`` are treated as
        omitted. Extra payload or message fields are ignored.

        Missing schema subscription support sends
        ``[{"message": "GraphQL subscriptions are not configured."}]``. Missing
        query strings send
        ``[{"message": "A GraphQL query string is required."}]``. Non-object
        variables send ``[{"message": "Variables must be provided as an object."}]``.
        Invalid operation names send
        ``[{"message": "The operation name must be a string when provided."}]``.
        GraphQL parse errors and ``GraphQLError`` setup errors send each
        ``GraphQLError.formatted`` mapping in an ``error`` envelope for the
        operation id followed by ``complete`` without registering a subscription
        task. Error ``extensions`` remain inside those formatted GraphQL error
        dictionaries when GraphQL provides them. Recoverable setup exceptions
        use ``[{"message": str(error)}]`` and also complete without a task.
        Recoverable setup exceptions are the classes in
        ``RECOVERABLE_SUBSCRIPTION_ERRORS``. A single
        ``ExecutionResult`` response sends one ``next`` envelope followed by
        ``complete``. Unexpected setup results that are neither an
        ``ExecutionResult`` nor an async iterator sends
        ``[{"message": "GraphQL subscription did not return an async iterator."}]``
        followed by ``complete`` and does not register a task.

        Async subscription iterators replace any active subscription with the
        same id only after ``subscribe()`` returns an iterator. Replacement
        cancellation suppresses ``CancelledError`` from the old task, but any
        other old-task exception propagates and prevents the new stream task from
        being registered; the consumer does not send an additional protocol
        response for the replacement operation in that case.

        Parameters:
            content: The incoming protocol message. Expected keys:
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
        operation_payload = cast(dict[str, object], payload)

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

        query = operation_payload.get("query")
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

        variables = operation_payload.get("variables")
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

        variable_values = cast(dict[str, object] | None, variables)

        operation_name = operation_payload.get("operationName")
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
            with ensure_calculation_run_context():
                subscription = await subscribe(
                    schema.graphql_schema,
                    document,
                    variable_values=variable_values,
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

        if not isinstance(subscription, AsyncIterator):
            await self._send_protocol_message(
                {
                    "type": "error",
                    "id": operation_id,
                    "payload": [
                        {
                            "message": "GraphQL subscription did not return an async iterator."
                        }
                    ],
                }
            )
            await self._send_protocol_message({"type": "complete", "id": operation_id})
            return

        if operation_id in self.active_subscriptions:
            await self._stop_subscription(operation_id)

        self.active_subscriptions[operation_id] = asyncio.create_task(
            self._stream_subscription(
                operation_id,
                subscription,
            )
        )

    async def _handle_complete(self, content: Mapping[str, object]) -> None:
        """
        Handle an incoming "complete" protocol message by stopping the subscription for the specified operation.

        If the message payload contains an "id" field that is a string, the
        corresponding active subscription task is cancelled and cleaned up.
        Missing, non-string, and unknown ids are ignored.

        Parameters:
                content: The received protocol message payload. Expected to
                    contain an ``id`` key with the operation identifier.
        """
        operation_id = content.get("id")
        if isinstance(operation_id, str):
            await self._stop_subscription(operation_id)

    async def _stream_subscription(
        self, operation_id: str, async_iterator: AsyncIterator[ExecutionResult]
    ) -> None:
        """
        Stream execution results from an async iterator to the client for a subscription operation.

        Sends each yielded execution result for the given operation_id to the
        client. Recoverable iteration errors are the classes listed in
        ``RECOVERABLE_SUBSCRIPTION_ERRORS``; they send
        ``{"type": "error", "id": operation_id, "payload": [{"message": str(error)}]}``.
        Finite iterators and recoverable iteration errors follow the cleanup
        order described below.

        Finite iterators, recoverable iteration errors, cancellation,
        non-recoverable iteration errors, and ``aclose`` errors all run the same
        cleanup path: ``aclose`` is attempted, then ``complete`` is attempted,
        then the operation id is removed from ``active_subscriptions``.
        ``asyncio.CancelledError`` propagates after that cleanup unless replaced
        by a later cleanup error. Non-recoverable iteration errors propagate after cleanup and the
        ``complete`` attempt. If ``aclose`` also raises, the close exception
        replaces the original cancellation or iteration exception. If sending
        ``complete`` raises an exception other than the ``RuntimeError`` suppressed by
        ``_send_protocol_message()``, that send exception replaces any active
        cancellation, iteration, or close exception. The operation id is removed from
        ``active_subscriptions`` in all cases after the ``complete`` attempt.

        Parameters:
            operation_id (str): The subscription operation identifier used in protocol messages.
            async_iterator: An asynchronous iterator that yields execution
                result objects to be sent to the client.

        Raises:
            asyncio.CancelledError: Propagated when the surrounding subscription task is cancelled.
            Exception: Non-recoverable iteration errors, ``aclose`` errors, and
                unsuppressed ``complete`` send errors propagate according to the
                precedence described above.
        """
        iterator = async_iterator.__aiter__()
        try:
            while True:
                with ensure_calculation_run_context():
                    result = await iterator.__anext__()
                    await self._send_execution_result(operation_id, result)
        except StopAsyncIteration:
            pass
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
            try:
                await self._close_iterator(async_iterator)
            finally:
                try:
                    await self._send_protocol_message(
                        {"type": "complete", "id": operation_id}
                    )
                finally:
                    self.active_subscriptions.pop(operation_id, None)

    async def _stop_subscription(self, operation_id: str) -> None:
        """
        Cancel and await the active subscription task for the given operation id, if one exists.

        If a task is found for operation_id it is cancelled and awaited;
        CancelledError raised during awaiting is suppressed. Other exceptions
        from the task propagate. If no task exists this is a no-op.
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

        The message payload includes a "data" field when result.data is present
        and an "errors" list when result.errors is non-empty; errors are
        converted to serializable dictionaries. ``ExecutionResult.extensions``
        is not forwarded. If neither data nor errors are present, the payload is
        an empty object.

        Parameters:
            operation_id (str): The operation identifier to include as the message `id`.
            result (ExecutionResult): The GraphQL execution result to serialize and send.
        """
        payload: dict[str, object] = {}
        if result.data is not None:
            payload["data"] = result.data
        if result.errors:
            payload["errors"] = [self._format_error(error) for error in result.errors]
        await self._send_protocol_message(
            {"type": "next", "id": operation_id, "payload": payload}
        )

    async def _send_protocol_message(self, message: Mapping[str, object]) -> None:
        """
        Send a JSON-serializable GraphQL transport protocol message over the WebSocket.

        Parameters:
            message: The protocol message to send. If the connection is
                already closed and Channels raises ``RuntimeError``, the message
                is discarded silently. Other ``send_json`` exceptions propagate
                unchanged.
        """
        try:
            await self.send_json(message)
        except RuntimeError:
            # The connection has already been closed. There is nothing else to send.
            pass

    def _build_context(self) -> GraphQLSubscriptionContext:
        """
        Builds a request context object for GraphQL execution containing the current user, decoded headers, scope, and connection parameters.

        Returns:
            context: A ``GraphQLSubscriptionContext`` with attributes:
                - `user`: the value of `scope["user"]` (may be None).
                - `headers`: a dict mapping header names to decoded string values.
                - `scope`: the consumer's `scope`.
                - `connection_params`: the connection parameters provided during `connection_init`.
        """
        user = self.scope.get("user")
        headers: dict[str, str] = {}
        raw_headers = self.scope.get("headers")
        if isinstance(raw_headers, (list, tuple)):
            for header in raw_headers:
                if not isinstance(header, (list, tuple)) or len(header) != 2:
                    continue
                key, value = header
                headers[self._decode_header_part(key)] = self._decode_header_part(value)
        return GraphQLSubscriptionContext(
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
    def _decode_header_part(value: object) -> str:
        """Decode one ASGI header key or value into text."""
        if isinstance(value, (bytes, bytearray)):
            return value.decode("latin1")
        if isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _format_error(error: Exception) -> dict[str, object]:
        """
        Format an exception as a GraphQL-compatible error dictionary.

        Parameters:
            error (Exception): The exception to format; if a `GraphQLError`, its `.formatted` representation is used.

        Returns:
            dict[str, object]: The error payload: the
                `GraphQLError.formatted` mapping for GraphQLError instances,
                otherwise `{"message": str(error)}`.
        """
        if isinstance(error, GraphQLError):
            return cast(dict[str, object], error.formatted)
        return {"message": str(error)}

    @staticmethod
    async def _close_iterator(async_iterator: object) -> None:
        """
        Close an asynchronous iterator by awaiting its `aclose` coroutine if present.

        Parameters:
            async_iterator: The iterator to close; if it defines an `aclose`
                callable, that callable is invoked and awaited. Objects without
                a callable ``aclose`` are ignored.

        Raises:
            Exception: Exceptions raised by ``aclose`` propagate unchanged. If
            ``aclose`` is callable but returns a non-awaitable value, awaiting it
            raises the normal Python ``TypeError``.
        """
        if not _supports_async_close(async_iterator):
            return
        await async_iterator.aclose()
