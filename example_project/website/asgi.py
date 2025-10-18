"""
ASGI config for website project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/asgi/
"""

import os

from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import-untyped]
from django.conf import settings
from django.core.asgi import get_asgi_application
from django.urls import re_path

from general_manager.api.graphql_subscription_consumer import (
    GraphQLSubscriptionConsumer,
)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings")

django_asgi_app = get_asgi_application()

graphql_route = getattr(settings, "GRAPHQL_URL", "graphql/")
normalized_route = graphql_route.strip("/")
pattern = rf"^{normalized_route}/?$" if normalized_route else r"^$"

websocket_urlpatterns = [
    re_path(pattern, GraphQLSubscriptionConsumer.as_asgi()), # type: ignore[arg-type]
]

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
