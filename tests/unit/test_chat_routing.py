from __future__ import annotations

from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.chat.routing import (
    build_chat_websocket_application,
    build_chat_ws_route,
)


class ChatRoutingTests(SimpleTestCase):
    def test_build_chat_ws_route_marks_general_manager_route(self) -> None:
        route = build_chat_ws_route("/chat/")

        assert getattr(route, "_general_manager_chat_ws", False) is True

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "allowed_origins": ["https://app.example.com"],
            }
        }
    )
    def test_build_chat_websocket_application_uses_origin_validator(self) -> None:
        application = build_chat_websocket_application([build_chat_ws_route("/chat/")])

        assert application.__class__.__name__ == "OriginValidator"
        assert application.allowed_origins == ["https://app.example.com"]

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "allowed_origins": None,
            }
        },
        ALLOWED_HOSTS=["chat.example.com"],
    )
    def test_build_chat_websocket_application_falls_back_to_allowed_hosts(self) -> None:
        application = build_chat_websocket_application([build_chat_ws_route("/chat/")])

        assert application.__class__.__name__ == "OriginValidator"
        assert application.allowed_origins == ["chat.example.com"]
