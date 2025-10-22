# type: ignore[file-ignores]
"""Unit tests for GraphQL subscription consumer functionality."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import unittest

from graphql import GraphQLError, GraphQLSchema, parse
from graphql.type import GraphQLObjectType, GraphQLField, GraphQLString

from general_manager.api.graphql_subscription_consumer import (
    GraphQLSubscriptionConsumer,
)


class GraphQLSubscriptionConsumerConnectTests(unittest.TestCase):
    """Test WebSocket connection establishment."""

    def test_connect_initializes_state(self) -> None:
        """Verify connect initializes connection state and accepts WebSocket."""
        consumer = GraphQLSubscriptionConsumer()
        consumer.scope = {"subprotocols": []}

        async def test_connect() -> None:
            with patch.object(
                consumer, "accept", new_callable=AsyncMock
            ) as mock_accept:
                await consumer.connect()
                mock_accept.assert_called_once_with(subprotocol=None)
                self.assertFalse(consumer.connection_acknowledged)
                self.assertEqual(consumer.connection_params, {})
                self.assertEqual(consumer.active_subscriptions, {})

        asyncio.run(test_connect())

    def test_connect_selects_graphql_transport_ws_subprotocol(self) -> None:
        """Verify connect selects graphql-transport-ws when offered."""
        consumer = GraphQLSubscriptionConsumer()
        consumer.scope = {"subprotocols": ["graphql-transport-ws", "other"]}

        async def test_connect() -> None:
            with patch.object(
                consumer, "accept", new_callable=AsyncMock
            ) as mock_accept:
                await consumer.connect()
                mock_accept.assert_called_once_with(subprotocol="graphql-transport-ws")

        asyncio.run(test_connect())


class GraphQLSubscriptionConsumerDisconnectTests(unittest.TestCase):
    """Test WebSocket disconnection cleanup."""

    def test_disconnect_cancels_active_subscriptions(self) -> None:
        """Verify disconnect cancels and awaits all active subscription tasks."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_disconnect() -> None:
            # Create real futures to mimic tasks
            loop = asyncio.get_running_loop()
            task1 = loop.create_future()
            task2 = loop.create_future()
            consumer.active_subscriptions = {"sub1": task1, "sub2": task2}

            await consumer.disconnect(1000)

            self.assertTrue(task1.cancelled())
            self.assertTrue(task2.cancelled())
            self.assertEqual(consumer.active_subscriptions, {})

        asyncio.run(test_disconnect())


class GraphQLSubscriptionConsumerReceiveJsonTests(unittest.TestCase):
    """Test JSON message dispatching."""

    def test_receive_json_handles_connection_init(self) -> None:
        """Verify receive_json dispatches connection_init messages."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_receive() -> None:
            with patch.object(
                consumer, "_handle_connection_init", new_callable=AsyncMock
            ) as mock_handle:
                await consumer.receive_json({"type": "connection_init"})
                mock_handle.assert_called_once_with({"type": "connection_init"})

        asyncio.run(test_receive())

    def test_receive_json_handles_ping(self) -> None:
        """Verify receive_json dispatches ping messages."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_receive() -> None:
            with patch.object(
                consumer, "_handle_ping", new_callable=AsyncMock
            ) as mock_handle:
                await consumer.receive_json({"type": "ping"})
                mock_handle.assert_called_once_with({"type": "ping"})

        asyncio.run(test_receive())

    def test_receive_json_handles_subscribe(self) -> None:
        """Verify receive_json dispatches subscribe messages."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_receive() -> None:
            with patch.object(
                consumer, "_handle_subscribe", new_callable=AsyncMock
            ) as mock_handle:
                await consumer.receive_json({"type": "subscribe"})
                mock_handle.assert_called_once_with({"type": "subscribe"})

        asyncio.run(test_receive())

    def test_receive_json_handles_complete(self) -> None:
        """Verify receive_json dispatches complete messages."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_receive() -> None:
            with patch.object(
                consumer, "_handle_complete", new_callable=AsyncMock
            ) as mock_handle:
                await consumer.receive_json({"type": "complete"})
                mock_handle.assert_called_once_with({"type": "complete"})

        asyncio.run(test_receive())

    def test_receive_json_closes_on_unknown_type(self) -> None:
        """Verify receive_json closes connection for unknown message types."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_receive() -> None:
            with patch.object(consumer, "close", new_callable=AsyncMock) as mock_close:
                await consumer.receive_json({"type": "unknown"})
                mock_close.assert_called_once_with(code=4400)

        asyncio.run(test_receive())

    def test_receive_json_closes_on_missing_type(self) -> None:
        """Verify receive_json closes connection when type is missing."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_receive() -> None:
            with patch.object(consumer, "close", new_callable=AsyncMock) as mock_close:
                await consumer.receive_json({"data": "no type"})
                mock_close.assert_called_once_with(code=4400)

        asyncio.run(test_receive())


