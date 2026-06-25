from __future__ import annotations

import pytest
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.settings import (
    ChatConfigurationError,
    ProviderDependencyError,
    get_chat_settings,
    get_permission_callable,
    import_provider,
    validate_chat_settings,
)


PROVIDER_EXTRA = {"MissingOptionalProvider": "chat-missing"}


def _allow_chat(_user, _scope) -> bool:
    return True


class ConfiguredProvider:
    @classmethod
    def check_configuration(cls) -> None:
        return None


class ChatSettingsTests(SimpleTestCase):
    def tearDown(self) -> None:
        GraphQL.reset_registry()
        super().tearDown()

    @override_settings(GENERAL_MANAGER={"CHAT": "enabled"})
    def test_get_chat_settings_rejects_non_mapping_configuration(self) -> None:
        with pytest.raises(
            ChatConfigurationError,
            match="GENERAL_MANAGER\\['CHAT'\\] must be a mapping",
        ):
            get_chat_settings()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "rate_limit": {
                    "max_requests_per_hour": 7,
                    "max_tokens_per_hour": 99,
                },
                "query_limits": {
                    "max_results": 12,
                    "query_timeout_seconds": 3,
                    "max_retries_per_message": 4,
                },
                "conversation": {
                    "max_recent_messages": 5,
                    "summarize_after": 6,
                    "ttl_hours": 48,
                },
                "audit": {"enabled": True, "level": "messages"},
            }
        }
    )
    def test_get_chat_settings_merges_legacy_nested_aliases(self) -> None:
        settings = get_chat_settings()

        assert settings["rate_limit"]["requests"] == 7
        assert settings["rate_limit"]["tokens"] == 99
        assert settings["rate_limit"]["window_seconds"] == 3600
        assert settings["max_results"] == 12
        assert settings["query_timeout_seconds"] == 3
        assert settings["max_retries_per_message"] == 4
        assert settings["max_recent_messages"] == 5
        assert settings["summarize_after"] == 6
        assert settings["ttl_hours"] == 48
        assert settings["audit"]["enabled"] is True
        assert settings["audit"]["level"] == "messages"
        assert "token" in settings["audit"]["redact_fields"]

    @override_settings(GENERAL_MANAGER={"CHAT": {"permission": 123}})
    def test_get_permission_callable_rejects_invalid_permission_type(self) -> None:
        with pytest.raises(ChatConfigurationError, match="Chat permission"):
            get_permission_callable()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "permission": "tests.unit.test_chat_settings._allow_chat",
            }
        }
    )
    def test_get_permission_callable_imports_dotted_path(self) -> None:
        assert get_permission_callable() is _allow_chat

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider": "tests.unit.test_chat_settings.MissingOptionalProvider",
            }
        }
    )
    def test_import_provider_maps_missing_optional_provider_to_extra_hint(self) -> None:
        with pytest.raises(
            ProviderDependencyError,
            match=r"pip install general-manager\[chat-missing\]",
        ):
            import_provider()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider": "tests.unit.test_chat_settings.ConfiguredProvider",
                "allowed_mutations": [],
                "confirm_mutations": ["createPart"],
            }
        }
    )
    def test_validate_chat_settings_rejects_confirm_mutation_not_allowed(self) -> None:
        import graphene

        class Query(graphene.ObjectType):
            ping = graphene.String()

        GraphQL._schema = graphene.Schema(query=Query)

        with pytest.raises(
            ChatConfigurationError,
            match="confirm_mutations must also be in allowed_mutations: createPart",
        ):
            validate_chat_settings()
