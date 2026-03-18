"""Runtime websocket invalidation client for RemoteManagerInterface managers."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol, cast

from general_manager.interface.interfaces.remote_manager import RemoteManagerInterface
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager

logger = get_logger("api.remote.ws_client")


class RemoteInvalidationConfigurationError(ValueError):
    """Raised when RemoteInvalidationClient is configured with unsupported managers."""

    @classmethod
    def missing_websocket_client(cls) -> RemoteInvalidationConfigurationError:
        return cls(
            "RemoteInvalidationClient requires an installed websocket client "
            "library or an explicit connection_factory."
        )

    @classmethod
    def invalid_connection_factory(cls) -> RemoteInvalidationConfigurationError:
        return cls("connection_factory must return an object with recv() and close().")

    @classmethod
    def empty_manager_classes(cls) -> RemoteInvalidationConfigurationError:
        return cls("RemoteInvalidationClient requires at least one manager class.")

    @classmethod
    def missing_interface(
        cls, manager_name: str
    ) -> RemoteInvalidationConfigurationError:
        return cls(f"{manager_name} has no bound interface.")

    @classmethod
    def non_remote_manager(
        cls, manager_name: str
    ) -> RemoteInvalidationConfigurationError:
        return cls(f"{manager_name} does not use RemoteManagerInterface.")

    @classmethod
    def websocket_disabled(
        cls, manager_name: str
    ) -> RemoteInvalidationConfigurationError:
        return cls(f"{manager_name} has websocket invalidation disabled.")

    @classmethod
    def invalid_payload(cls) -> RemoteInvalidationConfigurationError:
        return cls("Remote invalidation payloads must decode to an object.")


class _WebSocketConnection(Protocol):
    async def connect(self) -> None: ...

    async def recv(self) -> Any: ...

    async def close(self) -> None: ...


type ConnectionFactory = Callable[
    [str],
    _WebSocketConnection | Awaitable[_WebSocketConnection],
]


async def _default_connection_factory(url: str) -> _WebSocketConnection:
    connect: Callable[[str], Any] | None = None
    for module_path in (
        "websockets.asyncio.client",
        "websockets.client",
        "websockets.legacy.client",
    ):
        try:
            module = __import__(module_path, fromlist=["connect"])
        except ImportError:
            continue
        candidate = getattr(module, "connect", None)
        if callable(candidate):
            connect = cast(Callable[[str], Any], candidate)
            break
    if connect is None:
        raise RemoteInvalidationConfigurationError.missing_websocket_client()
    connection = await connect(url)
    if not hasattr(connection, "recv") or not hasattr(connection, "close"):
        raise RemoteInvalidationConfigurationError.invalid_connection_factory()
    return cast(_WebSocketConnection, connection)


class RemoteInvalidationClient:
    """Listen for websocket invalidation events and clear remote-manager caches."""

    def __init__(
        self,
        manager_classes: Sequence[type[GeneralManager]],
        *,
        connection_factory: ConnectionFactory | None = None,
        reconnect_delay: float = 1.0,
    ) -> None:
        if not manager_classes:
            raise RemoteInvalidationConfigurationError.empty_manager_classes()
        self.connection_factory = connection_factory or _default_connection_factory
        self.reconnect_delay = reconnect_delay
        self._interfaces_by_url = self._normalize_managers(manager_classes)
        self._connections: dict[str, _WebSocketConnection] = {}
        self._listener_tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    @staticmethod
    def _normalize_managers(
        manager_classes: Sequence[type[GeneralManager]],
    ) -> dict[str, list[type[RemoteManagerInterface]]]:
        interfaces_by_url: dict[str, list[type[RemoteManagerInterface]]] = {}
        for manager_cls in manager_classes:
            interface_cls = cast(
                type[Any] | None,
                getattr(manager_cls, "_interface", None)
                or getattr(manager_cls, "Interface", None),
            )
            if interface_cls is None:
                raise RemoteInvalidationConfigurationError.missing_interface(
                    manager_cls.__name__
                )
            if not issubclass(interface_cls, RemoteManagerInterface):
                raise RemoteInvalidationConfigurationError.non_remote_manager(
                    manager_cls.__name__
                )
            if not interface_cls.websocket_invalidation_enabled:
                raise RemoteInvalidationConfigurationError.websocket_disabled(
                    manager_cls.__name__
                )
            url = interface_cls.get_websocket_invalidation_url()
            bucket = interfaces_by_url.setdefault(url, [])
            if interface_cls not in bucket:
                bucket.append(interface_cls)
        return interfaces_by_url

    async def _connect(self, url: str) -> _WebSocketConnection:
        result = self.connection_factory(url)
        connection = await result if inspect.isawaitable(result) else result
        if not hasattr(connection, "recv") or not hasattr(connection, "close"):
            raise RemoteInvalidationConfigurationError.invalid_connection_factory()
        return cast(_WebSocketConnection, connection)

    async def _get_connection(self, url: str) -> _WebSocketConnection:
        connection = self._connections.get(url)
        if connection is None:
            connection = await self._connect(url)
            self._connections[url] = connection
        return connection

    async def connect(self) -> None:
        for url in self._interfaces_by_url:
            connection = await self._get_connection(url)
            connect = getattr(connection, "connect", None)
            if callable(connect):
                await connect()

    async def _close_connection(self, url: str) -> None:
        connection = self._connections.pop(url, None)
        if connection is None:
            return
        await connection.close()

    async def _recv_event(self, url: str) -> tuple[str, Mapping[str, Any]]:
        connection = await self._get_connection(url)
        raw_message = await connection.recv()
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        if isinstance(raw_message, str):
            payload = json.loads(raw_message)
        else:
            payload = raw_message
        if not isinstance(payload, dict):
            raise RemoteInvalidationConfigurationError.invalid_payload()
        return url, cast(Mapping[str, Any], payload)

    def _dispatch(self, url: str, event: Mapping[str, Any]) -> int:
        handled = 0
        for interface_cls in self._interfaces_by_url[url]:
            if interface_cls.handle_invalidation_event(event):
                handled += 1
        return handled

    async def listen_once(self) -> int:
        if self._closed:
            return 0
        tasks = {
            asyncio.create_task(self._recv_event(url)): url
            for url in self._interfaces_by_url
        }
        try:
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            results = await asyncio.gather(*done)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        except Exception:
            for url in tasks.values():
                await self._close_connection(url)
            raise
        else:
            handled = 0
            for url, event in results:
                handled += self._dispatch(url, event)
            return handled

    async def _listen_url(self, url: str) -> None:
        while not self._closed:
            try:
                _, event = await self._recv_event(url)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "remote invalidation websocket listener failed",
                    context={"url": url},
                )
                await self._close_connection(url)
                if self._closed:
                    break
                await asyncio.sleep(self.reconnect_delay)
                continue
            self._dispatch(url, event)

    async def run(self) -> None:
        if self._closed:
            return
        if not self._listener_tasks:
            self._listener_tasks = {
                url: asyncio.create_task(self._listen_url(url))
                for url in self._interfaces_by_url
            }
        try:
            await asyncio.gather(*self._listener_tasks.values())
        finally:
            await self.close()

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._listener_tasks.values())
        self._listener_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for url in list(self._connections):
            await self._close_connection(url)