class GraphQLSubscriptionConsumerConnectionInitTests(unittest.TestCase):
    """Test connection_init message handling."""

    def test_connection_init_acknowledges_connection(self) -> None:
        """Verify _handle_connection_init sends acknowledgment."""
        consumer = GraphQLSubscriptionConsumer()
        consumer.connection_acknowledged = False

        async def test_init() -> None:
            with patch.object(
                consumer, "_send_protocol_message", new_callable=AsyncMock
            ) as mock_send:
                await consumer._handle_connection_init({})
                mock_send.assert_called_once_with({"type": "connection_ack"})
                self.assertTrue(consumer.connection_acknowledged)

        asyncio.run(test_init())

    def test_connection_init_stores_payload(self) -> None:
        """Verify _handle_connection_init stores connection parameters."""
        consumer = GraphQLSubscriptionConsumer()
        consumer.connection_acknowledged = False

        async def test_init() -> None:
            with patch.object(
                consumer, "_send_protocol_message", new_callable=AsyncMock
            ):
                params = {"auth": "token"}
                await consumer._handle_connection_init({"payload": params})
                self.assertEqual(consumer.connection_params, params)

        asyncio.run(test_init())

    def test_connection_init_clears_non_dict_payload(self) -> None:
        """Verify _handle_connection_init clears params for non-dict payload."""
        consumer = GraphQLSubscriptionConsumer()
        consumer.connection_acknowledged = False

        async def test_init() -> None:
            with patch.object(
                consumer, "_send_protocol_message", new_callable=AsyncMock
            ):
                await consumer._handle_connection_init({"payload": "not a dict"})
                self.assertEqual(consumer.connection_params, {})

        asyncio.run(test_init())

    def test_connection_init_closes_if_already_acknowledged(self) -> None:
        """Verify _handle_connection_init closes connection if already acknowledged."""
        consumer = GraphQLSubscriptionConsumer()
        consumer.connection_acknowledged = True

        async def test_init() -> None:
            with patch.object(consumer, "close", new_callable=AsyncMock) as mock_close:
                await consumer._handle_connection_init({})
                mock_close.assert_called_once_with(code=4429)

        asyncio.run(test_init())


class GraphQLSubscriptionConsumerPingTests(unittest.TestCase):
    """Test ping message handling."""

    def test_ping_sends_pong(self) -> None:
        """Verify _handle_ping responds with pong message."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_ping() -> None:
            with patch.object(
                consumer, "_send_protocol_message", new_callable=AsyncMock
            ) as mock_send:
                await consumer._handle_ping({})
                mock_send.assert_called_once_with({"type": "pong"})

        asyncio.run(test_ping())

    def test_ping_echoes_payload(self) -> None:
        """Verify _handle_ping includes payload in pong response."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_ping() -> None:
            with patch.object(
                consumer, "_send_protocol_message", new_callable=AsyncMock
            ) as mock_send:
                payload = {"timestamp": 123}
                await consumer._handle_ping({"payload": payload})
                mock_send.assert_called_once_with({"type": "pong", "payload": payload})

        asyncio.run(test_ping())


class GraphQLSubscriptionConsumerBuildContextTests(unittest.TestCase):
    """Test context building for GraphQL execution."""

    def test_build_context_includes_user(self) -> None:
        """Verify _build_context includes user from scope."""
        consumer = GraphQLSubscriptionConsumer()
        mock_user = object()
        consumer.scope = {"user": mock_user}
        consumer.connection_params = {}

        context = consumer._build_context()
        self.assertIs(context.user, mock_user)

    def test_build_context_includes_connection_params(self) -> None:
        """Verify _build_context includes connection parameters."""
        consumer = GraphQLSubscriptionConsumer()
        params = {"auth": "token"}
        consumer.scope = {}
        consumer.connection_params = params

        context = consumer._build_context()
        self.assertEqual(context.connection_params, params)

    def test_build_context_decodes_headers(self) -> None:
        """Verify _build_context decodes byte headers to strings."""
        consumer = GraphQLSubscriptionConsumer()
        consumer.scope = {
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer token"),
            ]
        }
        consumer.connection_params = {}

        context = consumer._build_context()
        self.assertEqual(context.headers["content-type"], "application/json")
        self.assertEqual(context.headers["authorization"], "Bearer token")

    def test_build_context_handles_string_headers(self) -> None:
        """Verify _build_context handles already-decoded string headers."""
        consumer = GraphQLSubscriptionConsumer()
        consumer.scope = {
            "headers": [
                ("content-type", "application/json"),
            ]
        }
        consumer.connection_params = {}

        context = consumer._build_context()
        self.assertEqual(context.headers["content-type"], "application/json")


