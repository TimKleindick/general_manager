from __future__ import annotations

from django.test import SimpleTestCase
from django.urls import re_path
from channels.auth import AuthMiddlewareStack  # type: ignore[import-untyped]
from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore[import-untyped]

from general_manager.api.remote_invalidation import (
    clear_remote_invalidation_routes,
)

from tests import testing_asgi


def _unwrap_websocket_router():
    router = testing_asgi.application.application_mapping["websocket"]
    while hasattr(router, "inner") and not hasattr(router, "routes"):
        router = router.inner
    return router


class RemoteInvalidationRouteTests(SimpleTestCase):
    def tearDown(self) -> None:
        clear_remote_invalidation_routes()

    def test_clear_remote_invalidation_routes_rebuilds_live_router(self) -> None:
        graphql_route = testing_asgi.websocket_urlpatterns[0]
        remote_route = re_path(r"^remote/ws/projects/?$", lambda *_: None)
        remote_route._general_manager_remote_ws = True
        remote_route._general_manager_remote_ws_key = ("/remote", "projects")
        testing_asgi.websocket_urlpatterns[:] = [graphql_route, remote_route]
        testing_asgi.application.application_mapping["websocket"] = AuthMiddlewareStack(
            URLRouter(list(testing_asgi.websocket_urlpatterns))
        )
        router = _unwrap_websocket_router()
        self.assertEqual(len(testing_asgi.websocket_urlpatterns), 2)
        self.assertEqual(len(router.routes), 2)

        clear_remote_invalidation_routes()

        router = _unwrap_websocket_router()
        self.assertEqual(len(testing_asgi.websocket_urlpatterns), 1)
        self.assertIs(testing_asgi.websocket_urlpatterns[0], graphql_route)
        self.assertEqual(len(router.routes), 1)

    def test_clear_remote_invalidation_routes_rebuilds_protocol_router_for_plain_app(
        self,
    ) -> None:
        graphql_route = testing_asgi.websocket_urlpatterns[0]
        remote_route = re_path(r"^remote/ws/projects/?$", lambda *_: None)
        remote_route._general_manager_remote_ws = True
        remote_route._general_manager_remote_ws_key = ("/remote", "projects")
        original_application = testing_asgi.application
        try:
            testing_asgi.websocket_urlpatterns[:] = [graphql_route, remote_route]

            async def http_app(scope, receive, send):  # type: ignore[no-untyped-def]
                del scope, receive, send

            testing_asgi.application = http_app

            clear_remote_invalidation_routes()

            self.assertIsInstance(testing_asgi.application, ProtocolTypeRouter)
            router = _unwrap_websocket_router()
            self.assertEqual(len(testing_asgi.websocket_urlpatterns), 1)
            self.assertEqual(len(router.routes), 1)
            self.assertIs(testing_asgi.websocket_urlpatterns[0], graphql_route)
        finally:
            testing_asgi.application = original_application
