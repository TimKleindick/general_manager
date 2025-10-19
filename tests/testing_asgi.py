"""ASGI configuration used for the test suite."""

from __future__ import annotations

import os
from typing import Any, List

from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import-untyped]
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_settings")

http_application = get_asgi_application()
websocket_urlpatterns: List[Any] = []

application = ProtocolTypeRouter(
    {
        "http": http_application,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
