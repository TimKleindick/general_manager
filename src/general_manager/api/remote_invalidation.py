"""Websocket invalidation events for opt-in RemoteAPI managers."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
import json
from importlib import import_module
import re
from uuid import UUID, uuid4
from typing import Any, TYPE_CHECKING, cast

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
    from channels.layers import BaseChannelLayer  # type: ignore[import-untyped]
    from general_manager.manager.general_manager import GeneralManager

logger = get_logger("api.remote.ws")


def _json_safe_identification_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _json_safe_identification(identification: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _json_safe_identification_value(value)
        for key, value in identification.items()
    }


def remote_invalidation_group_name(config: RemoteAPIConfig) -> str:
    base = config.base_path.strip("/").replace("/", ".")
    return f"gm.remote.{base}.{config.resource_name}"


def _get_channel_layer_safe() -> "BaseChannelLayer | None":
    try:
        from channels.layers import get_channel_layer  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - optional dependency
        return None
    return cast("BaseChannelLayer | None", get_channel_layer())


def emit_remote_invalidation(
    sender: type["GeneralManager"],
    *,
    instance: "GeneralManager | None" = None,
    action: str,
    **_: Any,
) -> None:
    config = get_remote_api_config(sender)
    if config is None or not config.websocket_invalidation:
        return
    channel_layer = _get_channel_layer_safe()
    if channel_layer is None:
        return
    payload = {
        "type": "gm.remote.invalidation",
        "protocol_version": config.protocol_version,
        "base_path": config.base_path,
        "resource_name": config.resource_name,
        "action": action,
        "identification": _json_safe_identification(dict(instance.identification))
        if instance is not None
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
    def as_asgi(cls):
        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            consumer = cls()
            await consumer(scope, receive, send)

        return app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        from general_manager.manager.meta import GeneralManagerMeta

        url_kwargs = scope.get("url_route", {}).get("kwargs", {})
        base_path = "/" + str(url_kwargs["base_path"]).strip("/")
        resource_name = str(url_kwargs["resource_name"])
        protocol_version = scope.get("query_string", b"").decode("utf-8")
        manager_configs = build_remote_api_registry(
            cast(list[type["GeneralManager"]], GeneralManagerMeta.all_classes)
        )
        config = manager_configs.get((base_path, resource_name))
        if config is None or not config.websocket_invalidation:
            await send({"type": "websocket.close", "code": 4404})
            return
        if (
            protocol_version
            and protocol_version != f"version={config.protocol_version}"
        ):
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
        receive_task = asyncio.create_task(receive())
        event_task = asyncio.create_task(channel_layer.receive(self._channel_name))
        try:
            while True:
                done, _ = await asyncio.wait(
                    {receive_task, event_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                disconnect = False
                if receive_task in done:
                    message = receive_task.result()
                    message_type = message["type"]
                    if message_type == "websocket.disconnect":
                        disconnect = True
                    else:
                        receive_task = asyncio.create_task(receive())
                if event_task in done:
                    event = event_task.result()
                    if event.get("type") == "gm.remote.invalidation":
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
    asgi_path = getattr(settings, "ASGI_APPLICATION", None)
    if not asgi_path:
        return
    try:
        module_path, attr_name = asgi_path.rsplit(".", 1)
    except ValueError:
        return
    try:
        from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
        from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - optional dependency
        return
    try:
        asgi_module = import_module(module_path)
    except RuntimeError as exc:
        if "populate() isn't reentrant" in str(exc):
            return
        raise
    websocket_patterns = getattr(asgi_module, "websocket_urlpatterns", None)
    if websocket_patterns is None:
        websocket_patterns = []
        cast(Any, asgi_module).websocket_urlpatterns = websocket_patterns
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
            RemoteInvalidationConsumer.as_asgi(),
            kwargs={
                "base_path": config.base_path.strip("/"),
                "resource_name": config.resource_name,
            },
        )
        websocket_route._general_manager_remote_ws = True
        websocket_route._general_manager_remote_ws_key = (
            config.base_path,
            config.resource_name,
        )
        websocket_patterns.append(websocket_route)
    application = getattr(asgi_module, attr_name, None)
    if application is None:
        return
    if hasattr(application, "application_mapping") and isinstance(
        application.application_mapping, dict
    ):
        application.application_mapping["websocket"] = AuthMiddlewareStack(
            URLRouter(list(websocket_patterns))
        )
    else:
        cast(Any, asgi_module).application = ProtocolTypeRouter(
            {
                "http": application,
                "websocket": AuthMiddlewareStack(URLRouter(list(websocket_patterns))),
            }
        )


def clear_remote_invalidation_routes() -> None:
    asgi_path = getattr(settings, "ASGI_APPLICATION", None)
    if not asgi_path:
        return
    try:
        module_path, attr_name = asgi_path.rsplit(".", 1)
    except ValueError:
        return
    try:
        from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
        from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - optional dependency
        return
    try:
        asgi_module = import_module(module_path)
    except ImportError:
        return
    websocket_patterns = getattr(asgi_module, "websocket_urlpatterns", None)
    if websocket_patterns is None:
        return
    websocket_patterns = [
        route
        for route in websocket_patterns
        if not (
            getattr(route, "_general_manager_remote_ws", False)
            or getattr(route, "_general_manager_remote_ws_key", None) is not None
        )
    ]
    cast(Any, asgi_module).websocket_urlpatterns = websocket_patterns
    application = getattr(asgi_module, attr_name, None)
    if application is None:
        return
    if hasattr(application, "application_mapping") and isinstance(
        application.application_mapping, dict
    ):
        application.application_mapping["websocket"] = AuthMiddlewareStack(
            URLRouter(list(websocket_patterns))
        )
    else:
        cast(Any, asgi_module).application = ProtocolTypeRouter(
            {
                "http": application,
                "websocket": AuthMiddlewareStack(URLRouter(list(websocket_patterns))),
            }
        )
