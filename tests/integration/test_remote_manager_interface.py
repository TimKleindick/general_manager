from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qsl, urlencode, urlsplit
from typing import Any, ClassVar
from unittest.mock import patch

from asgiref.testing import ApplicationCommunicator
from asgiref.sync import sync_to_async
from django.db.models import CharField
from django.http import HttpResponse
from django.test import Client, override_settings

from general_manager.api import RemoteInvalidationClient
from general_manager.as_of import (
    HistoricalMutationError,
    HistoricalReadNotSupportedError,
    as_of,
)
from general_manager.cache.cache_decorator import cached
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.interface import DatabaseInterface, RemoteManagerInterface
from general_manager.interface.requests import (
    RequestNotFoundError,
    RequestField,
    RequestPlan,
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
        plan: RequestPlan,
        identification: dict[str, Any] | None,
    ) -> RequestTransportResponse:
        del interface_cls, operation, plan, identification
        if self.client is None:
            raise AssertionError

        body = json.dumps(dict(request.body)) if request.body is not None else None
        request_query = urlsplit(request.url).query
        query = request_query
        if request.query_params:
            encoded_pairs = [
                (key, str(value))
                for key, raw_value in request.query_params.items()
                for value in (
                    raw_value if isinstance(raw_value, list | tuple) else (raw_value,)
                )
            ]
            existing_pairs = set(parse_qsl(request_query, keep_blank_values=True))
            extra_pairs = [
                (key, value)
                for key, value in encoded_pairs
                if (key, value) not in existing_pairs
            ]
            if extra_pairs:
                extra_query = urlencode(extra_pairs, doseq=True)
                query = (
                    f"{request_query}&{extra_query}" if request_query else extra_query
                )
        full_path = f"{request.path}?{query}" if query else request.path
        response = self.client.generic(
            request.method,
            full_path,
            data=body,
            content_type="application/json",
            **{
                "HTTP_X_GENERAL_MANAGER_PROTOCOL_VERSION": request.headers.get(
                    "X-General-Manager-Protocol-Version", ""
                )
            },
        )
        payload = None
        if response.status_code != 204 and response.content:
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


class DjangoClientTransportTests(GeneralManagerTransactionTestCase):
    def test_send_preserves_query_string(self) -> None:
        transport = DjangoClientTransport()
        calls: list[tuple[str, str]] = []

        class FakeClient:
            def generic(self, method: str, path: str, **kwargs: Any) -> HttpResponse:
                del kwargs
                calls.append((method, path))
                response = HttpResponse(
                    json.dumps({"items": [], "metadata": {}}),
                    content_type="application/json",
                )
                response.status_code = 200
                return response

        transport.client = FakeClient()  # type: ignore[assignment]

        transport.send(
            RequestTransportRequest(
                method="GET",
                url="https://service.example.test/projects?page=2",
                path="/projects",
                query_params={"page": 2, "search": "alpha"},
            ),
            interface_cls=object,
            operation=object(),
            plan=object(),
            identification=None,
        )

        self.assertEqual(calls, [("GET", "/projects?page=2&search=alpha")])

    def test_send_prefers_original_encoded_query_string(self) -> None:
        transport = DjangoClientTransport()
        calls: list[tuple[str, str]] = []

        class FakeClient:
            def generic(self, method: str, path: str, **kwargs: Any) -> HttpResponse:
                del kwargs
                calls.append((method, path))
                response = HttpResponse(
                    json.dumps({"items": [], "metadata": {}}),
                    content_type="application/json",
                )
                response.status_code = 200
                return response

        transport.client = FakeClient()  # type: ignore[assignment]

        transport.send(
            RequestTransportRequest(
                method="GET",
                url="https://service.example.test/projects?search=a%2Bb",
                path="/projects",
                query_params={"search": "a+b"},
            ),
            interface_cls=object,
            operation=object(),
            plan=object(),
            identification=None,
        )

        self.assertEqual(calls, [("GET", "/projects?search=a%2Bb")])


