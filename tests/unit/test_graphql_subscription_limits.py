"""Resource limits apply before GraphQL subscription setup acquires resources."""

import asyncio
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from graphql import GraphQLField, GraphQLObjectType, GraphQLSchema, GraphQLString

from general_manager.api.graphql_subscription_consumer import (
    GraphQLSubscriptionConsumer,
)


class SubscriptionLimitTests(TestCase):
    def test_active_streams_are_bounded_and_completion_releases_capacity(self):
        async def scenario():
            closed = []

            async def stream(*_args):
                try:
                    yield {"event": "ready"}
                    await asyncio.Event().wait()
                finally:
                    closed.append(True)

            consumer = self.consumer()
            schema = self.schema(stream)
            with (
                patch.object(
                    GraphQLSubscriptionConsumer, "max_subscriptions", 1, create=True
                ),
                patch(
                    "general_manager.api.graphql_subscription_consumer.GraphQL.get_schema",
                    return_value=schema,
                ),
            ):
                await consumer._handle_subscribe(self.message("first"))
                await asyncio.sleep(0)
                await consumer._handle_subscribe(self.message("second"))
                await consumer._handle_subscribe(self.message("first"))
                self.assertEqual(closed, [])
                consumer.close.assert_awaited_once_with(code=4409)
                self.assertEqual(set(consumer.active_subscriptions), {"first"})
                self.assertTrue(
                    any(
                        call.args[0]["type"] == "error"
                        for call in consumer.send_json.call_args_list
                    )
                )
                await consumer._handle_complete({"id": "first"})
                self.assertEqual(closed, [True])
                await consumer._handle_subscribe(self.message("third"))
                self.assertEqual(set(consumer.active_subscriptions), {"third"})
                await consumer.disconnect(1000)
                self.assertEqual(consumer.active_subscriptions, {})

        asyncio.run(scenario())

    def test_pending_setup_counts_toward_limit_and_disconnect_cancels_it(self):
        async def scenario():
            entered = asyncio.Event()
            cancelled = asyncio.Event()

            async def setup(*_args):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            consumer = self.consumer()
            with (
                patch.object(
                    GraphQLSubscriptionConsumer, "max_subscriptions", 1, create=True
                ),
                patch(
                    "general_manager.api.graphql_subscription_consumer.GraphQL.get_schema",
                    return_value=self.schema(setup),
                ),
            ):
                pending = asyncio.create_task(
                    consumer._handle_subscribe(self.message("first"))
                )
                await entered.wait()
                await asyncio.wait_for(
                    consumer._handle_subscribe(self.message("second")), 0.1
                )
                await consumer.disconnect(1000)
                with self.assertRaises(asyncio.CancelledError):
                    await pending
                self.assertTrue(cancelled.is_set())
                self.assertEqual(consumer.active_subscriptions, {})

        asyncio.run(scenario())

    def test_duplicate_pending_id_does_not_start_another_setup(self):
        async def scenario():
            entered = asyncio.Event()
            calls = []

            async def setup(*_args):
                calls.append(True)
                entered.set()
                await asyncio.Event().wait()

            consumer = self.consumer()
            with patch(
                "general_manager.api.graphql_subscription_consumer.GraphQL.get_schema",
                return_value=self.schema(setup),
            ):
                pending = asyncio.create_task(
                    consumer._handle_subscribe(self.message("first"))
                )
                await entered.wait()
                await consumer._handle_subscribe(self.message("first"))
                consumer.close.assert_awaited_once_with(code=4409)
                self.assertEqual(calls, [True])
                await consumer.disconnect(1000)
                with self.assertRaises(asyncio.CancelledError):
                    await pending

        asyncio.run(scenario())

    def test_failed_setup_releases_capacity(self):
        async def scenario():
            calls = []

            async def setup(*_args):
                calls.append(True)
                raise ValueError("unavailable")

            consumer = self.consumer()
            with (
                patch.object(GraphQLSubscriptionConsumer, "max_subscriptions", 1),
                patch(
                    "general_manager.api.graphql_subscription_consumer.GraphQL.get_schema",
                    return_value=self.schema(setup),
                ),
            ):
                await consumer._handle_subscribe(self.message("first"))
                await consumer._handle_subscribe(self.message("second"))
                self.assertEqual(calls, [True, True])
                self.assertEqual(consumer.active_subscriptions, {})

        asyncio.run(scenario())

    def test_setup_timeout_cancels_work_and_allows_retry(self):
        async def scenario():
            cancelled = []

            async def setup(*_args):
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.append(True)

            consumer = self.consumer()
            with (
                patch.object(
                    GraphQLSubscriptionConsumer,
                    "subscription_setup_timeout",
                    0.01,
                    create=True,
                ),
                patch(
                    "general_manager.api.graphql_subscription_consumer.GraphQL.get_schema",
                    return_value=self.schema(setup),
                ),
            ):
                await asyncio.wait_for(
                    consumer._handle_subscribe(self.message("first")), 0.2
                )
                await asyncio.wait_for(
                    consumer._handle_subscribe(self.message("first")), 0.2
                )
                self.assertEqual(cancelled, [True, True])
                self.assertEqual(consumer.active_subscriptions, {})
                self.assertEqual(
                    sum(
                        call.args[0]["type"] == "error"
                        for call in consumer.send_json.call_args_list
                    ),
                    2,
                )

        asyncio.run(scenario())

    def test_timeout_during_replacement_closes_unregistered_iterator(self):
        async def scenario():
            class Source:
                closed = False

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    await asyncio.Event().wait()

                async def aclose(self):
                    self.closed = True

            source = Source()

            async def setup(*_args):
                return source

            async def old_stream():
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.Event().wait()

            consumer = self.consumer()
            old = asyncio.create_task(old_stream())
            consumer.active_subscriptions["first"] = old
            await asyncio.sleep(0)
            with (
                patch.object(
                    GraphQLSubscriptionConsumer, "subscription_setup_timeout", 0.01
                ),
                patch(
                    "general_manager.api.graphql_subscription_consumer.GraphQL.get_schema",
                    return_value=self.schema(setup),
                ),
            ):
                await asyncio.wait_for(
                    consumer._handle_subscribe(self.message("first")), 0.2
                )
                self.assertTrue(source.closed)
                self.assertEqual(consumer.active_subscriptions, {})
                self.assertTrue(old.cancelled())

        asyncio.run(scenario())

    @staticmethod
    def consumer():
        consumer = GraphQLSubscriptionConsumer()
        consumer.connection_acknowledged = True
        consumer.connection_params = {}
        consumer.active_subscriptions = {}
        consumer.scope = {}
        consumer.send_json = AsyncMock()
        consumer.close = AsyncMock()
        return consumer

    @staticmethod
    def schema(subscribe):
        field = GraphQLField(GraphQLString, subscribe=subscribe)
        return SimpleNamespace(
            graphql_schema=GraphQLSchema(
                query=GraphQLObjectType(
                    "Query", {"value": GraphQLField(GraphQLString)}
                ),
                subscription=GraphQLObjectType("Subscription", {"event": field}),
            )
        )

    @staticmethod
    def message(identifier):
        return {"id": identifier, "payload": {"query": "subscription { event }"}}