class GraphQLSubscriptionConsumerSchemaCheckTests(unittest.TestCase):
    """Test schema validation helper."""

    def test_schema_has_no_subscription_when_none(self) -> None:
        """Verify _schema_has_no_subscription returns True when subscription_type is None."""
        schema = MagicMock(spec=GraphQLSchema)
        schema.subscription_type = None

        result = GraphQLSubscriptionConsumer._schema_has_no_subscription(schema)
        self.assertTrue(result)

    def test_schema_has_no_subscription_when_present(self) -> None:
        """Verify _schema_has_no_subscription returns False when subscription_type exists."""
        schema = MagicMock(spec=GraphQLSchema)
        schema.subscription_type = MagicMock()

        result = GraphQLSubscriptionConsumer._schema_has_no_subscription(schema)
        self.assertFalse(result)


class GraphQLSubscriptionConsumerFormatErrorTests(unittest.TestCase):
    """Test error formatting."""

    def test_format_error_handles_graphql_error(self) -> None:
        """Verify _format_error returns formatted dict for GraphQLError."""
        error = GraphQLError("Test error")
        formatted = GraphQLSubscriptionConsumer._format_error(error)

        self.assertIsInstance(formatted, dict)
        self.assertIn("message", formatted)
        self.assertEqual(formatted["message"], "Test error")

    def test_format_error_handles_generic_exception(self) -> None:
        """Verify _format_error converts generic exceptions to dict with message."""
        error = ValueError("Invalid value")
        formatted = GraphQLSubscriptionConsumer._format_error(error)

        self.assertEqual(formatted, {"message": "Invalid value"})


class GraphQLSubscriptionConsumerCloseIteratorTests(unittest.TestCase):
    """Test async iterator cleanup."""

    def test_close_iterator_calls_aclose(self) -> None:
        """Verify _close_iterator calls aclose on async iterators."""
        mock_iterator = MagicMock()
        mock_aclose = AsyncMock()
        mock_iterator.aclose = mock_aclose

        async def test_close() -> None:
            await GraphQLSubscriptionConsumer._close_iterator(mock_iterator)
            mock_aclose.assert_called_once()

        asyncio.run(test_close())

    def test_close_iterator_handles_no_aclose(self) -> None:
        """Verify _close_iterator handles iterators without aclose gracefully."""
        mock_iterator = object()

        async def test_close() -> None:
            # Should not raise
            await GraphQLSubscriptionConsumer._close_iterator(mock_iterator)

        asyncio.run(test_close())


class GraphQLSubscriptionConsumerStopSubscriptionTests(unittest.TestCase):
    """Test subscription cancellation."""

    def test_stop_subscription_cancels_and_removes_task(self) -> None:
        """Verify _stop_subscription cancels the task and removes it from active_subscriptions."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_stop() -> None:
            loop = asyncio.get_running_loop()
            mock_task = loop.create_future()
            consumer.active_subscriptions = {"sub1": mock_task}

            await consumer._stop_subscription("sub1")

            self.assertTrue(mock_task.cancelled())
            self.assertNotIn("sub1", consumer.active_subscriptions)

        asyncio.run(test_stop())

    def test_stop_subscription_handles_missing_id(self) -> None:
        """Verify _stop_subscription handles non-existent operation IDs gracefully."""
        consumer = GraphQLSubscriptionConsumer()
        consumer.active_subscriptions = {}

        async def test_stop() -> None:
            # Should not raise
            await consumer._stop_subscription("nonexistent")

        asyncio.run(test_stop())


class GraphQLSubscriptionConsumerSendProtocolMessageTests(unittest.TestCase):
    """Test protocol message sending."""

    def test_send_protocol_message_sends_json(self) -> None:
        """Verify _send_protocol_message sends message as JSON."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_send() -> None:
            """
            Verifies that _send_protocol_message delegates sending the given protocol message to the consumer's send_json method.

            Asserts that send_json is called exactly once with the provided message.
            """
            with patch.object(
                consumer, "send_json", new_callable=AsyncMock
            ) as mock_send:
                message = {"type": "test", "data": "value"}
                await consumer._send_protocol_message(message)
                mock_send.assert_called_once_with(message)

        asyncio.run(test_send())

    def test_send_protocol_message_handles_closed_connection(self) -> None:
        """Verify _send_protocol_message handles RuntimeError from closed connection."""
        consumer = GraphQLSubscriptionConsumer()

        async def test_send() -> None:
            with patch.object(
                consumer, "send_json", new_callable=AsyncMock
            ) as mock_send:
                mock_send.side_effect = RuntimeError("Connection closed")
                # Should not raise
                await consumer._send_protocol_message({"type": "test"})

        asyncio.run(test_send())
