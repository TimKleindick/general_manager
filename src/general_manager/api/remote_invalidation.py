"""Websocket invalidation events for opt-in RemoteAPI managers."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
import json
from importlib import import_module
import re
from uuid import UUID, uuid4
from collections.abc import Callable, Coroutine, Mapping, MutableSequence, Sequence
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast
from urllib.parse import parse_qs

from asgiref.sync import async_to_sync
from django.conf import settings
from django.urls import re_path

from general_manager.api.remote_api import (
    RemoteAPIConfig,
    build_remote_api_registry,
    get_remote_api_config,
)
from general_manager.cache.signals import post_data_change
from general_manager.logging import get_logger

if TYPE_CHECKING:
    from channels.layers import BaseChannelLayer
    from general_manager.manager.general_manager import GeneralManager

logger = get_logger("api.remote.ws")
JSONValue: TypeAlias = (
    None | str | int | float | bool | list["JSONValue"] | dict[str, "JSONValue"]
)
IdentificationPayload: TypeAlias = Mapping[str, object]
RemoteInvalidationPayload: TypeAlias = dict[str, JSONValue]
AsgiMessage: TypeAlias = Mapping[str, object]
AsgiReceive: TypeAlias = Callable[[], Coroutine[object, object, AsgiMessage]]
AsgiSend: TypeAlias = Callable[[Mapping[str, object]], Coroutine[object, object, None]]
AsgiApp: TypeAlias = Callable[
    [Mapping[str, object], AsgiReceive, AsgiSend],
    Coroutine[object, object, None],
]


class _ApplicationMapping(Protocol):
    """Channels protocol router shape used for websocket route installation."""

    application_mapping: dict[str, object]


class _AsgiModuleRoutes(Protocol):
    """ASGI module route attribute managed by remote invalidation wiring."""

    websocket_urlpatterns: MutableSequence[object]


class _RouteMarker(Protocol):
    """Dynamic marker attributes attached to generated URL patterns."""

    _general_manager_remote_ws: bool
    _general_manager_remote_ws_key: tuple[str, str]


def _json_safe_identification_value(value: object) -> JSONValue:
    """Return a JSON-compatible representation of an identification value."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _json_safe_identification(
    identification: IdentificationPayload,
) -> dict[str, JSONValue]:
    """Return a JSON-compatible identification mapping."""
    return {
        key: _json_safe_identification_value(value)
        for key, value in identification.items()
    }


def remote_invalidation_group_name(config: RemoteAPIConfig) -> str:
    """
    Return the channel-layer group name for a RemoteAPI websocket resource.

    The group name is `gm.remote.<base-path>.<resource-name>`, where slashes in
    `config.base_path` are stripped and converted to dots. The helper has no side
    effects and does not check whether websocket invalidation is enabled.
    Repeated internal slashes produce repeated dots, an empty base path produces
    an empty group segment, and Channels group-name length/character constraints
    are not enforced here. Attribute errors from malformed config objects
    propagate.
    """
    base = config.base_path.strip("/").replace("/", ".")
    return f"gm.remote.{base}.{config.resource_name}"


def _get_channel_layer_safe() -> "BaseChannelLayer | None":
    try:
        from channels.layers import get_channel_layer
    except ImportError:  # pragma: no cover - optional dependency
        return None
    return cast("BaseChannelLayer | None", get_channel_layer())


