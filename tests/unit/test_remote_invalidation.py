from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock, patch
from uuid import UUID

from asgiref.sync import async_to_sync
from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import-untyped]
from django.test import SimpleTestCase
from django.test import override_settings
from django.urls import re_path

from general_manager.api import bulk_data_change_notifications
from general_manager.api.graphql import GraphQL
from general_manager.api.remote_invalidation import (
    clear_remote_invalidation_routes,
    emit_remote_invalidation,
    ensure_remote_invalidation_route,
)
from general_manager.manager.general_manager import GeneralManager

from tests import testing_asgi
from tests.utils.simple_manager_interface import BaseTestInterface


def _unwrap_websocket_router():
    router = testing_asgi.application.application_mapping["websocket"]
    while hasattr(router, "inner") and not hasattr(router, "routes"):
        router = router.inner
    return router


class RemoteInvalidationRouteTests(SimpleTestCase):
    def setUp(self) -> None:
        self._original_websocket_urlpatterns = list(testing_asgi.websocket_urlpatterns)
        self._original_application = testing_asgi.application
        application_mapping = getattr(
            testing_asgi.application, "application_mapping", {}
        )
        self._original_websocket_application = (
            application_mapping.get("websocket")
            if isinstance(application_mapping, dict)
            else None
        )

    def tearDown(self) -> None:
        clear_remote_invalidation_routes()
        testing_asgi.websocket_urlpatterns[:] = self._original_websocket_urlpatterns
        testing_asgi.application = self._original_application
        application_mapping = getattr(
            testing_asgi.application, "application_mapping", {}
        )
        if self._original_websocket_application is not None and isinstance(
            application_mapping, dict
        ):
            application_mapping["websocket"] = self._original_websocket_application

    def test_clear_remote_invalidation_routes_rebuilds_live_router(self) -> None:
        if testing_asgi.websocket_urlpatterns:
            graphql_route = testing_asgi.websocket_urlpatterns[0]
        else:
            graphql_route = re_path(r"^graphql/?$", lambda *_: None)
            testing_asgi.websocket_urlpatterns[:] = [graphql_route]
        remote_route = re_path(r"^remote/ws/projects/?$", lambda *_: None)
        remote_route._general_manager_remote_ws = True
        remote_route._general_manager_remote_ws_key = ("/remote", "projects")
        testing_asgi.websocket_urlpatterns[:] = [graphql_route, remote_route]
        testing_asgi.application.application_mapping["websocket"] = AuthMiddlewareStack(
            URLRouter(list(testing_asgi.websocket_urlpatterns))
        )
        router = _unwrap_websocket_router()
        self.assertEqual(len(testing_asgi.websocket_urlpatterns), 2)
        self.assertEqual(len(router.routes), 2)

        clear_remote_invalidation_routes()

        router = _unwrap_websocket_router()
        self.assertEqual(len(testing_asgi.websocket_urlpatterns), 1)
        self.assertIs(testing_asgi.websocket_urlpatterns[0], graphql_route)
        self.assertEqual(len(router.routes), 1)

    def test_clear_remote_invalidation_routes_rebuilds_protocol_router_for_plain_app(
        self,
    ) -> None:
        if testing_asgi.websocket_urlpatterns:
            graphql_route = testing_asgi.websocket_urlpatterns[0]
        else:
            graphql_route = re_path(r"^graphql/?$", lambda *_: None)
            testing_asgi.websocket_urlpatterns[:] = [graphql_route]
        remote_route = re_path(r"^remote/ws/projects/?$", lambda *_: None)
        remote_route._general_manager_remote_ws = True
        remote_route._general_manager_remote_ws_key = ("/remote", "projects")
        original_application = testing_asgi.application
        try:
            testing_asgi.websocket_urlpatterns[:] = [graphql_route, remote_route]

            async def http_app(scope, receive, send):  # type: ignore[no-untyped-def]
                del scope, receive, send

            testing_asgi.application = http_app

            clear_remote_invalidation_routes()

            self.assertIsInstance(testing_asgi.application, ProtocolTypeRouter)
            router = _unwrap_websocket_router()
            self.assertEqual(len(testing_asgi.websocket_urlpatterns), 1)
            self.assertEqual(len(router.routes), 1)
            self.assertIs(testing_asgi.websocket_urlpatterns[0], graphql_route)
        finally:
            testing_asgi.application = original_application

    @override_settings(ASGI_APPLICATION="tests.testing_asgi.custom_application")
    def test_ensure_remote_invalidation_route_wraps_configured_asgi_attribute(
        self,
    ) -> None:
        """Wrap the configured ASGI application attribute, not a hard-coded name."""

        class Project:
            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "projects"
                allow_update = True
                websocket_invalidation = True

        async def custom_application(scope, receive, send):  # type: ignore[no-untyped-def]
            del scope, receive, send

        original_application = getattr(testing_asgi, "custom_application", None)
        testing_asgi.custom_application = custom_application
        testing_asgi.websocket_urlpatterns[:] = []
        try:
            ensure_remote_invalidation_route([Project])

            self.assertIsInstance(testing_asgi.custom_application, ProtocolTypeRouter)
            self.assertIsNot(testing_asgi.custom_application, custom_application)
            self.assertEqual(len(testing_asgi.websocket_urlpatterns), 1)
        finally:
            if original_application is None:
                delattr(testing_asgi, "custom_application")
            else:
                testing_asgi.custom_application = original_application

    @override_settings(ASGI_APPLICATION="tests.testing_asgi.application")
    def test_ensure_remote_invalidation_route_preserves_existing_mapped_routes(
        self,
    ) -> None:
        """Merge remote routes into the existing websocket router."""

        class Project:
            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "projects"
                allow_update = True
                websocket_invalidation = True

        existing_route = re_path(
            r"^existing/$",
            lambda _scope, _receive, _send: None,
        )
        testing_asgi.websocket_urlpatterns[:] = []
        testing_asgi.application.application_mapping["websocket"] = AuthMiddlewareStack(
            URLRouter([existing_route])
        )

        ensure_remote_invalidation_route([Project])

        router = _unwrap_websocket_router()
        self.assertIn(existing_route, router.routes)
        self.assertEqual(len(router.routes), 2)

    def test_emit_remote_invalidation_serializes_identification_values(self) -> None:
        class Project:
            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "projects"
                allow_update = True
                websocket_invalidation = True

        channel_layer = SimpleNamespace(group_send=MagicMock())
        instance = SimpleNamespace(
            identification={
                "id": UUID("12345678-1234-5678-1234-567812345678"),
                "start_date": date(2026, 3, 18),
                "updated_at": datetime(2026, 3, 18, 12, 30, 0),
                "name": "Alpha",
            }
        )

        with (
            patch(
                "general_manager.api.remote_invalidation._get_channel_layer_safe",
                return_value=channel_layer,
            ),
            patch(
                "general_manager.api.remote_invalidation.async_to_sync",
                side_effect=lambda fn: fn,
            ),
        ):
            emit_remote_invalidation(Project, instance=instance, action="update")

        _, payload = channel_layer.group_send.call_args.args
        self.assertEqual(
            payload["identification"]["id"],
            "12345678-1234-5678-1234-567812345678",
        )
        self.assertEqual(payload["identification"]["start_date"], "2026-03-18")
        self.assertEqual(payload["identification"]["updated_at"], "2026-03-18T12:30:00")
        self.assertEqual(payload["identification"]["name"], "Alpha")

    def test_emit_remote_invalidation_uses_delete_identification_metadata(
        self,
    ) -> None:
        class Project:
            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "projects"
                allow_update = True
                websocket_invalidation = True

        channel_layer = SimpleNamespace(group_send=MagicMock())

        with (
            patch(
                "general_manager.api.remote_invalidation._get_channel_layer_safe",
                return_value=channel_layer,
            ),
            patch(
                "general_manager.api.remote_invalidation.async_to_sync",
                side_effect=lambda fn: fn,
            ),
        ):
            emit_remote_invalidation(
                Project,
                instance=None,
                identification={"id": UUID("12345678-1234-5678-1234-567812345678")},
                action="delete",
            )

        _, payload = channel_layer.group_send.call_args.args
        self.assertEqual(payload["action"], "delete")
        self.assertEqual(
            payload["identification"],
            {"id": "12345678-1234-5678-1234-567812345678"},
        )


