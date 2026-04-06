"""Routing helpers for chat."""

from __future__ import annotations

import re
from typing import Any

from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
from channels.routing import URLRouter  # type: ignore[import-untyped]
from channels.security.websocket import (  # type: ignore[import-untyped]
    AllowedHostsOriginValidator,
    OriginValidator,
)
from django.urls import re_path

from general_manager.chat.consumer import ChatConsumer
from general_manager.chat.settings import get_chat_settings


def build_chat_ws_route(chat_url: str) -> Any:
    """Build the chat websocket route for the configured URL."""
    normalized = chat_url.strip("/")
    escaped = re.escape(normalized)
    pattern = rf"^{escaped}/?$" if normalized else r"^$"
    route = re_path(pattern, ChatConsumer.as_asgi())  # type: ignore[arg-type]
    route._general_manager_chat_ws = True
    return route


def build_chat_websocket_application(websocket_patterns: list[Any]) -> Any:
    """Wrap the chat websocket routes with auth and origin validation."""
    application = AuthMiddlewareStack(URLRouter(list(websocket_patterns)))
    allowed_origins = get_chat_settings().get("allowed_origins")
    if allowed_origins:
        return OriginValidator(application, list(allowed_origins))
    return AllowedHostsOriginValidator(application)