class ASGIWebSocketConnectionTests(GeneralManagerTransactionTestCase):
    def test_close_without_connect_is_safe(self) -> None:
        async def run_test() -> None:
            connection = ASGIWebSocketConnection(
                "ws://testserver/internal/gm/ws/projects?version=v1"
            )
            await connection.close()

        asyncio.run(run_test())


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

    def test_historical_context_rejects_direct_remote_interface_mutations(self) -> None:
        interface = self.RemoteProject(id=self.project.id)._interface
        interface._request_payload_cache = {
            "id": self.project.id,
            "name": "Alpha",
            "status": "active",
        }

        with (
            patch.object(
                self.RemoteProject.Interface, "execute_request_plan"
            ) as request,
            as_of("2022-01-01"),
        ):
            with self.assertRaises(HistoricalMutationError):
                self.RemoteProject.Interface.create(name="Blocked", status="active")
            with self.assertRaises(HistoricalMutationError):
                interface.update(status="inactive")
            with self.assertRaises(HistoricalMutationError):
                interface.delete()

        request.assert_not_called()

    def test_historical_context_rejects_remote_reads_before_planning_or_transport(
        self,
    ) -> None:
        interface_cls = self.RemoteProject.Interface
        query_capability = interface_cls.require_capability("query")
        project_id = self.project.id
        operations = (
            lambda: self.RemoteProject(id=project_id),
            lambda: interface_cls(id=project_id),
            lambda: self.RemoteProject.get(id=project_id),
            lambda: self.RemoteProject.filter(status="active"),
            lambda: self.RemoteProject.exclude(status="inactive"),
            self.RemoteProject.all,
            lambda: interface_cls.query_operation("list"),
            lambda: query_capability.for_operation(interface_cls, "list"),
            lambda: query_capability.filter(interface_cls, status="active"),
            lambda: query_capability.exclude(interface_cls, status="inactive"),
            lambda: query_capability.all(interface_cls),
        )

        with (
            patch.object(interface_cls, "get_query_operation") as planner,
            patch.object(interface_cls, "execute_request_plan") as transport,
            as_of("2022-01-01"),
        ):
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(HistoricalReadNotSupportedError):
                        operation()

        planner.assert_not_called()
        transport.assert_not_called()

    def test_precreated_remote_reads_fail_before_any_request_side_effect(self) -> None:
        interface_cls = self.RemoteProject.Interface
        project_id = self.project.id
        bucket = self.RemoteProject.all()
        request_plan = bucket.request_plan
        self.assertIsNotNone(request_plan)
        manager = self.RemoteProject(id=project_id)
        interface = manager._interface
        interface.set_request_payload_cache(
            {"id": project_id, "name": "Cached", "status": "active"}
        )
        query_capability = interface_cls.require_capability("query")

        with (
            patch.object(
                interface_cls,
                "get_query_operation",
                wraps=interface_cls.get_query_operation,
            ) as planner,
            patch.object(DependencyTracker, "track") as dependency,
            patch(
                "general_manager.interface.capabilities.request.with_observability"
            ) as observability,
            patch.object(interface_cls, "execute_request_plan") as transport,
            as_of("2022-01-01"),
        ):
            operations = (
                lambda: query_capability.validate_lookups(interface_cls),
                lambda: query_capability.build_bucket(interface_cls),
                lambda: query_capability.execute_plan(interface_cls, request_plan),
                lambda: list(bucket),
                interface.get_data,
            )
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(HistoricalReadNotSupportedError):
                        operation()

        planner.assert_not_called()
        dependency.assert_not_called()
        observability.assert_not_called()
        transport.assert_not_called()

    def test_direct_request_plan_execution_uses_historical_read_and_mutation_guards(
        self,
    ) -> None:
        interface_cls = self.RemoteProject.Interface
        query_plan = self.RemoteProject.all().request_plan
        self.assertIsNotNone(query_plan)
        mutation_plan = RequestPlan(
            operation_name="create",
            action="create",
            method="POST",
            path="/internal/gm/projects",
        )
        request_transport = interface_cls.transport
        self.assertIsNotNone(request_transport)

        with (
            patch.object(interface_cls, "get_query_operation") as query_operation,
            patch.object(interface_cls, "get_mutation_operation") as mutation_operation,
            patch.object(request_transport, "execute") as execute,
            as_of("2022-01-01"),
        ):
            with self.assertRaises(HistoricalReadNotSupportedError):
                interface_cls.execute_request_plan(query_plan)
            with self.assertRaises(HistoricalMutationError):
                interface_cls.execute_request_plan(mutation_plan)

        query_operation.assert_not_called()
        mutation_operation.assert_not_called()
        execute.assert_not_called()

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
        @cached(cache="dependency")
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
            accept = await asyncio.wait_for(communicator.receive_output(), timeout=5)
            assert accept["type"] == "websocket.accept"

            await sync_to_async(self.project.update)(
                ignore_permission=True,
                status="inactive",
            )
            message = await asyncio.wait_for(communicator.receive_output(), timeout=5)
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

    def test_websocket_invalidation_accepts_additional_query_parameters(self) -> None:
        async def run_test() -> None:
            communicator = ApplicationCommunicator(
                application,
                {
                    "type": "websocket",
                    "path": "/internal/gm/ws/projects",
                    "query_string": b"foo=bar&version=v1",
                    "headers": [],
                    "subprotocols": [],
                },
            )
            await communicator.send_input({"type": "websocket.connect"})
            accept = await communicator.receive_output()
            assert accept["type"] == "websocket.accept"
            await communicator.send_input(
                {"type": "websocket.disconnect", "code": 1000}
            )
            await communicator.wait()

        asyncio.run(run_test())

    def test_remote_invalidation_client_can_invalidate_cached_remote_queries(
        self,
    ) -> None:
        @cached(cache="dependency")
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