def emit_remote_invalidation(
    sender: type["GeneralManager"],
    *,
    instance: "GeneralManager | None" = None,
    identification: IdentificationPayload | None = None,
    action: str,
    **_: object,
) -> None:
    """
    Emit one websocket invalidation event for a RemoteAPI-enabled manager.

    Returns without sending when the manager has no RemoteAPI config, websocket
    invalidation is disabled, or Channels has no configured channel layer.
    Identification is taken from the explicit mapping first, then from
    `instance.identification` when an instance is provided; missing
    identification is sent as `None`. The `action` string is forwarded without
    validation. UUID, date, and datetime identification values are serialized to
    strings, and other non-JSON values fall back to `str(value)`.

    The synchronous signal receiver sends one channel-layer payload through
    `async_to_sync(channel_layer.group_send)`. Payload fields are `type`
    (`"gm.remote.invalidation"`), `protocol_version`, `base_path`,
    `resource_name`, `action`, `identification`, and `event_id` as a UUID4
    string. Identification serialization is shallow; nested lists or mappings
    fall back to `str(value)`. Missing `instance.identification`, pathological
    `str(value)` errors, and channel-layer send errors propagate.
    """
    config = get_remote_api_config(sender)
    if config is None or not config.websocket_invalidation:
        return
    channel_layer = _get_channel_layer_safe()
    if channel_layer is None:
        return
    event_identification = identification
    if event_identification is None and instance is not None:
        event_identification = dict(instance.identification)
    payload: RemoteInvalidationPayload = {
        "type": "gm.remote.invalidation",
        "protocol_version": config.protocol_version,
        "base_path": config.base_path,
        "resource_name": config.resource_name,
        "action": action,
        "identification": _json_safe_identification(event_identification)
        if event_identification is not None
        else None,
        "event_id": str(uuid4()),
    }
    async_to_sync(channel_layer.group_send)(
        remote_invalidation_group_name(config),
        payload,
    )


post_data_change.connect(emit_remote_invalidation, weak=False)


