"""Runtime websocket invalidation client for RemoteManagerInterface managers."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, cast

from general_manager.interface.interfaces.remote_manager import RemoteManagerInterface
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager

logger = get_logger("api.remote.ws_client")


class RemoteInvalidationConfigurationError(ValueError):
    """Raised when the remote invalidation client cannot be configured or used.

    The error covers missing websocket client support, invalid connection
    factories, empty manager lists, managers without a remote interface, disabled
    websocket invalidation, and runtime websocket payloads that do not decode to
    a JSON object.
    """

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

    async def recv(self) -> object: ...

    async def close(self) -> None: ...


type ConnectionFactory = Callable[
    [str],
    _WebSocketConnection | Awaitable[_WebSocketConnection],
]


async def _default_connection_factory(url: str) -> _WebSocketConnection:
    connect: Callable[[str], Awaitable[object]] | None = None
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
            connect = cast(Callable[[str], Awaitable[object]], candidate)
            break
    if connect is None:
        raise RemoteInvalidationConfigurationError.missing_websocket_client()
    connection = await connect(url)
    if not hasattr(connection, "recv") or not hasattr(connection, "close"):
        raise RemoteInvalidationConfigurationError.invalid_connection_factory()
    return cast(_WebSocketConnection, connection)


class RemoteInvalidationClient:
    """Listen for websocket invalidation events and clear remote-manager caches.

    Args:
        manager_classes: Non-empty sequence of ``GeneralManager`` subclasses
            whose bound interface subclasses ``RemoteManagerInterface`` and has
            websocket invalidation enabled.
        connection_factory: Optional callable receiving a websocket URL and
            returning, or resolving to, an object with async ``recv()`` and
            ``close()`` methods. If it also exposes async ``connect()``,
            :meth:`connect` calls it before listening. The structural
            connection protocol is intentionally documented here rather than
            exported as a public type.
        reconnect_delay: Delay in seconds between background listener reconnect
            attempts after a listener error.

    Raises:
        RemoteInvalidationConfigurationError: If no managers are supplied, a
            manager is not backed by an enabled ``RemoteManagerInterface``, the
            default websocket client cannot be imported, a connection object does
            not expose ``recv``/``close``, or a received payload does not decode
            to an object. Runtime non-object payloads intentionally use this
            same configuration error class for compatibility.

    For each manager class, the client reads ``_interface`` first and then
    ``Interface`` to find the bound interface. Websocket URLs come from
    ``interface_cls.get_websocket_invalidation_url()``, and websocket support is
    checked through ``interface_cls.websocket_invalidation_enabled``. The client
    groups managers by websocket URL and deduplicates interface classes by class
    identity within each URL, preserving the first occurrence order from
    ``manager_classes``. Decoded invalidation payloads are dispatched to each
    interface's synchronous ``handle_invalidation_event(...)`` method in that
    stored interface order. The payload is the decoded mapping as received from
    the websocket; event field semantics are owned by
    ``RemoteManagerInterface.handle_invalidation_event(...)``.
    ``listen_once()`` waits for the first completed receive task across the
    configured URLs, cancels pending receive tasks, and gathers those pending
    tasks with ``return_exceptions=True``. When any completed task raises, it
    closes cached connections for every configured URL and re-raises that
    exception; if more than one task completed, ``asyncio.gather`` determines
    which exception is raised. Handler exceptions are not caught by the client.
    Handlers are called synchronously; awaitable handler returns are not awaited.
    The ``listen_once()`` return count intentionally uses normal truthiness, so a
    handler that returns ``None`` is not counted. ``run()`` is intended as a long-running
    coroutine. It starts persistent listener tasks and always calls ``close()``
    in its ``finally`` block when the gather exits, including external
    cancellation. It can create websocket connections lazily, but it does not
    call optional connection ``connect()`` hooks itself. This is intentional:
    call :meth:`connect` before :meth:`run` when a connection object needs that
    hook. ``close()`` is
    safe to call repeatedly during normal shutdown: it marks the client closed,
    cancels any current listener tasks, and closes any cached connections. It
    does not directly cancel one-off ``listen_once()`` receive tasks that are
    already waiting; those tasks finish according to their connection's
    receive/close behavior.
    """

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
                type[object] | None,
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
        return connection

    async def _get_connection(self, url: str) -> _WebSocketConnection:
        connection = self._connections.get(url)
        if connection is None:
            connection = await self._connect(url)
            self._connections[url] = connection
        return connection

    async def connect(self) -> None:
        """Open cached websocket connections and call optional connection hooks."""

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

    async def _recv_event(self, url: str) -> tuple[str, Mapping[str, object]]:
        connection = await self._get_connection(url)
        raw_message = await connection.recv()
        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode("utf-8")
        if isinstance(raw_message, str):
            payload = json.loads(raw_message)
        else:
            payload = raw_message
        if not isinstance(payload, Mapping):
            raise RemoteInvalidationConfigurationError.invalid_payload()
        return url, cast(Mapping[str, object], payload)

    def _dispatch(self, url: str, event: Mapping[str, object]) -> int:
        handled = 0
        for interface_cls in self._interfaces_by_url[url]:
            if interface_cls.handle_invalidation_event(event):
                handled += 1
        return handled

    async def listen_once(self) -> int:
        """Receive and dispatch a single invalidation event.

        Returns:
            Number of configured interfaces whose
            ``handle_invalidation_event(...)`` method returned ``True``.

        Raises:
            RemoteInvalidationConfigurationError: If a received payload is not a
                JSON object or decoded mapping.
            json.JSONDecodeError: If a string/bytes websocket message is not
                valid UTF-8 JSON. Bytes are decoded with UTF-8 before JSON
                parsing.
            UnicodeDecodeError: If a bytes websocket message is not valid UTF-8.
            Exception: Propagates connection factory, receive, close, and
                interface dispatch errors.
        """

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
        """Run persistent listener tasks until cancelled, closed, or failed."""

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
        """Cancel listener tasks and close all cached websocket connections."""

        self._closed = True
        tasks = list(self._listener_tasks.values())
        self._listener_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for url in list(self._connections):
            await self._close_connection(url)
