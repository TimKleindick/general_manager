from __future__ import annotations

from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.chat.bootstrap import ensure_chat_route, initialize_chat

from tests import testing_asgi


def _unwrap_websocket_router():
    router = testing_asgi.application.application_mapping["websocket"]
    while hasattr(router, "application") and not hasattr(router, "routes"):
        router = router.application
    while hasattr(router, "inner") and not hasattr(router, "routes"):
        router = router.inner
    return router


class ChatAsgiRouteTests(SimpleTestCase):
    def setUp(self) -> None:
        self._original_patterns = list(testing_asgi.websocket_urlpatterns)
        self._original_application = testing_asgi.application

    def tearDown(self) -> None:
        testing_asgi.websocket_urlpatterns[:] = self._original_patterns
        testing_asgi.application = self._original_application
        super().tearDown()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "url": "/chat/",
            }
        }
    )
    def test_ensure_chat_route_appends_websocket_route_once(self) -> None:
        testing_asgi.websocket_urlpatterns[:] = []

        ensure_chat_route()
        ensure_chat_route()

        marked_routes = [
            route
            for route in testing_asgi.websocket_urlpatterns
            if getattr(route, "_general_manager_chat_ws", False)
        ]
        assert len(marked_routes) == 1

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "url": "/chat/",
            }
        }
    )
    def test_initialize_chat_rebuilds_live_websocket_router(self) -> None:
        testing_asgi.websocket_urlpatterns[:] = []

        initialize_chat()

        router = _unwrap_websocket_router()
        assert len(router.routes) == 1
        assert getattr(router.routes[0], "_general_manager_chat_ws", False) is True
