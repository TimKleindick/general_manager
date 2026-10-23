"""Websocket invalidation events for opt-in RemoteAPI managers."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime
from dataclasses import replace
import json
from importlib import import_module
import re
from time import perf_counter
from uuid import UUID, uuid4
from collections.abc import Callable, Coroutine, Mapping, MutableSequence, Sequence
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Protocol, TypeAlias, cast
from urllib.parse import parse_qs

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.handlers.asgi import ASGIHandler
from django.db import DEFAULT_DB_ALIAS, transaction
from django.urls import re_path

from general_manager.api.remote_api import (
    RemoteAPIConfig,
    build_remote_api_registry,
    get_remote_api_config,
)
from general_manager.api.notification_batching import _queue_notification
from general_manager.cache.data_change_context import record_data_change_phase
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


def _build_remote_invalidation_payload(
    config: RemoteAPIConfig,
    *,
    action: str,
    identification: IdentificationPayload | None,
    event_id: str,
) -> RemoteInvalidationPayload:
    """Build one protocol-complete RemoteAPI invalidation payload."""
    return {
        "type": "gm.remote.invalidation",
        "protocol_version": config.protocol_version,
        "base_path": config.base_path,
        "resource_name": config.resource_name,
        "action": action,
        "identification": _json_safe_identification(identification)
        if identification is not None
        else None,
        "event_id": event_id,
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


def _publish_remote_invalidation(
    config: RemoteAPIConfig,
    action: str,
    identification: IdentificationPayload | None,
) -> None:
    """Publish or batch a committed RemoteAPI websocket invalidation."""
    channel_layer = _get_channel_layer_safe()
    if channel_layer is None:
        return
    group_name = remote_invalidation_group_name(config)
    event_id = str(uuid4())
    refresh_payload = _build_remote_invalidation_payload(
        config,
        action="refresh",
        identification=None,
        event_id=event_id,
    )
    if _queue_notification(
        key=("remote", group_name, config.protocol_version),
        group_send=channel_layer.group_send,
        group=group_name,
        message=refresh_payload,
    ):
        return

    payload = _build_remote_invalidation_payload(
        config,
        action=action,
        identification=identification,
        event_id=event_id,
    )
    try:
        async_to_sync(channel_layer.group_send)(group_name, payload)
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "failed to dispatch remote invalidation",
            context={
                "base_path": config.base_path,
                "resource_name": config.resource_name,
                "action": action,
                "group": group_name,
            },
            exc_info=exc,
        )


def emit_remote_invalidation(
    sender: type["GeneralManager"],
    *,
    instance: "GeneralManager | None" = None,
    identification: IdentificationPayload | None = None,
    action: str,
    database_alias: str = DEFAULT_DB_ALIAS,
    **_: object,
) -> None:
    """
    Schedule a RemoteAPI websocket invalidation after the data change commits.

    Returns without scheduling when the manager has no enabled websocket
    RemoteAPI configuration. Identification is taken from the explicit mapping
    first, then from `instance.identification` when an instance is provided,
    and deep-copied so transaction callbacks observe the value at signal time.
    The `action` string is forwarded without validation. The callback uses the
    originating database alias and delegates channel lookup, batching, payload
    creation, and delivery to `_publish_remote_invalidation` after commit.
    """
    started = perf_counter()
    try:
        config = get_remote_api_config(sender)
        if config is None or not config.websocket_invalidation:
            return

        event_identification = identification
        if event_identification is None and instance is not None:
            event_identification = dict(instance.identification)
        captured_identification = (
            deepcopy(event_identification) if event_identification is not None else None
        )
        from general_manager.manager.bulk_create import current_create_many_batch

        batch = current_create_many_batch(database_alias)

        def callback() -> None:
            _publish_remote_invalidation(
                config,
                action,
                captured_identification,
            )

        if batch is not None and batch.manager_class is sender:
            batch.notification_callbacks.append(callback)
            return
        transaction.on_commit(
            callback,
            using=database_alias,
        )
    finally:
        record_data_change_phase(
            "subscription", perf_counter() - started, database_alias
        )


post_data_change.connect(emit_remote_invalidation, weak=False)


def _fresh_remote_principal(principal: object) -> object | None:
    """Reload the authenticated account, without reusing cached permission state."""
    if not getattr(principal, "is_authenticated", False):
        return None
    identity = getattr(principal, "pk", None)
    if identity is None:
        return None
    user = get_user_model()._default_manager.filter(pk=identity).first()
    if user is None or not getattr(user, "is_active", True):
        return None
    return user


def _can_receive_remote_event(
    config: RemoteAPIConfig, principal: object, event: Mapping[str, object]
) -> bool:
    """Apply the native current-instance read gate; never disclose deleted IDs."""
    if (
        event.get("action") not in {"create", "update"}
        or event.get("protocol_version") != config.protocol_version
        or event.get("base_path") != config.base_path
        or event.get("resource_name") != config.resource_name
    ):
        return False
    identification = event.get("identification")
    if not isinstance(identification, Mapping) or not identification:
        return False
    from general_manager.interface.interfaces.request import RequestInterface
    from general_manager.api.graphql_errors import get_read_permission_filter
    from general_manager.api.graphql_resolvers import (
        prepare_read_authorization,
        finalize_read_authorization,
    )
    from general_manager.bucket._materialized_bucket import MaterializedBucket
    from general_manager.bucket.base_bucket import Bucket

    try:
        user = _fresh_remote_principal(principal)
        if user is None:
            return False
        instance = config.manager_cls(**dict(identification))
        if isinstance(instance._interface, RequestInterface):
            # Request constructors are lazy. Even a user-only read rule must
            # not authorize an ID whose current detail cannot be loaded.
            instance._interface.set_request_payload_cache(None)
            instance._interface.get_data()
        info = SimpleNamespace(context=SimpleNamespace(user=user))
        plan = get_read_permission_filter(config.manager_cls, info)
        source: Bucket[GeneralManager] = MaterializedBucket(
            config.manager_cls, [instance]
        )
        if plan.decision == "conditional" and any(
            constraint.get("filter") or constraint.get("exclude")
            for constraint in plan.filters
        ):
            # Keep native ORM filter/exclude semantics for this one candidate.
            source = config.manager_cls.all().with_instances([instance])
        preparation = prepare_read_authorization(
            source,
            config.manager_cls,
            info,
            permission_plan=replace(plan, requires_instance_check=False),
        )
        authorized = finalize_read_authorization(
            preparation, config.manager_cls, info, source="remote_invalidation"
        ).queryset
        # Run the native instance gate unconditionally, even for allow-all plans.
        return any(
            item.Permission(item, user).can_read_instance() for item in authorized
        )
    except MemoryError:
        raise
    except Exception:  # noqa: BLE001
        # Missing instances and failed authorization lookups have the same
        # externally silent result. Never send a fallback action or identity.
        logger.warning("remote invalidation authorization failed")
        return False


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
        """Forward identified changes only under fresh native instance read rights.

        Admission requires an active authenticated Django account (4401).
        Unknown resources close with 4404, incompatible versions with 4406.
        Deleted, unavailable and identity-free batch events are suppressed.
        Existing client data is not evicted when access is lost.
        """
        from channels.db import database_sync_to_async

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
        principal = await database_sync_to_async(_fresh_remote_principal)(
            scope.get("user")
        )
        if principal is None:
            await send({"type": "websocket.close", "code": 4401})
            return
        channel_layer = _get_channel_layer_safe()
        if channel_layer is None:
            await send({"type": "websocket.close", "code": 1011})
            return
        self._channel_layer = channel_layer
        self._group_name = remote_invalidation_group_name(config)
        self._channel_name = await channel_layer.new_channel()
        receive_task: asyncio.Task[AsgiMessage] | None = None
        event_task: asyncio.Task[object] | None = None
        try:
            await channel_layer.group_add(self._group_name, self._channel_name)
            await send({"type": "websocket.accept"})
            receive_task = asyncio.create_task(receive())
            event_task = asyncio.create_task(channel_layer.receive(self._channel_name))
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
                        and await database_sync_to_async(_can_receive_remote_event)(
                            config, principal, event
                        )
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
            pending_tasks = [
                task for task in (receive_task, event_task) if task is not None
            ]
            for task in pending_tasks:
                task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)
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
    Existing middleware wrappers and the live URLRouter are preserved; opaque
    websocket applications are left unchanged with a warning. Routes append
    after existing websocket patterns. Missing or non-mutable
    `websocket_urlpatterns` are replaced with a new list. The helper supports
    ASGI modules that expose either a Channels `application_mapping` router or a
    unwrapped Django ASGIHandler attribute named by `ASGI_APPLICATION`. Unknown
    root callables (including closures and custom HTTP wrappers) are left unchanged
    because treating them as HTTP-only could bypass their websocket security.
    """
    registry = build_remote_api_registry(manager_classes)
    enabled = [config for config in registry.values() if config.websocket_invalidation]
    if not enabled:
        return
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
    application = getattr(asgi_module, attr_name, None)
    if application is None:
        return
    mapped_application = _protocol_application(application)
    websocket_app = (
        mapped_application.application_mapping.get("websocket")
        if mapped_application is not None
        else None
    )
    router = _websocket_router(websocket_app)
    if (websocket_app is not None and router is None) or (
        mapped_application is None and type(application) is not ASGIHandler
    ):
        logger.warning("remote invalidation routes require an accessible URLRouter")
        return
    websocket_patterns = _websocket_patterns(asgi_module)
    patterns = list(websocket_patterns or [])
    if router is not None:
        for route in router.routes:
            if route not in patterns:
                patterns.append(route)
    for config in enabled:
        normalized = f"{config.base_path.strip('/')}/ws/{config.resource_name}"
        pattern = rf"^{re.escape(normalized)}/?$"
        route_exists = any(
            getattr(route, "_general_manager_remote_ws_key", None)
            == (config.base_path, config.resource_name)
            for route in patterns
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
        patterns.append(websocket_route)
    if websocket_patterns is None:
        cast(_AsgiModuleRoutes, asgi_module).websocket_urlpatterns = patterns
    else:
        websocket_patterns[:] = patterns
    if router is not None:
        router.routes = list(patterns)
    elif mapped_application is not None:
        mapped_application.application_mapping["websocket"] = AuthMiddlewareStack(
            URLRouter(list(patterns))
        )
    else:
        setattr(
            asgi_module,
            attr_name,
            ProtocolTypeRouter(
                {
                    "http": application,
                    "websocket": AuthMiddlewareStack(URLRouter(list(patterns))),
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
    sequence websocket patterns are unavailable. The live URLRouter is updated in place through existing middleware wrappers.
    A plain HTTP application is never wrapped merely to clear routes.
    """
    asgi_path = getattr(settings, "ASGI_APPLICATION", None)
    if not asgi_path:
        return
    try:
        module_path, attr_name = asgi_path.rsplit(".", 1)
    except ValueError:
        return
    try:
        asgi_module = import_module(module_path)
    except ImportError:
        return
    websocket_patterns = _websocket_patterns(asgi_module)
    application = getattr(asgi_module, attr_name, None)
    mapped_application = _protocol_application(application)
    router = _websocket_router(
        mapped_application.application_mapping.get("websocket")
        if mapped_application is not None
        else None
    )

    def retained(route: object) -> bool:
        return not (
            getattr(route, "_general_manager_remote_ws", False)
            or getattr(route, "_general_manager_remote_ws_key", None) is not None
        )

    if websocket_patterns is not None:
        websocket_patterns[:] = [
            route for route in websocket_patterns if retained(route)
        ]
    if router is not None:
        router.routes = [route for route in router.routes if retained(route)]


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


class _RouteCollection(Protocol):
    routes: Sequence[object]


def _protocol_application(application: object) -> _ApplicationMapping | None:
    """Find the protocol router without discarding wrappers around it."""
    seen: set[int] = set()
    while application is not None and id(application) not in seen:
        seen.add(id(application))
        if isinstance(getattr(application, "application_mapping", None), dict):
            return cast(_ApplicationMapping, application)
        application = _middleware_inner(application)
    return None


def _websocket_router(application: object) -> _RouteCollection | None:
    """Locate an actual URLRouter through the existing middleware chain."""
    try:
        from channels.routing import URLRouter
    except ImportError:  # pragma: no cover - optional dependency
        return None

    seen: set[int] = set()
    while application is not None and id(application) not in seen:
        seen.add(id(application))
        if isinstance(application, URLRouter):
            return cast(_RouteCollection, application)
        application = _middleware_inner(application)
    return None


def _middleware_inner(application: object) -> object:
    """Traverse standard inner links and Channels' known origin wrapper."""
    try:
        from channels.security.websocket import OriginValidator
    except ImportError:  # pragma: no cover - optional dependency
        return getattr(application, "inner", None)
    if isinstance(application, OriginValidator):
        return application.application
    return getattr(application, "inner", None)
