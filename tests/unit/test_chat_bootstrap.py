from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.checks import Error
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager import apps as gm_apps
from general_manager.api.graphql import GraphQL


class ChatBootstrapTests(SimpleTestCase):
    def setUp(self) -> None:
        GraphQL.reset_registry()
        super().setUp()

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
            patch.object(config, "install_search_auto_reindex"),
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

    GraphQL._schema = Schema(query=Query)

    with pytest.raises(
        ChatConfigurationError,
        match="Unknown chat allowed_mutations: missingMutation",
    ):
        validate_chat_settings()


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

    GraphQL._schema = Schema(query=Query, mutation=MutationRoot)

    with pytest.raises(
        ChatConfigurationError,
        match=r"Unknown chat allowed_mutations: createPrat \(did you mean: createPart\?\)",
    ):
        validate_chat_settings()