class RemoteInvalidationConsumer:
    """Minimal ASGI websocket consumer for remote invalidation events."""

    def __init__(self) -> None:
        self._group_name: str | None = None
        self._channel_layer: BaseChannelLayer | None = None
        self._channel_name: str | None = None

    @classmethod
    def as_asgi(cls) -> AsgiApp:
        """Return an ASGI application callable for remote invalidation events."""

        async def app(
            scope: Mapping[str, object],
            receive: AsgiReceive,
            send: AsgiSend,
        ) -> None:
            consumer = cls()
            await consumer(scope, receive, send)

        return app

    async def __call__(
        self,
        scope: Mapping[str, object],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        """
        Accept a remote invalidation websocket and forward channel-layer events.

        The consumer closes with 4404 for unknown or disabled resources, 4406
        for protocol-version mismatches, and 1011 when no channel layer is
        available. It accepts matching connections, subscribes to the resource
        group, sends JSON invalidation payloads, and discards the channel in a
        `finally` block. The generated route supplies `scope["url_route"]
        ["kwargs"]["base_path"]` and `["resource_name"]`; the optional
        `version` query string parameter is compared with the resource protocol
        version. A missing version is accepted, multiple values use the first
        parsed value, and invalid UTF-8 query strings propagate decoding errors.
        Non-disconnect inbound websocket messages are ignored while the consumer
        keeps listening.

        Outbound JSON messages include `protocol_version`, `base_path`,
        `resource_name`, `action`, `identification`, and `event_id` in a
        `websocket.send` frame with a JSON `text` payload. Extra channel-layer
        event fields are ignored; missing required event fields raise `KeyError`.
        Client disconnect exits the loop and cleans up without raising. Missing
        route kwargs, channel-layer, receive, send, and JSON errors propagate.
        """
        from general_manager.manager.meta import GeneralManagerMeta

        url_route = scope.get("url_route", {})
        url_kwargs = (
            url_route.get("kwargs", {}) if isinstance(url_route, Mapping) else {}
        )
        base_path = "/" + str(url_kwargs["base_path"]).strip("/")
        resource_name = str(url_kwargs["resource_name"])
        query_string = scope.get("query_string", b"")
        query_params = (
            parse_qs(query_string.decode("utf-8"))
            if isinstance(query_string, bytes) and query_string
            else {}
        )
        protocol_version = query_params.get("version", [None])[0]
        manager_configs = build_remote_api_registry(GeneralManagerMeta.all_classes)
        config = manager_configs.get((base_path, resource_name))
        if config is None or not config.websocket_invalidation:
            await send({"type": "websocket.close", "code": 4404})
            return
        if protocol_version and protocol_version != config.protocol_version:
            await send({"type": "websocket.close", "code": 4406})
            return
        channel_layer = _get_channel_layer_safe()
        if channel_layer is None:
            await send({"type": "websocket.close", "code": 1011})
            return
        self._channel_layer = channel_layer
        self._group_name = remote_invalidation_group_name(config)
        self._channel_name = await channel_layer.new_channel()
        await channel_layer.group_add(self._group_name, self._channel_name)
        await send({"type": "websocket.accept"})
        receive_task: asyncio.Task[AsgiMessage] = asyncio.create_task(receive())
        event_task: asyncio.Task[object] = asyncio.create_task(
            channel_layer.receive(self._channel_name)
        )
        try:
            while True:
                done, _ = await asyncio.wait(
                    {receive_task, event_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                disconnect = False
                if receive_task in done:
                    message = receive_task.result()
                    message_type = message.get("type")
                    if message_type == "websocket.disconnect":
                        disconnect = True
                    else:
                        receive_task = asyncio.create_task(receive())
                if event_task in done:
                    event = event_task.result()
                    if (
                        not disconnect
                        and isinstance(event, Mapping)
                        and event.get("type") == "gm.remote.invalidation"
                    ):
                        await send(
                            {
                                "type": "websocket.send",
                                "text": json.dumps(
                                    {
                                        "protocol_version": event["protocol_version"],
                                        "base_path": event["base_path"],
                                        "resource_name": event["resource_name"],
                                        "action": event["action"],
                                        "identification": event["identification"],
                                        "event_id": event["event_id"],
                                    }
                                ),
                            }
                        )
                    if not disconnect:
                        event_task = asyncio.create_task(
                            channel_layer.receive(self._channel_name)
                        )
                if disconnect:
                    event_task.cancel()
                    await asyncio.gather(event_task, return_exceptions=True)
                    break
        finally:
            receive_task.cancel()
            event_task.cancel()
            await asyncio.gather(receive_task, event_task, return_exceptions=True)
            if self._group_name is not None and self._channel_name is not None:
                await channel_layer.group_discard(self._group_name, self._channel_name)


def ensure_remote_invalidation_route(
    manager_classes: list[type["GeneralManager"]],
) -> None:
    """
    Install RemoteAPI websocket invalidation routes into the configured ASGI app.

    Returns without changes when `ASGI_APPLICATION` is missing, does not contain
    a dotted module attribute, Channels is unavailable, the ASGI module is
    unavailable during reentrant population,
    or no manager has websocket invalidation enabled. Existing generated routes
    for the same RemoteAPI resource are preserved. Import errors other than
    reentrant population and router construction errors propagate.

    Generated paths are `<base_path>/ws/<resource_name>/?` with an optional
    trailing slash. The helper is idempotent for the same `(base_path,
    resource_name)` pair using the resolved config values without additional
    normalization; changed config for an existing key is preserved unchanged.
    Routes append after existing websocket patterns. Missing or non-mutable
    `websocket_urlpatterns` are replaced with a new list. The helper supports
    ASGI modules that expose either a Channels `application_mapping` router or a
    plain HTTP application attribute named by `ASGI_APPLICATION`.
    """
    asgi_path = getattr(settings, "ASGI_APPLICATION", None)
    if not asgi_path:
        return
    try:
        module_path, attr_name = asgi_path.rsplit(".", 1)
    except ValueError:
        return
    try:
        from channels.auth import AuthMiddlewareStack
        from channels.routing import ProtocolTypeRouter, URLRouter
    except ImportError:  # pragma: no cover - optional dependency
        return
    try:
        asgi_module = import_module(module_path)
    except RuntimeError as exc:
        if "populate() isn't reentrant" in str(exc):
            return
        raise
    websocket_patterns = _websocket_patterns(asgi_module)
    if websocket_patterns is None:
        websocket_patterns = []
        cast(_AsgiModuleRoutes, asgi_module).websocket_urlpatterns = websocket_patterns
    application = getattr(asgi_module, attr_name, None)
    if application is None:
        return
    for route in _websocket_application_routes(application):
        if route not in websocket_patterns:
            websocket_patterns.append(route)
    registry = build_remote_api_registry(manager_classes)
    for config in registry.values():
        if not config.websocket_invalidation:
            continue
        normalized = f"{config.base_path.strip('/')}/ws/{config.resource_name}"
        pattern = rf"^{re.escape(normalized)}/?$"
        route_exists = any(
            getattr(route, "_general_manager_remote_ws_key", None)
            == (config.base_path, config.resource_name)
            for route in websocket_patterns
        )
        if route_exists:
            continue
        websocket_route = re_path(
            pattern,
            cast(
                Callable[..., Coroutine[object, object, None]],
                RemoteInvalidationConsumer.as_asgi(),
            ),
            kwargs={
                "base_path": config.base_path.strip("/"),
                "resource_name": config.resource_name,
            },
        )
        route_marker = cast(_RouteMarker, websocket_route)
        route_marker._general_manager_remote_ws = True
        route_marker._general_manager_remote_ws_key = (
            config.base_path,
            config.resource_name,
        )
        websocket_patterns.append(websocket_route)
    if hasattr(application, "application_mapping"):
        mapped_application = cast(_ApplicationMapping, application)
        mapped_application.application_mapping["websocket"] = AuthMiddlewareStack(
            URLRouter(list(websocket_patterns))
        )
    else:
        setattr(
            asgi_module,
            attr_name,
            ProtocolTypeRouter(
                {
                    "http": application,
                    "websocket": AuthMiddlewareStack(
                        URLRouter(list(websocket_patterns))
                    ),
                }
            ),
        )


def clear_remote_invalidation_routes() -> None:
    """
    Remove generated RemoteAPI websocket invalidation routes from the ASGI app.

    GraphQL or user-defined websocket routes are preserved. Generated routes are
    detected by the `_general_manager_remote_ws` marker or the
    `_general_manager_remote_ws_key` marker installed by
    `ensure_remote_invalidation_route()`. All generated RemoteAPI invalidation
    routes in the configured ASGI module are removed regardless of path or
    resource shape while preserving the order of remaining routes. Returns
    without changes when ASGI settings, Channels, the ASGI module, or mutable
    sequence websocket patterns are unavailable. Router reconstruction errors
    propagate.
    """
    asgi_path = getattr(settings, "ASGI_APPLICATION", None)
    if not asgi_path:
        return
    try:
        module_path, attr_name = asgi_path.rsplit(".", 1)
    except ValueError:
        return
    try:
        from channels.auth import AuthMiddlewareStack
        from channels.routing import ProtocolTypeRouter, URLRouter
    except ImportError:  # pragma: no cover - optional dependency
        return
    try:
        asgi_module = import_module(module_path)
    except ImportError:
        return
    websocket_patterns = _websocket_patterns(asgi_module)
    if websocket_patterns is None:
        return
    filtered_patterns = [
        route
        for route in websocket_patterns
        if not (
            getattr(route, "_general_manager_remote_ws", False)
            or getattr(route, "_general_manager_remote_ws_key", None) is not None
        )
    ]
    cast(_AsgiModuleRoutes, asgi_module).websocket_urlpatterns = filtered_patterns
    application = getattr(asgi_module, attr_name, None)
    if application is None:
        return
    if hasattr(application, "application_mapping"):
        mapped_application = cast(_ApplicationMapping, application)
        mapped_application.application_mapping["websocket"] = AuthMiddlewareStack(
            URLRouter(list(filtered_patterns))
        )
    else:
        setattr(
            asgi_module,
            attr_name,
            ProtocolTypeRouter(
                {
                    "http": application,
                    "websocket": AuthMiddlewareStack(
                        URLRouter(list(filtered_patterns))
                    ),
                }
            ),
        )


def _websocket_patterns(
    asgi_module: ModuleType,
) -> MutableSequence[object] | None:
    """Return appendable websocket URL patterns from an ASGI module."""
    value = getattr(asgi_module, "websocket_urlpatterns", None)
    if value is None:
        return None
    if not isinstance(value, MutableSequence):
        return None
    return value


def _websocket_application_routes(application: object) -> list[object]:
    """Return routes already installed in a Channels websocket router."""
    if not hasattr(application, "application_mapping"):
        return []
    websocket_app = cast(_ApplicationMapping, application).application_mapping.get(
        "websocket"
    )
    router = websocket_app
    seen: set[int] = set()
    while router is not None and id(router) not in seen:
        seen.add(id(router))
        routes = getattr(router, "routes", None)
        if isinstance(routes, Sequence) and not isinstance(routes, str | bytes):
            return list(routes)
        router = getattr(router, "inner", None)
    return []
