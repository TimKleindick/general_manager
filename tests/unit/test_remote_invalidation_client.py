from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import ClassVar
from unittest import TestCase
from unittest.mock import patch

from general_manager.api import RemoteInvalidationClient
from general_manager.api.remote_invalidation_client import (
    RemoteInvalidationConfigurationError,
)
from general_manager.interface import RequestInterface, RemoteManagerInterface
from general_manager.interface.requests import RequestField, RequestQueryOperation
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input


class FakeWebSocketConnection:
    def __init__(self, payload: Mapping[str, object]) -> None:
        self.payload = dict(payload)
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def recv(self) -> str:
        return json.dumps(self.payload)

    async def close(self) -> None:
        self.closed = True


class BytesWebSocketConnection(FakeWebSocketConnection):
    async def recv(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class InvalidPayloadConnection(FakeWebSocketConnection):
    async def recv(self) -> str:
        return "[]"


class RemoteProject(GeneralManager):
    class Interface(RemoteManagerInterface):
        id = Input(type=int)
        name = RequestField(str)

        class Meta:
            base_url = "https://projects.example.test"
            base_path = "/remote"
            remote_manager = "projects"
            websocket_invalidation_enabled = True


class RemoteProjectReplica(GeneralManager):
    class Interface(RemoteManagerInterface):
        id = Input(type=int)
        name = RequestField(str)

        class Meta:
            base_url = "https://projects.example.test"
            base_path = "/remote"
            remote_manager = "projects"
            websocket_invalidation_enabled = True


class RemoteTaskNoWebsocket(GeneralManager):
    class Interface(RemoteManagerInterface):
        id = Input(type=int)
        name = RequestField(str)

        class Meta:
            base_url = "https://tasks.example.test"
            base_path = "/remote"
            remote_manager = "tasks"


class RemoteInvalidationClientTests(TestCase):
    def test_rejects_empty_manager_list(self) -> None:
        with self.assertRaises(RemoteInvalidationConfigurationError):
            RemoteInvalidationClient([])

    def test_rejects_non_remote_manager_classes(self) -> None:
        class LocalManager(GeneralManager):
            class Interface(RequestInterface):
                name = RequestField(str)

                class Meta:
                    query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                        "detail": RequestQueryOperation(
                            name="detail",
                            method="GET",
                            path="/local/{id}",
                        ),
                        "list": RequestQueryOperation(
                            name="list",
                            method="GET",
                            path="/local",
                            collection=True,
                        ),
                    }

        with self.assertRaises(RemoteInvalidationConfigurationError):
            RemoteInvalidationClient([LocalManager])

    def test_rejects_remote_managers_without_websocket_support(self) -> None:
        with self.assertRaises(RemoteInvalidationConfigurationError):
            RemoteInvalidationClient([RemoteTaskNoWebsocket])

    def test_deduplicates_connections_for_managers_sharing_the_same_url(self) -> None:
        payload = {
            "protocol_version": "v1",
            "base_path": "/remote",
            "resource_name": "projects",
            "action": "update",
            "identification": {"id": 1},
            "event_id": "evt-1",
        }
        connections: list[FakeWebSocketConnection] = []

        def factory(url: str) -> FakeWebSocketConnection:
            self.assertEqual(
                url,
                "wss://projects.example.test/remote/ws/projects?version=v1",
            )
            connection = FakeWebSocketConnection(payload)
            connections.append(connection)
            return connection

        async def run_test() -> None:
            client = RemoteInvalidationClient(
                [RemoteProject, RemoteProjectReplica],
                connection_factory=factory,
            )
            with (
                patch.object(
                    RemoteProject.Interface,
                    "handle_invalidation_event",
                    return_value=True,
                ) as handle_project,
                patch.object(
                    RemoteProjectReplica.Interface,
                    "handle_invalidation_event",
                    return_value=True,
                ) as handle_replica,
            ):
                await client.connect()
                handled = await client.listen_once()
                await client.close()

            self.assertEqual(handled, 2)
            self.assertEqual(len(connections), 1)
            self.assertTrue(connections[0].connected)
            self.assertTrue(connections[0].closed)
            handle_project.assert_called_once_with(payload)
            handle_replica.assert_called_once_with(payload)

        asyncio.run(run_test())

    def test_listen_once_dispatches_all_completed_tasks(self) -> None:
        payload = {
            "protocol_version": "v1",
            "base_path": "/remote",
            "resource_name": "projects",
            "action": "update",
            "identification": {"id": 1},
            "event_id": "evt-1",
        }
        urls: list[str] = []

        class RemoteTask(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                name = RequestField(str)

                class Meta:
                    base_url = "https://projects.example.test"
                    base_path = "/remote"
                    remote_manager = "tasks"
                    websocket_invalidation_enabled = True

        def factory(url: str) -> FakeWebSocketConnection:
            urls.append(url)
            return FakeWebSocketConnection(payload)

        async def run_test() -> None:
            client = RemoteInvalidationClient(
                [RemoteProject, RemoteTask],
                connection_factory=factory,
            )
            with (
                patch.object(
                    RemoteProject.Interface,
                    "handle_invalidation_event",
                    return_value=True,
                ) as handle_project,
                patch.object(
                    RemoteTask.Interface,
                    "handle_invalidation_event",
                    return_value=True,
                ) as handle_task,
            ):
                await client.connect()
                handled = await client.listen_once()
                await client.close()

            self.assertEqual(handled, 2)
            self.assertEqual(
                set(urls),
                {
                    "wss://projects.example.test/remote/ws/projects?version=v1",
                    "wss://projects.example.test/remote/ws/tasks?version=v1",
                },
            )
            handle_project.assert_called_once_with(payload)
            handle_task.assert_called_once_with(payload)

        asyncio.run(run_test())

    def test_recv_event_accepts_bytes_payload(self) -> None:
        payload = {
            "protocol_version": "v1",
            "base_path": "/remote",
            "resource_name": "projects",
            "action": "update",
            "identification": {"id": 1},
            "event_id": "evt-1",
        }

        async def run_test() -> None:
            client = RemoteInvalidationClient(
                [RemoteProject],
                connection_factory=lambda _url: BytesWebSocketConnection(payload),
            )
            await client.connect()
            handled = await client.listen_once()
            await client.close()

            self.assertEqual(handled, 1)

        asyncio.run(run_test())

    def test_listen_once_closes_connections_and_reraises_on_invalid_payload(
        self,
    ) -> None:
        payload = {
            "protocol_version": "v1",
            "base_path": "/remote",
            "resource_name": "projects",
            "action": "update",
            "identification": {"id": 1},
            "event_id": "evt-1",
        }
        connections: list[FakeWebSocketConnection] = []

        class RemoteTask(GeneralManager):
            class Interface(RemoteManagerInterface):
                id = Input(type=int)
                name = RequestField(str)

                class Meta:
                    base_url = "https://projects.example.test"
                    base_path = "/remote"
                    remote_manager = "tasks"
                    websocket_invalidation_enabled = True

        def factory(url: str) -> FakeWebSocketConnection:
            if url.endswith("/projects?version=v1"):
                connection = InvalidPayloadConnection(payload)
            else:
                connection = FakeWebSocketConnection(payload)
            connections.append(connection)
            return connection

        async def run_test() -> None:
            client = RemoteInvalidationClient(
                [RemoteProject, RemoteTask],
                connection_factory=factory,
            )
            await client.connect()
            with self.assertRaises(RemoteInvalidationConfigurationError):
                await client.listen_once()
            self.assertTrue(all(connection.closed for connection in connections))

        asyncio.run(run_test())

    def test_listen_once_returns_zero_after_close(self) -> None:
        async def run_test() -> None:
            client = RemoteInvalidationClient(
                [RemoteProject],
                connection_factory=lambda _url: FakeWebSocketConnection({}),
            )
            await client.close()

            self.assertEqual(await client.listen_once(), 0)

        asyncio.run(run_test())
