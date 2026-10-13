"""Request-bound authorization for generated detail subscription lifecycles."""

import asyncio
import os
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, patch

import graphene
from channels.layers import InMemoryChannelLayer
from django.contrib.auth import get_user_model
from django.db.models import IntegerField
from graphql import ExecutionResult

from general_manager.api.graphql import GraphQL
from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.permission import AdditiveManagerPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestDetailSubscriptionAuthorization(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        state = {"allowed": True, "field_allowed": True, "reads": [], "checks": []}

        class Account(GeneralManager):
            class Interface(DatabaseInterface):
                owner_id = IntegerField()

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

        class ActorSummary(GeneralManager):
            class Interface(CalculationInterface):
                actor = Input(Account)
                selections = Input(list[int], required=False)
                members = Input(list[Account], required=False)

            class Permission(AdditiveManagerPermission):
                __read__: ClassVar[list[str]] = ["public"]

                def can_read_instance(self):
                    state["checks"].append(self.request_user.pk)
                    # This intentionally performs ORM work: subscription permission
                    # evaluation must stay outside the async event-loop thread.
                    exists = (
                        get_user_model()
                        .objects.filter(pk=self.request_user.pk)
                        .exists()
                    )
                    return (
                        exists
                        and state["allowed"]
                        and self.instance.actor.owner_id == self.request_user.pk
                    )

                def check_permission(self, action, attribute=None):
                    if action == "read" and attribute == "overview":
                        return (
                            get_user_model()
                            .objects.filter(pk=self.request_user.pk)
                            .exists()
                            and state["field_allowed"]
                        )
                    return super().check_permission(action, attribute)

            @graph_ql_property
            def overview(self) -> int:
                state["reads"].append(self.actor.id)
                return get_user_model().objects.filter(pk=self.actor.owner_id).count()

            @graph_ql_property
            def member_selection(self) -> str:
                if self.members is None:
                    return "unrestricted"
                return ",".join(str(member.id) for member in self.members)

        cls.Account = Account
        cls.ActorSummary = ActorSummary
        cls.state = state
        cls.general_manager_classes = [Account, ActorSummary]

    def setUp(self):
        super().setUp()
        self.state.update(allowed=True, field_allowed=True, reads=[], checks=[])
        self.user = get_user_model().objects.create_user(username="subscriber")
        self.other_user = get_user_model().objects.create_user(username="other")
        self.account = self.Account.create(
            owner_id=self.user.pk, creator_id=self.user.pk
        )
        self.actor_id = self.account.id
        self.schema = graphene.Schema(
            query=GraphQL._query_class, subscription=GraphQL._subscription_class
        )
        self.layer = InMemoryChannelLayer()
        self.layer_patch = patch.object(
            GraphQL, "_get_channel_layer", return_value=self.layer
        )
        self.layer_patch.start()
        self.addCleanup(self.layer_patch.stop)
        self.environment_patch = patch.dict(os.environ)
        self.environment_patch.start()
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        self.addCleanup(self.environment_patch.stop)

    async def subscribe(
        self, *, user=None, fields="action item { overview }", members=None
    ):
        result = await self.schema.subscribe(
            "subscription ($actor: ID!, $members: [ID!]) { "
            "onActorSummaryChange(actorId: $actor, membersId: $members) { "
            + fields
            + " } }",
            variable_values={"actor": self.actor_id, "members": members},
            context_value=SimpleNamespace(user=user or self.user),
        )

        return result

    async def send_update(self):
        # Use the real channel listener and all joined groups; no permission
        # helper or hydration mock can accidentally satisfy these regressions.
        for group in list(self.layer.groups):
            await self.layer.group_send(
                group, {"type": "gm.subscription.event", "action": "update"}
            )

    def test_denied_actor_never_primes_or_registers(self):
        async def run():
            result = await self.subscribe(user=self.other_user)
            if not isinstance(result, ExecutionResult):
                await result.aclose()
            self.assertIsInstance(result, ExecutionResult)
            self.assertTrue(result.errors)
            self.assertTrue(self.state["checks"])

        asyncio.run(run())
        self.assertEqual(self.state["reads"], [])
        self.assertEqual(self.layer.groups, {})

    def test_action_only_selection_still_checks_instance(self):
        async def run():
            result = await self.subscribe(user=self.other_user, fields="action")
            if not isinstance(result, ExecutionResult):
                await result.aclose()
            self.assertIsInstance(result, ExecutionResult)
            self.assertTrue(result.errors)
            self.assertTrue(self.state["checks"])

        asyncio.run(run())
        self.assertEqual(self.layer.groups, {})

    def test_denied_field_is_never_primed(self):
        self.state["field_allowed"] = False

        async def run():
            stream = await self.subscribe()
            try:
                event = await anext(stream)
                self.assertIsNone(event.errors)
                self.assertIsNone(
                    event.data["onActorSummaryChange"]["item"]["overview"]
                )
            finally:
                await stream.aclose()

        asyncio.run(run())
        self.assertEqual(self.state["reads"], [])
        self.assertEqual(self.layer.groups, {})

    def test_allowed_snapshot_and_update_use_request_permission_off_loop(self):
        async def run():
            stream = await self.subscribe()
            try:
                first = await anext(stream)
                await self.send_update()
                second = await asyncio.wait_for(anext(stream), 2)
                for event, action in [(first, "snapshot"), (second, "update")]:
                    self.assertIsNone(event.errors)
                    self.assertEqual(
                        event.data["onActorSummaryChange"],
                        {"action": action, "item": {"overview": 1}},
                    )
            finally:
                await stream.aclose()

        asyncio.run(run())
        self.assertGreaterEqual(len(self.state["checks"]), 2)
        self.assertEqual(set(self.state["checks"]), {self.user.pk})
        self.assertEqual(self.layer.groups, {})

    def test_revocation_before_consumption_prevents_registration_and_snapshot(self):
        async def run():
            stream = await self.subscribe()
            self.state["allowed"] = False
            try:
                with self.assertRaises(StopAsyncIteration):
                    await anext(stream)
            finally:
                await stream.aclose()
            self.assertEqual(self.layer.groups, {})

        asyncio.run(run())

    def test_revocation_after_snapshot_ends_action_only_stream(self):
        async def run():
            stream = await self.subscribe(fields="action")
            try:
                first = await anext(stream)
                self.assertIsNone(first.errors)
                self.state["allowed"] = False
                await self.send_update()
                with self.assertRaises(StopAsyncIteration):
                    await asyncio.wait_for(anext(stream), 2)
            finally:
                await stream.aclose()
            self.assertEqual(self.layer.groups, {})

        asyncio.run(run())

    def test_deleted_actor_ends_stream_without_null_item_event(self):
        async def run():
            stream = await self.subscribe()
            try:
                self.assertIsNone((await anext(stream)).errors)
                await asyncio.to_thread(self.account.delete, creator_id=self.user.pk)
                await self.send_update()
                with self.assertRaises(StopAsyncIteration):
                    await asyncio.wait_for(anext(stream), 2)
            finally:
                await stream.aclose()
            self.assertEqual(self.layer.groups, {})

        asyncio.run(run())

    def test_revocation_during_registration_prevents_snapshot_and_cleans_groups(self):
        original_add = self.layer.group_add

        async def revoke_during_add(group, channel):
            await original_add(group, channel)
            self.state["allowed"] = False

        async def run():
            stream = await self.subscribe()
            with patch.object(self.layer, "group_add", side_effect=revoke_during_add):
                try:
                    with self.assertRaises(StopAsyncIteration):
                        await anext(stream)
                finally:
                    await stream.aclose()
            self.assertEqual(self.layer.groups, {})

        asyncio.run(run())

    def test_denied_before_consumption_does_not_join_even_temporarily(self):
        async def run():
            stream = await self.subscribe()
            self.state["allowed"] = False
            with patch.object(self.layer, "group_add", new_callable=AsyncMock) as join:
                try:
                    with self.assertRaises(StopAsyncIteration):
                        await anext(stream)
                finally:
                    await stream.aclose()
                join.assert_not_awaited()

        asyncio.run(run())

    def test_initial_permission_backend_failure_never_registers_or_primes(self):
        async def run():
            with patch.object(
                self.ActorSummary.Permission,
                "can_read_instance",
                side_effect=RuntimeError("permission backend unavailable"),
            ):
                result = await self.subscribe()
            self.assertIsInstance(result, ExecutionResult)
            self.assertTrue(result.errors)
            self.assertIn("permission backend unavailable", result.errors[0].message)

        asyncio.run(run())
        self.assertEqual(self.state["reads"], [])
        self.assertEqual(self.layer.groups, {})

    def test_followup_permission_backend_failure_cleans_memberships(self):
        async def run():
            stream = await self.subscribe()
            try:
                self.assertIsNone((await anext(stream)).errors)
                with patch.object(
                    self.ActorSummary.Permission,
                    "can_read_instance",
                    side_effect=RuntimeError("permission backend unavailable"),
                ):
                    await self.send_update()
                    with self.assertRaisesRegex(
                        RuntimeError, "permission backend unavailable"
                    ):
                        await asyncio.wait_for(anext(stream), 2)
                self.assertEqual(self.layer.groups, {})
            finally:
                await stream.aclose()

        asyncio.run(run())

    def test_initial_missing_actor_fails_before_registration_or_priming(self):
        self.account.delete(creator_id=self.user.pk)

        async def run():
            result = await self.subscribe()
            self.assertIsInstance(result, ExecutionResult)
            self.assertTrue(result.errors)
            self.assertEqual(
                result.errors[0].message,
                "Subscription target is unavailable or not readable.",
            )

        asyncio.run(run())
        self.assertEqual(self.state["reads"], [])
        self.assertEqual(self.layer.groups, {})

    def test_typed_manager_list_preserves_null_empty_order_and_duplicates(self):
        second = self.Account.create(owner_id=self.user.pk, creator_id=self.user.pk)
        cases = [
            (None, "unrestricted"),
            ([], ""),
            (
                [second.id, self.account.id, second.id],
                f"{second.id},{self.account.id},{second.id}",
            ),
        ]

        async def run(members, expected):
            stream = await self.subscribe(
                members=members, fields="action item { memberSelection }"
            )
            try:
                first = await anext(stream)
                if members:
                    self.assertIn(
                        GraphQL._group_name(self.Account, second.identification),
                        self.layer.groups,
                    )
                await self.send_update()
                update = await asyncio.wait_for(anext(stream), 2)
                for event, action in [(first, "snapshot"), (update, "update")]:
                    self.assertIsNone(event.errors)
                    self.assertEqual(
                        event.data["onActorSummaryChange"],
                        {"action": action, "item": {"memberSelection": expected}},
                    )
            finally:
                await stream.aclose()
            self.assertEqual(self.layer.groups, {})

        for members, expected in cases:
            with self.subTest(members=members):
                asyncio.run(run(members, expected))
