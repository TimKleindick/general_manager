"""Startup integration for chat."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from channels.routing import ProtocolTypeRouter
from django.conf import settings
from django.core.checks import register
from django.urls import path

from general_manager.chat.checks import check_chat_configuration
from general_manager.chat.routing import (
    build_chat_websocket_application,
    build_chat_ws_route,
)
from general_manager.chat.settings import get_chat_settings, is_chat_enabled


_CHECKS_REGISTERED = False


def register_checks() -> None:
    """Register chat system checks once."""
    global _CHECKS_REGISTERED
    if _CHECKS_REGISTERED:
        return
    register("general_manager")(check_chat_configuration)
    _CHECKS_REGISTERED = True


def initialize_chat() -> None:
    """Initialize chat startup hooks when enabled."""
    register_checks()
    if not is_chat_enabled():
        return
    ensure_chat_http_routes()
    ensure_chat_route()


def ensure_chat_http_routes() -> None:
    """Install chat HTTP and SSE routes into the configured URLConf."""
    from general_manager.chat.views import (
        chat_confirm_view,
        chat_http_view,
        chat_sse_view,
    )

    root_url_conf_path = getattr(settings, "ROOT_URLCONF", None)
    if not root_url_conf_path:
        return
    urlconf: Any = import_module(root_url_conf_path)
    urlpatterns = getattr(urlconf, "urlpatterns", None)
    if urlpatterns is None:
        urlpatterns = []
        urlconf.urlpatterns = urlpatterns
    chat_path = str(get_chat_settings()["url"]).strip("/")
    base_route = f"{chat_path}/" if chat_path else ""
    routes = [
        (base_route, chat_http_view, "_general_manager_chat_http"),
        (f"{base_route}stream/", chat_sse_view, "_general_manager_chat_sse"),
        (f"{base_route}confirm/", chat_confirm_view, "_general_manager_chat_confirm"),
    ]
    for route_path, view, marker in routes:
        if any(getattr(route, marker, False) for route in urlpatterns):
            continue
        route = path(route_path, view)
        setattr(route, marker, True)
        urlpatterns.append(route)


def ensure_chat_route() -> None:
    """Install the chat websocket route into the configured ASGI application."""
    asgi_path = getattr(settings, "ASGI_APPLICATION", None)
    if not asgi_path:
        return
    module_path, _, attr_name = asgi_path.rpartition(".")
    if not module_path or not attr_name:
        return
    asgi_module: Any = import_module(module_path)
    application = getattr(asgi_module, attr_name, None)
    extracted_patterns = (
        _extract_urlrouter_patterns(application) if application is not None else None
    )
    existing_websocket_application = (
        _get_websocket_application(application) if application is not None else None
    )
    if existing_websocket_application is not None and extracted_patterns is None:
        return
    websocket_patterns = getattr(asgi_module, "websocket_urlpatterns", None)
    if websocket_patterns is None:
        websocket_patterns = list(extracted_patterns or [])
        asgi_module.websocket_urlpatterns = websocket_patterns
    elif not isinstance(websocket_patterns, list):
        return
    route_exists = any(
        getattr(route, "_general_manager_chat_ws", False)
        for route in websocket_patterns
    )
    if not route_exists:
        websocket_patterns.append(build_chat_ws_route(get_chat_settings()["url"]))
    websocket_application = build_chat_websocket_application(websocket_patterns)
    if application is None:
        return
    if hasattr(application, "application_mapping") and isinstance(
        application.application_mapping, dict
    ):
        application.application_mapping["websocket"] = websocket_application
        return
    if extracted_patterns is not None:
        setattr(asgi_module, attr_name, websocket_application)
        return
    setattr(
        asgi_module,
        attr_name,
        ProtocolTypeRouter({"http": application, "websocket": websocket_application}),
    )


def _get_websocket_application(application: Any) -> Any | None:
    application_mapping = getattr(application, "application_mapping", None)
    if not isinstance(application_mapping, dict):
        return None
    return application_mapping.get("websocket")


def _extract_urlrouter_patterns(application: Any) -> list[Any] | None:
    """Return URLRouter patterns from a mapped websocket app when inspectable."""
    router = _get_websocket_application(application) or application
    seen: set[int] = set()
    while router is not None and id(router) not in seen:
        seen.add(id(router))
        routes = getattr(router, "routes", None)
        if isinstance(routes, list):
            return list(routes)
        urlpatterns = getattr(router, "urlpatterns", None)
        if isinstance(urlpatterns, list):
            return list(urlpatterns)
        router = getattr(router, "inner", None) or getattr(router, "application", None)
    return None
