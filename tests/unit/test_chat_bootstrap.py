from __future__ import annotations

from importlib import import_module
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from channels.routing import ProtocolTypeRouter, URLRouter
import pytest
from django.core.checks import Error
from django.test import SimpleTestCase
from django.test.utils import override_settings
from django.urls import path

from general_manager import apps as gm_apps
from general_manager.api.graphql import GraphQL
from general_manager.chat.bootstrap import ensure_chat_http_routes, ensure_chat_route
from tests import test_urls, testing_asgi


def _extract_test_urlrouter_routes(application: object) -> list[object]:
    application_mapping = getattr(application, "application_mapping", None)
    router = (
        application_mapping.get("websocket")
        if isinstance(application_mapping, dict)
        else application
    )
    seen: set[int] = set()
    while router is not None and id(router) not in seen:
        seen.add(id(router))
        routes = getattr(router, "routes", None)
        if isinstance(routes, list):
            return routes
        router = getattr(router, "inner", None) or getattr(router, "application", None)
    raise AssertionError


class ChatBootstrapTests(SimpleTestCase):
    def setUp(self) -> None:
        GraphQL.reset_registry()
        self._original_asgi_application = testing_asgi.application
        self._original_websocket_patterns = list(testing_asgi.websocket_urlpatterns)
        test_urls.urlpatterns[:] = []
        testing_asgi.websocket_urlpatterns[:] = []
        super().setUp()

    def tearDown(self) -> None:
        test_urls.urlpatterns[:] = []
        testing_asgi.application = self._original_asgi_application
        testing_asgi.websocket_urlpatterns[:] = self._original_websocket_patterns
        GraphQL.reset_registry()
        super().tearDown()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "general_manager.chat.providers.OllamaProvider",
            }
        },
    )
    def test_ready_initializes_chat_when_enabled(self) -> None:
        config = gm_apps.GeneralmanagerConfig(
            "general_manager", import_module("general_manager")
        )
        config.apps = SimpleNamespace(get_app_configs=lambda: ())

        with (
            patch.object(config, "install_startup_hook_runner"),
            patch.object(config, "register_system_checks"),
            patch("general_manager.apps._autoload_app_managers_modules"),
            patch.object(config, "initialize_general_manager_classes"),
            patch("general_manager.apps.handle_remote_api"),
            patch("general_manager.apps.handle_graph_ql"),
            patch("general_manager.apps.configure_audit_logger_from_settings"),
            patch("general_manager.apps.configure_search_backend_from_settings"),
            patch("general_manager.apps.configure_workflow_engine_from_settings"),
            patch("general_manager.apps.configure_event_registry_from_settings"),
            patch(
                "general_manager.apps.configure_workflow_signal_bridge_from_settings"
            ),
            patch(
                "general_manager.apps.configure_workflow_beat_schedule_from_settings"
            ),
            patch(
                "general_manager.apps.configure_search_reconcile_beat_schedule_from_settings"
            ),
            patch(
                "general_manager.apps.configure_graphql_warmup_beat_schedule_from_settings"
            ),
            patch("general_manager.apps.initialize_chat") as initialize_chat,
        ):
            config.ready()

        initialize_chat.assert_called_once_with()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "general_manager.chat.providers.OllamaProvider",
            }
        },
    )
    def test_chat_check_fails_when_schema_missing(self) -> None:
        from general_manager.chat.checks import check_chat_configuration

        errors = check_chat_configuration()

        assert errors == [
            Error(
                "GeneralManager chat requires an initialized GraphQL schema.",
                hint="Enable GraphQL schema generation before enabling chat.",
                id="general_manager.chat.E001",
            )
        ]

    @override_settings(GENERAL_MANAGER={"CHAT": {"enabled": False}})
    def test_chat_check_skips_validation_when_chat_disabled(self) -> None:
        from general_manager.chat.checks import check_chat_configuration

        assert check_chat_configuration() == []

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "permission": 123,
            }
        },
    )
    def test_chat_check_reports_generic_configuration_error(self) -> None:
        from graphene import ObjectType, Schema, String

        from general_manager.chat.checks import check_chat_configuration

        class Query(ObjectType):
            ping = String()

        GraphQL._schema = Schema(query=Query)

        errors = check_chat_configuration()

        assert len(errors) == 1
        assert errors[0].id == "general_manager.chat.E002"
        assert errors[0].msg == "Chat permission must be a callable or dotted path."

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.DoesNotExistProvider",
            }
        },
    )
    def test_chat_check_reports_provider_import_error(self) -> None:
        from graphene import ObjectType, Schema, String

        from general_manager.chat.checks import check_chat_configuration

        class Query(ObjectType):
            ping = String()

        GraphQL._schema = Schema(query=Query)

        errors = check_chat_configuration()

        assert len(errors) == 1
        assert errors[0].id == "general_manager.chat.E004"
        assert "Failed to import configured chat provider" in errors[0].msg

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.MissingSdkProvider",
            }
        },
    )
    def test_chat_check_reports_provider_dependency_error(self) -> None:
        from graphene import ObjectType, Schema, String

        from general_manager.chat.checks import check_chat_configuration

        class Query(ObjectType):
            ping = String()

        GraphQL._schema = Schema(query=Query)
        errors = check_chat_configuration()

        assert len(errors) == 1
        assert errors[0].id == "general_manager.chat.E003"
        assert (
            errors[0].msg
            == "To use MissingSdkProvider, install: pip install general-manager[chat-missing]"
        )

    @override_settings(
        ROOT_URLCONF="tests.test_urls",
        GENERAL_MANAGER={"CHAT": {"url": "/assistant/"}},
    )
    def test_ensure_chat_http_routes_installs_routes_once(self) -> None:
        ensure_chat_http_routes()
        ensure_chat_http_routes()

        route_patterns = [str(route.pattern) for route in test_urls.urlpatterns]

        assert route_patterns == [
            "assistant/",
            "assistant/stream/",
            "assistant/confirm/",
        ]
        assert len(test_urls.urlpatterns) == 3
        assert test_urls.urlpatterns[0]._general_manager_chat_http is True
        assert test_urls.urlpatterns[1]._general_manager_chat_sse is True
        assert test_urls.urlpatterns[2]._general_manager_chat_confirm is True

    @override_settings(ROOT_URLCONF=None)
    def test_ensure_chat_http_routes_ignores_missing_root_urlconf(self) -> None:
        ensure_chat_http_routes()

        assert test_urls.urlpatterns == []

    @override_settings(
        ASGI_APPLICATION="tests.testing_asgi.application",
        GENERAL_MANAGER={"CHAT": {"url": "/chat/"}},
    )
    def test_ensure_chat_route_updates_existing_protocol_router_once(self) -> None:
        ensure_chat_route()
        ensure_chat_route()

        assert "websocket" in testing_asgi.application.application_mapping
        assert len(testing_asgi.websocket_urlpatterns) == 1
        assert testing_asgi.websocket_urlpatterns[0]._general_manager_chat_ws is True

    @override_settings(
        ASGI_APPLICATION="tests.unit.test_chat_bootstrap_fake_asgi.application",
        GENERAL_MANAGER={"CHAT": {"url": "/chat/"}},
    )
    def test_ensure_chat_route_preserves_existing_urlrouter_routes_without_patterns(
        self,
    ) -> None:
        module_name = "tests.unit.test_chat_bootstrap_fake_asgi"
        fake_asgi = ModuleType(module_name)

        async def existing_application(scope, receive, send):  # type: ignore[no-untyped-def]
            del scope, receive, send

        existing_route = path("existing/", existing_application)
        fake_asgi.application = ProtocolTypeRouter(
            {
                "http": object(),
                "websocket": URLRouter([existing_route]),
            }
        )
        sys.modules[module_name] = fake_asgi

        try:
            ensure_chat_route()
            ensure_chat_route()
        finally:
            sys.modules.pop(module_name, None)

        websocket_application = fake_asgi.application.application_mapping["websocket"]
        routes = _extract_test_urlrouter_routes(websocket_application)

        assert existing_route in routes
        assert (
            sum(getattr(route, "_general_manager_chat_ws", False) for route in routes)
            == 1
        )
        assert len(routes) == 2

    @override_settings(
        ASGI_APPLICATION="tests.unit.test_chat_bootstrap_stale_asgi.application",
        GENERAL_MANAGER={"CHAT": {"url": "/chat/"}},
    )
    def test_ensure_chat_route_does_not_merge_live_routes_when_patterns_empty(
        self,
    ) -> None:
        module_name = "tests.unit.test_chat_bootstrap_stale_asgi"
        fake_asgi = ModuleType(module_name)

        async def stale_application(scope, receive, send):  # type: ignore[no-untyped-def]
            del scope, receive, send

        stale_route = path("graphql/", stale_application)
        fake_asgi.websocket_urlpatterns = []
        fake_asgi.application = ProtocolTypeRouter(
            {
                "http": object(),
                "websocket": URLRouter([stale_route]),
            }
        )
        sys.modules[module_name] = fake_asgi

        try:
            ensure_chat_route()
        finally:
            sys.modules.pop(module_name, None)

        routes = _extract_test_urlrouter_routes(fake_asgi.application)

        assert stale_route not in fake_asgi.websocket_urlpatterns
        assert stale_route not in routes
        assert len(routes) == 1
        assert getattr(routes[0], "_general_manager_chat_ws", False) is True

    @override_settings(
        ASGI_APPLICATION="tests.unit.test_chat_bootstrap_direct_asgi.application",
        GENERAL_MANAGER={"CHAT": {"url": "/chat/"}},
    )
    def test_ensure_chat_route_preserves_direct_urlrouter_routes(self) -> None:
        module_name = "tests.unit.test_chat_bootstrap_direct_asgi"
        fake_asgi = ModuleType(module_name)

        async def existing_application(scope, receive, send):  # type: ignore[no-untyped-def]
            del scope, receive, send

        existing_route = path("existing/", existing_application)
        fake_asgi.application = URLRouter([existing_route])
        sys.modules[module_name] = fake_asgi

        try:
            ensure_chat_route()
            ensure_chat_route()
        finally:
            sys.modules.pop(module_name, None)

        routes = _extract_test_urlrouter_routes(fake_asgi.application)

        assert existing_route in routes
        assert (
            sum(getattr(route, "_general_manager_chat_ws", False) for route in routes)
            == 1
        )
        assert len(routes) == 2

    @override_settings(
        ASGI_APPLICATION="tests.unit.test_chat_bootstrap_uninspectable_asgi.application",
        GENERAL_MANAGER={"CHAT": {"url": "/chat/"}},
    )
    def test_ensure_chat_route_leaves_uninspectable_websocket_app_unmodified(
        self,
    ) -> None:
        module_name = "tests.unit.test_chat_bootstrap_uninspectable_asgi"
        fake_asgi = ModuleType(module_name)

        async def existing_application(scope, receive, send):  # type: ignore[no-untyped-def]
            del scope, receive, send

        existing_route = path("existing/", existing_application)
        websocket_application = object()
        fake_asgi.websocket_urlpatterns = [existing_route]
        fake_asgi.application = ProtocolTypeRouter(
            {
                "http": object(),
                "websocket": websocket_application,
            }
        )
        sys.modules[module_name] = fake_asgi

        try:
            ensure_chat_route()
        finally:
            sys.modules.pop(module_name, None)

        assert fake_asgi.websocket_urlpatterns == [existing_route]
        assert (
            fake_asgi.application.application_mapping["websocket"]
            is websocket_application
        )

    @override_settings(ASGI_APPLICATION="tests.testing_asgi.missing")
    def test_ensure_chat_route_creates_pattern_list_without_application(self) -> None:
        if hasattr(testing_asgi, "missing"):
            delattr(testing_asgi, "missing")
        delattr(testing_asgi, "websocket_urlpatterns")

        try:
            ensure_chat_route()

            assert len(testing_asgi.websocket_urlpatterns) == 1
            assert not hasattr(testing_asgi, "missing")
        finally:
            testing_asgi.websocket_urlpatterns = []