class RemoteInvalidationBatchTests(SimpleTestCase):
    def test_batch_deduplicates_rows_into_one_resource_refresh(self) -> None:
        class Project:
            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "projects"
                allow_update = True
                websocket_invalidation = True
                protocol_version = "v2"

        sent: list[tuple[str, dict[str, object]]] = []

        async def group_send(group: str, message: dict[str, object]) -> None:
            sent.append((group, message))

        channel_layer = SimpleNamespace(group_send=group_send)
        with (
            patch(
                "general_manager.api.remote_invalidation._get_channel_layer_safe",
                return_value=channel_layer,
            ),
            patch(
                "general_manager.api.remote_invalidation.async_to_sync"
            ) as immediate_bridge,
        ):
            with bulk_data_change_notifications():
                for identification in ({"id": 1}, {"id": 2}, {"id": 3}):
                    emit_remote_invalidation(
                        Project,
                        identification=identification,
                        action="update",
                    )
                self.assertEqual(sent, [])
                immediate_bridge.assert_not_called()

        self.assertEqual(len(sent), 1)
        group, payload = sent[0]
        self.assertEqual(group, "gm.remote.remote.projects")
        event_id = payload.pop("event_id")
        self.assertEqual(
            payload,
            {
                "type": "gm.remote.invalidation",
                "protocol_version": "v2",
                "base_path": "/remote",
                "resource_name": "projects",
                "action": "refresh",
                "identification": None,
            },
        )
        self.assertEqual(UUID(str(event_id)).version, 4)

    def test_batch_queues_one_refresh_for_each_remote_resource(self) -> None:
        class Project:
            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "projects"
                allow_update = True
                websocket_invalidation = True

        class Task:
            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "tasks"
                allow_delete = True
                websocket_invalidation = True

        sent: list[tuple[str, dict[str, object]]] = []

        async def group_send(group: str, message: dict[str, object]) -> None:
            sent.append((group, message))

        channel_layer = SimpleNamespace(group_send=group_send)
        with patch(
            "general_manager.api.remote_invalidation._get_channel_layer_safe",
            return_value=channel_layer,
        ):
            with bulk_data_change_notifications():
                emit_remote_invalidation(
                    Task, identification={"id": 2}, action="delete"
                )
                emit_remote_invalidation(Project, instance=None, action="update")

        self.assertEqual(
            [
                (group, message["resource_name"], message["action"])
                for group, message in sent
            ],
            [
                ("gm.remote.remote.projects", "projects", "refresh"),
                ("gm.remote.remote.tasks", "tasks", "refresh"),
            ],
        )
        self.assertTrue(all(message["identification"] is None for _, message in sent))

    def test_batch_skips_managers_without_enabled_websocket_config(self) -> None:
        class NoRemoteAPI:
            pass

        class WebsocketDisabled:
            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "disabled"
                allow_update = True
                websocket_invalidation = False

        with (
            patch(
                "general_manager.api.remote_invalidation._get_channel_layer_safe"
            ) as get_channel_layer,
            patch("general_manager.api.notification_batching.async_to_sync") as bridge,
        ):
            with bulk_data_change_notifications():
                emit_remote_invalidation(NoRemoteAPI, action="update")
                emit_remote_invalidation(WebsocketDisabled, action="update")

        get_channel_layer.assert_not_called()
        bridge.assert_not_called()

    def test_batch_skips_remote_resource_without_channel_layer(self) -> None:
        class Project:
            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "projects"
                allow_update = True
                websocket_invalidation = True

        with (
            patch(
                "general_manager.api.remote_invalidation._get_channel_layer_safe",
                return_value=None,
            ),
            patch("general_manager.api.notification_batching.async_to_sync") as bridge,
        ):
            with bulk_data_change_notifications():
                emit_remote_invalidation(Project, action="update")

        bridge.assert_not_called()

    def test_graphql_and_remote_refreshes_share_one_batch_bridge(self) -> None:
        class Project(GeneralManager):
            identification: ClassVar[dict[str, object]] = {"id": 1}
            Interface = BaseTestInterface

            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "projects"
                allow_update = True
                websocket_invalidation = True

        sent: list[tuple[str, str, dict[str, object]]] = []

        async def graphql_group_send(group: str, message: dict[str, object]) -> None:
            await asyncio.sleep(0)
            sent.append(("graphql", group, message))

        async def remote_group_send(group: str, message: dict[str, object]) -> None:
            await asyncio.sleep(0)
            sent.append(("remote", group, message))

        graphql_layer = SimpleNamespace(group_send=graphql_group_send)
        remote_layer = SimpleNamespace(group_send=remote_group_send)
        project = Project()
        with (
            patch.object(GraphQL, "manager_registry", {"Project": Project}),
            patch.object(GraphQL, "_get_channel_layer", return_value=graphql_layer),
            patch(
                "general_manager.api.remote_invalidation._get_channel_layer_safe",
                return_value=remote_layer,
            ),
            patch("general_manager.api.graphql.async_to_sync") as graphql_bridge,
            patch(
                "general_manager.api.remote_invalidation.async_to_sync"
            ) as remote_bridge,
            patch(
                "general_manager.api.notification_batching.async_to_sync",
                side_effect=async_to_sync,
            ) as batch_bridge,
        ):
            with bulk_data_change_notifications():
                GraphQL._handle_data_change(
                    sender=Project,
                    instance=project,
                    action="update",
                )
                emit_remote_invalidation(
                    Project,
                    instance=project,
                    action="update",
                )

        batch_bridge.assert_called_once()
        graphql_bridge.assert_not_called()
        remote_bridge.assert_not_called()
        self.assertEqual(
            [
                (subsystem, group, message["action"])
                for subsystem, group, message in sent
            ],
            [
                ("graphql", GraphQL._refresh_group_name(Project), "refresh"),
                ("remote", "gm.remote.remote.projects", "refresh"),
            ],
        )
