from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "orl.settings")

django_asgi_app = get_asgi_application()

try:
    from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
    from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import-untyped]
    from django.urls import re_path

    from general_manager.api.graphql_subscription_consumer import (
        GraphQLSubscriptionConsumer,
    )

    websocket_urlpatterns = [
        re_path(r"^graphql/?$", GraphQLSubscriptionConsumer.as_asgi()),
    ]

    application = ProtocolTypeRouter(
        {
            "http": django_asgi_app,
            "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
        }
    )
except Exception:
    application = django_asgi_app
