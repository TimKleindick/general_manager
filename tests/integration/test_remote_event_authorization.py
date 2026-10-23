from __future__ import annotations

import asyncio
import json
from typing import ClassVar
from unittest.mock import patch

from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.layers import InMemoryChannelLayer
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.db.models import CharField, ForeignKey, CASCADE
from django.test.utils import CaptureQueriesContext

from general_manager.api.remote_api import get_remote_api_config
from general_manager.api.remote_invalidation import (
    RemoteInvalidationConsumer,
    remote_invalidation_group_name,
    _can_receive_remote_event,
    _fresh_remote_principal,
)
from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class RemoteEventAuthorizationTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class SecuredProject(GeneralManager):
            class Interface(DatabaseInterface):
                status = CharField(max_length=30)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = [
                    "isAuthenticated&matches:status:visible"
                ]

                def can_read_instance(self):
                    return self.request_user.is_staff and super().can_read_instance()

            class RemoteAPI:
                enabled = True
                base_path = "/internal/gm"
                resource_name = "secured-projects"
                allow_filter = True
                allow_update = True
                websocket_invalidation = True

        class DelegatedProject(GeneralManager):
            class Interface(DatabaseInterface):
                parent = ForeignKey("general_manager.SecuredProject", on_delete=CASCADE)

            class Permission(ManagerBasedPermission):
                __based_on__: ClassVar[str] = "parent"
                __read__: ClassVar[list[str]] = ["public"]

            class RemoteAPI:
                enabled = True
                base_path = "/internal/gm"
                resource_name = "delegated-projects"
                allow_filter = True
                allow_update = True
                websocket_invalidation = True

        cls.Child = DelegatedProject
        cls.Project = SecuredProject
        cls.general_manager_classes = [SecuredProject, DelegatedProject]
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="reader", is_staff=True
        )
        self.visible = self.Project.create(ignore_permission=True, status="visible")
        self.hidden = self.Project.create(ignore_permission=True, status="hidden")
        self.config = get_remote_api_config(self.Project)
        self.layer = InMemoryChannelLayer()

    def peer(self, user):
        return ApplicationCommunicator(
            RemoteInvalidationConsumer.as_asgi(),
            {
                "type": "websocket",
                "user": user,
                "query_string": b"",
                "url_route": {
                    "kwargs": {
                        "base_path": "internal/gm",
                        "resource_name": "secured-projects",
                    }
                },
            },
        )

    async def publish(self, identification, action="update"):
        await self.layer.group_send(
            remote_invalidation_group_name(self.config),
            {
                "type": "gm.remote.invalidation",
                "protocol_version": "v1",
                "base_path": "/internal/gm",
                "resource_name": "secured-projects",
                "action": action,
                "identification": identification,
                "event_id": "event",
            },
        )

    def test_anonymous_rejected_before_group_registration(self):
        async def run():
            peer = self.peer(AnonymousUser())
            await peer.send_input({"type": "websocket.connect"})
            assert (await peer.receive_output())["type"] == "websocket.close"
            await peer.wait()
            assert not self.layer.groups

        with patch(
            "general_manager.api.remote_invalidation._get_channel_layer_safe",
            return_value=self.layer,
        ):
            asyncio.run(run())

    def test_current_custom_permission_and_revocation(self):
        async def run():
            peer = self.peer(self.user)
            await peer.send_input({"type": "websocket.connect"})
            assert (await peer.receive_output())["type"] == "websocket.accept"
            await self.publish({"id": self.hidden.id})
            assert await peer.receive_nothing(timeout=0.1)
            await self.publish({"id": self.visible.id})
            payload = json.loads((await peer.receive_output())["text"])
            assert payload["identification"] == {"id": self.visible.id}
            await sync_to_async(self.visible.update)(
                ignore_permission=True, status="hidden"
            )
            assert await peer.receive_nothing(timeout=0.1)
            await self.publish({"id": self.visible.id})
            assert await peer.receive_nothing(timeout=0.1)
            await sync_to_async(self.visible.update)(
                ignore_permission=True, status="visible"
            )
            assert (await peer.receive_output())["type"] == "websocket.send"
            await sync_to_async(
                get_user_model().objects.filter(pk=self.user.pk).update
            )(is_staff=False)
            await self.publish({"id": self.visible.id})
            assert await peer.receive_nothing(timeout=0.1)
            await peer.send_input({"type": "websocket.disconnect"})
            await peer.wait()
            assert not self.layer.groups

        with patch(
            "general_manager.api.remote_invalidation._get_channel_layer_safe",
            return_value=self.layer,
        ):
            asyncio.run(run())

    def test_delete_missing_and_bulk_are_suppressed(self):
        async def run():
            peer = self.peer(self.user)
            await peer.send_input({"type": "websocket.connect"})
            assert (await peer.receive_output())["type"] == "websocket.accept"
            for identity, action in [
                ({"id": self.visible.id}, "delete"),
                ({"id": -1}, "update"),
                (None, "refresh"),
                (None, "update"),
                ({}, "update"),
                ([], "update"),
            ]:
                await self.publish(identity, action)
                assert await peer.receive_nothing(timeout=0.1)
            await peer.send_input({"type": "websocket.disconnect"})
            await peer.wait()

        with patch(
            "general_manager.api.remote_invalidation._get_channel_layer_safe",
            return_value=self.layer,
        ):
            asyncio.run(run())

    def test_unpersisted_inactive_and_deleted_accounts_cannot_receive(self):
        event = {
            "action": "update",
            "base_path": self.config.base_path,
            "resource_name": self.config.resource_name,
            "protocol_version": self.config.protocol_version,
            "identification": {"id": self.visible.id},
        }
        assert _fresh_remote_principal(get_user_model()()) is None
        assert _can_receive_remote_event(self.config, self.user, event)
        # Keep the original active scope object to prove a fresh database lookup.
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        assert self.user.is_active
        assert not _can_receive_remote_event(self.config, self.user, event)
        get_user_model().objects.filter(pk=self.user.pk).delete()
        assert not _can_receive_remote_event(self.config, self.user, event)

    def test_memory_exhaustion_is_not_swallowed_as_permission_denial(self):
        event = {
            "action": "update",
            "base_path": self.config.base_path,
            "resource_name": self.config.resource_name,
            "protocol_version": self.config.protocol_version,
            "identification": {"id": self.visible.id},
        }
        with (
            patch(
                "general_manager.api.remote_invalidation._fresh_remote_principal",
                side_effect=MemoryError,
            ),
            self.assertRaises(MemoryError),
        ):
            _can_receive_remote_event(self.config, self.user, event)

    def test_delegated_native_permission_reloads_parent(self):
        child = self.Child.create(ignore_permission=True, parent=self.visible)
        config = get_remote_api_config(self.Child)
        event = {
            "action": "update",
            "base_path": config.base_path,
            "resource_name": config.resource_name,
            "protocol_version": config.protocol_version,
            "identification": {"id": child.id},
        }
        assert _can_receive_remote_event(config, self.user, event)
        self.visible.update(ignore_permission=True, status="hidden")
        assert not _can_receive_remote_event(config, self.user, event)

    def test_failed_accept_and_cancellation_discard_group(self):
        async def run():
            consumer = RemoteInvalidationConsumer()
            scope = {
                "user": self.user,
                "url_route": {
                    "kwargs": {
                        "base_path": "internal/gm",
                        "resource_name": "secured-projects",
                    }
                },
            }

            async def receive():
                await asyncio.Future()

            async def fail_send(message):
                message = "accept failed"
                raise RuntimeError(message)

            with self.assertRaisesRegex(RuntimeError, "accept failed"):
                await consumer(scope, receive, fail_send)
            assert not self.layer.groups
            accepted = asyncio.Event()

            async def send(message):
                accepted.set()

            task = asyncio.create_task(
                RemoteInvalidationConsumer()(scope, receive, send)
            )
            await accepted.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert not self.layer.groups

        with patch(
            "general_manager.api.remote_invalidation._get_channel_layer_safe",
            return_value=self.layer,
        ):
            asyncio.run(run())

    def test_measured_fanout_and_native_query_cost(self):
        measurements = []

        def measured(config, user, event):
            with CaptureQueriesContext(connection) as queries:
                allowed = _can_receive_remote_event(config, user, event)
            measurements.append((allowed, len(queries)))
            return allowed

        async def run():
            peers = [self.peer(self.user) for _ in range(8)]
            for peer in peers:
                await peer.send_input({"type": "websocket.connect"})
                assert (await peer.receive_output())["type"] == "websocket.accept"
            await self.publish({"id": self.visible.id})
            for peer in peers:
                assert (await peer.receive_output())["type"] == "websocket.send"
            await self.publish({"id": self.hidden.id})
            for peer in peers:
                assert await peer.receive_nothing(timeout=0.05)
            for peer in peers:
                await peer.send_input({"type": "websocket.disconnect"})
                await peer.wait()

        with (
            patch(
                "general_manager.api.remote_invalidation._get_channel_layer_safe",
                return_value=self.layer,
            ),
            patch(
                "general_manager.api.remote_invalidation._can_receive_remote_event",
                side_effect=measured,
            ),
        ):
            asyncio.run(run())
        assert len(measurements) == 16
        assert sum(allowed for allowed, _ in measurements) == 8
        assert all(count == 3 for _, count in measurements)
        print(
            "Measured: 8 subscribers; 2 publications; 16 authorization decisions; 48 SQL queries; 8 public sends"
        )

    def test_lazy_remote_requires_successful_current_detail_read(self):
        from general_manager.interface import RemoteManagerInterface
        from general_manager.interface.requests import (
            RequestField,
            RequestNotFoundError,
            RequestQueryResult,
        )
        from general_manager.manager.input import Input
        from general_manager.permission import OverrideManagerPermission

        class LazyEventRemote(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(int)
                title = RequestField(str)

                class Meta:
                    base_url = "https://unused.invalid"
                    remote_manager = "lazy"

            class Permission(OverrideManagerPermission):
                __read__: ClassVar[list[str]] = ["isAdmin"]

            class RemoteAPI:
                enabled = True
                allow_update = True
                resource_name = "lazy"
                websocket_invalidation = True

        config = get_remote_api_config(LazyEventRemote)
        event = {
            "action": "update",
            "base_path": config.base_path,
            "resource_name": config.resource_name,
            "protocol_version": config.protocol_version,
            "identification": {"id": 999},
        }
        for error in (RequestNotFoundError("gone"), RuntimeError("upstream failure")):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(
                    LazyEventRemote.Interface, "execute_request_plan", side_effect=error
                ) as read,
            ):
                assert not _can_receive_remote_event(config, self.user, event)
                assert read.call_count == 1
        with patch.object(
            LazyEventRemote.Interface,
            "execute_request_plan",
            return_value=RequestQueryResult(items=({"id": 999, "title": "current"},)),
        ) as read:
            assert _can_receive_remote_event(config, self.user, event)
            assert read.call_count == 1

    def test_native_plan_and_instance_gate_both_apply(self):
        from general_manager.permission.base_permission import (
            BasePermission,
            ReadPermissionPlan,
        )

        class PlanPermission(BasePermission):
            def check_permission(self, action, attribute):
                return True

            def check_operation_permission(self, action):
                return True

            def describe_operation_permissions(self, action):
                return ()

            def get_read_permission_plan(self):
                return selected_plan

        event = {
            "action": "update",
            "base_path": self.config.base_path,
            "resource_name": self.config.resource_name,
            "protocol_version": self.config.protocol_version,
            "identification": {"id": self.visible.id},
        }
        for selected_plan, expected in [
            (
                ReadPermissionPlan(
                    filters=[], requires_instance_check=False, decision="deny_all"
                ),
                False,
            ),
            (
                ReadPermissionPlan(
                    filters=[{"filter": {"status": "hidden"}}],
                    requires_instance_check=False,
                ),
                False,
            ),
            (
                ReadPermissionPlan(
                    filters=[{"exclude": {"status": "visible"}}],
                    requires_instance_check=False,
                ),
                False,
            ),
            (
                ReadPermissionPlan(
                    filters=[{"filter": {"status": "visible"}}],
                    requires_instance_check=False,
                ),
                True,
            ),
            (
                ReadPermissionPlan(
                    filters=[{"exclude": {"status": "visible", "id": -1}}],
                    requires_instance_check=False,
                ),
                True,
            ),
        ]:
            with (
                self.subTest(plan=selected_plan),
                patch.object(self.Project, "Permission", PlanPermission),
            ):
                assert (
                    _can_receive_remote_event(self.config, self.user, event) == expected
                )
        selected_plan = ReadPermissionPlan(
            filters=[], requires_instance_check=False, decision="allow_all"
        )
        with (
            patch.object(self.Project, "Permission", PlanPermission),
            patch.object(
                PlanPermission, "can_read_instance", return_value=False
            ) as gate,
        ):
            assert not _can_receive_remote_event(self.config, self.user, event)
            gate.assert_called_once()
