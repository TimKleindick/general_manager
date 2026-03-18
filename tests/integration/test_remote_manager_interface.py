from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit
from typing import Any, ClassVar

from asgiref.testing import ApplicationCommunicator
from asgiref.sync import sync_to_async
from django.db.models import CharField
from django.test import Client, override_settings

from general_manager.api import RemoteInvalidationClient
from general_manager.cache.cache_decorator import cached
from general_manager.interface import DatabaseInterface, RemoteManagerInterface
from general_manager.interface.requests import (
    RequestNotFoundError,
    RequestField,
    RequestTransportRequest,
    RequestTransportResponse,
    RequestTransportStatusError,
    RequestTransportError,
    SharedRequestTransport,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import AttributeEvaluationError, GeneralManagerMeta
from general_manager.manager.input import Input
from general_manager.permission import ManagerBasedPermission
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from tests.testing_asgi import application


class DjangoClientTransport(SharedRequestTransport):
    def __init__(self) -> None:
        self.client: Client | None = None

    def send(
        self,
        request: RequestTransportRequest,
        *,
        interface_cls: type[Any],
        operation: Any,
        plan: Any,
        identification: dict[str, Any] | None,
    ) -> RequestTransportResponse:
        del interface_cls, operation, plan, identification
        if self.client is None:
            raise AssertionError

        body = json.dumps(dict(request.body)) if request.body is not None else None
        response = self.client.generic(
            request.method,
            request.path,
            data=body,
            content_type="application/json",
            **{
                "HTTP_X_GENERAL_MANAGER_PROTOCOL_VERSION": request.headers.get(
                    "X-General-Manager-Protocol-Version", ""
                )
            },
        )
        payload = json.loads(response.content.decode("utf-8"))
        if response.status_code >= 400:
            raise RequestTransportStatusError(
                status_code=response.status_code,
                request=request,
                payload=payload,
                headers=dict(response.headers),
            )
        return RequestTransportResponse(
            payload=payload,
            status_code=response.status_code,
            headers=dict(response.headers),
        )


class ASGIWebSocketConnection:
    def __init__(self, url: str) -> None:
        parsed = urlsplit(url)
        query_string = parsed.query.encode("utf-8")
        self.communicator = ApplicationCommunicator(
            application,
            {
                "type": "websocket",
                "path": parsed.path,
                "query_string": query_string,
                "headers": [],
                "subprotocols": [],
            },
        )
        self._connected = False

    async def connect(self) -> None:
        if not self._connected:
            await self.communicator.send_input({"type": "websocket.connect"})
            accept = await self.communicator.receive_output()
            assert accept["type"] == "websocket.accept"
            self._connected = True

    async def recv(self) -> str:
        await self.connect()
        await self.communicator.send_input(
            {"type": "websocket.receive", "text": "ping"}
        )
        message = await self.communicator.receive_output()
        assert message["type"] == "websocket.send"
        return str(message["text"])

    async def close(self) -> None:
        if self._connected:
            await self.communicator.send_input(
                {"type": "websocket.disconnect", "code": 1000}
            )
        await self.communicator.wait()


@override_settings(AUTOCREATE_GRAPHQL=False)
class RemoteManagerInterfaceIntegrationTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        transport = DjangoClientTransport()

        class Project(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)
                status = CharField(max_length=50)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

            class RemoteAPI:
                enabled = True
                base_path = "/internal/gm"
                resource_name = "projects"
                allow_filter = True
                allow_detail = True
                allow_create = True
                allow_update = True
                allow_delete = True
                websocket_invalidation = True
                protocol_version = "v1"

        class HiddenProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=200)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        class RemoteProject(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                name = RequestField(str)
                status = RequestField(str)

                class Meta:
                    base_url = "http://testserver"
                    base_path = "/internal/gm"
                    remote_manager = "projects"
                    protocol_version = "v1"
                    websocket_invalidation_enabled = True

        RemoteProject.Interface.transport = transport

        cls.general_manager_classes = [Project, HiddenProject, RemoteProject]
        cls.Project = Project
        cls.HiddenProject = HiddenProject
        cls.RemoteProject = RemoteProject
        cls.transport = transport
        GeneralManagerMeta.all_classes = cls.general_manager_classes
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.transport.client = self.client
        self.project = self.Project.create(
            ignore_permission=True,
            name="Alpha",
            status="active",
        )
        self.Project.create(
            ignore_permission=True,
            name="Beta",
            status="inactive",
        )

    def test_remote_manager_interface_can_query_detail_and_hide_non_exposed_managers(
        self,
    ) -> None:
        query_response = self.client.generic(
            "POST",
            "/internal/gm/projects/query",
            data=json.dumps({"filters": {"status": "active"}, "excludes": {}}),
            content_type="application/json",
        )
        self.assertEqual(query_response.status_code, 200)

        active_projects = list(self.RemoteProject.filter(status="active"))
        self.assertEqual(len(active_projects), 1)
        self.assertEqual(active_projects[0].name, "Alpha")
        self.assertEqual(
            active_projects[0]._interface._request_payload_cache["status"],
            "active",
        )

        remote_project = self.RemoteProject(id=self.project.id)
        self.assertEqual(remote_project.name, "Alpha")

        hidden_response = self.client.generic(
            "POST",
            "/internal/gm/hidden-projects/query",
            data=json.dumps({"filters": {}, "excludes": {}}),
            content_type="application/json",
        )
        self.assertEqual(hidden_response.status_code, 404)

    def test_remote_manager_interface_mutations_round_trip(self) -> None:
        created = self.RemoteProject.create(
            ignore_permission=True,
            name="Gamma",
            status="active",
        )
        self.assertEqual(created.name, "Gamma")
        self.assertEqual(created.status, "active")

        updated = created.update(ignore_permission=True, status="inactive")
        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.status, "inactive")

        deleted_id = created.id
        created.delete(ignore_permission=True)
        response = self.client.get(f"/internal/gm/projects/{deleted_id}")
        self.assertEqual(response.status_code, 404)

    def test_protocol_version_mismatch_fails_explicitly(self) -> None:
        transport = DjangoClientTransport()

        class WrongVersionProject(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                name = RequestField(str)
                status = RequestField(str)

                class Meta:
                    base_url = "http://testserver"
                    base_path = "/internal/gm"
                    remote_manager = "projects"
                    protocol_version = "v2"

        WrongVersionProject.Interface.transport = transport
        transport.client = self.client

        with self.assertRaises(RequestTransportError):
            list(WrongVersionProject.filter(status="active"))

    def test_query_supports_ordering_and_pagination(self) -> None:
        ordered_page = list(
            self.RemoteProject.filter(
                ordering="-name",
                page=1,
                page_size=1,
            )
        )
        self.assertEqual(len(ordered_page), 1)
        self.assertEqual(ordered_page[0].name, "Beta")
        self.assertEqual(
            ordered_page[0]._interface._request_payload_cache["name"], "Beta"
        )

    def test_missing_detail_maps_remote_error_and_preserves_request_id(self) -> None:
        missing = self.RemoteProject(id=999)
        with self.assertRaises(AttributeEvaluationError) as context:
            _ = missing.name

        cause = context.exception.__cause__
        self.assertIsInstance(cause, RequestNotFoundError)
        self.assertEqual(cause.status_code, 404)
        self.assertEqual(cause.error_code, "not_found")
        self.assertEqual(cause.request_id, "gm-detail-999")

    def test_websocket_event_can_invalidate_cached_remote_queries(self) -> None:
        @cached()
        def active_count() -> int:
            return self.RemoteProject.filter(status="active").count()

        self.assertEqual(active_count(), 1)
        self.assert_cache_miss()
        self.assertEqual(active_count(), 1)
        self.assert_cache_hit()

        self.RemoteProject.Interface.handle_invalidation_event(
            {
                "protocol_version": "v1",
                "base_path": "/internal/gm",
                "resource_name": "projects",
                "action": "update",
                "identification": {"id": self.project.id},
            }
        )

        self.Project.create(ignore_permission=True, name="Gamma", status="active")
        self.assertEqual(active_count(), 2)
        self.assert_cache_miss()

    def test_websocket_invalidation_emits_minimal_payload(self) -> None:
        async def run_test() -> None:
            communicator = ApplicationCommunicator(
                application,
                {
                    "type": "websocket",
                    "path": "/internal/gm/ws/projects",
                    "query_string": b"version=v1",
                    "headers": [],
                    "subprotocols": [],
                },
            )
            await communicator.send_input({"type": "websocket.connect"})
            accept = await communicator.receive_output()
            assert accept["type"] == "websocket.accept"

            await sync_to_async(self.project.update)(
                ignore_permission=True,
                status="inactive",
            )
            message = await communicator.receive_output()
            assert message["type"] == "websocket.send"
            payload = json.loads(message["text"])
            assert payload == {
                "protocol_version": "v1",
                "base_path": "/internal/gm",
                "resource_name": "projects",
                "action": "update",
                "identification": {"id": self.project.id},
                "event_id": payload["event_id"],
            }
            assert isinstance(payload["event_id"], str)

            await communicator.send_input(
                {"type": "websocket.disconnect", "code": 1000}
            )
            await communicator.wait()

        asyncio.run(run_test())

    def test_remote_invalidation_client_can_invalidate_cached_remote_queries(
        self,
    ) -> None:
        @cached()
        def active_count() -> int:
            return self.RemoteProject.filter(status="active").count()

        self.assertEqual(active_count(), 1)
        self.assert_cache_miss()
        self.assertEqual(active_count(), 1)
        self.assert_cache_hit()

        async def run_test() -> None:
            client = RemoteInvalidationClient(
                [self.RemoteProject],
                connection_factory=ASGIWebSocketConnection,
            )
            await client.connect()
            await sync_to_async(self.Project.create)(
                ignore_permission=True,
                name="Gamma",
                status="active",
            )
            await client.listen_once()
            await client.close()

        asyncio.run(run_test())

        self.assertEqual(active_count(), 2)
        self.assert_cache_miss()