class MissingSdkProvider:
    required_extra = "chat-missing"

    @classmethod
    def check_configuration(cls) -> None:
        raise MissingSdkProviderImportError()


class MissingSdkProviderImportError(ImportError):
    def __init__(self) -> None:
        super().__init__("missing optional dependency")


class NoopProvider:
    @classmethod
    def check_configuration(cls) -> None:
        return None


@override_settings(
    GENERAL_MANAGER={
        "CHAT": {
            "enabled": True,
            "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
            "allowed_mutations": ["missingMutation"],
        }
    },
)
def test_validate_chat_settings_rejects_unknown_allowed_mutations() -> None:
    from graphene import ObjectType, Schema, String

    from general_manager.chat.settings import (
        ChatConfigurationError,
        validate_chat_settings,
    )

    class Query(ObjectType):
        ping = String()

    previous_schema = GraphQL._schema

    try:
        GraphQL._schema = Schema(query=Query)

        with pytest.raises(
            ChatConfigurationError,
            match="Unknown chat allowed_mutations: missingMutation",
        ):
            validate_chat_settings()
    finally:
        GraphQL.reset_registry()
        GraphQL._schema = previous_schema


@override_settings(
    GENERAL_MANAGER={
        "CHAT": {
            "enabled": True,
            "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
            "allowed_mutations": ["createPrat"],
        }
    },
)
def test_validate_chat_settings_suggests_close_match_for_unknown_mutation() -> None:
    from graphene import Boolean, Mutation, ObjectType, Schema, String

    from general_manager.chat.settings import (
        ChatConfigurationError,
        validate_chat_settings,
    )

    class CreatePart(Mutation):
        success = Boolean(required=True)

        class Arguments:
            name = String(required=True)

        @staticmethod
        def mutate(_root, _info, name: str):  # type: ignore[no-untyped-def]
            del name
            return CreatePart(success=True)

    class Query(ObjectType):
        ping = String()

    class MutationRoot(ObjectType):
        createPart = CreatePart.Field()

    previous_schema = GraphQL._schema

    try:
        GraphQL._schema = Schema(query=Query, mutation=MutationRoot)

        with pytest.raises(
            ChatConfigurationError,
            match=r"Unknown chat allowed_mutations: createPrat \(did you mean: createPart\?\)",
        ):
            validate_chat_settings()
    finally:
        GraphQL.reset_registry()
        GraphQL._schema = previous_schema
