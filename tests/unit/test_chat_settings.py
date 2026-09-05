from __future__ import annotations

from typing import Any, ClassVar

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


class ProfileConstructionProvider:
    """Test double that distinguishes profile and legacy construction."""

    constructed_configs: ClassVar[list[dict[str, Any]]] = []

    def __init__(self) -> None:
        self.config = dict(get_chat_settings()["provider_config"])
        type(self).constructed_configs.append(self.config)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ProfileConstructionProvider:
        provider = cls.__new__(cls)
        provider.config = dict(config)
        cls.constructed_configs.append(provider.config)
        return provider


class ChatSettingsTests(SimpleTestCase):
    def tearDown(self) -> None:
        GraphQL.reset_registry()
        ProfileConstructionProvider.constructed_configs.clear()
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

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider": "tests.unit.test_chat_settings.ConfiguredProvider",
                "planned": None,
            }
        }
    )
    def test_validate_chat_settings_rejects_explicit_none_planned_settings(
        self,
    ) -> None:
        import graphene

        class Query(graphene.ObjectType):
            ping = graphene.String()

        GraphQL._schema = graphene.Schema(query=Query)

        with pytest.raises(ChatConfigurationError, match="planned must be a mapping"):
            validate_chat_settings()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider": (
                    "tests.unit.test_chat_settings.ProfileConstructionProvider"
                ),
                "provider_config": {
                    "model": "legacy-model",
                    "api_key": "legacy-key",
                    "base_url": "https://legacy.example.test",
                },
                "provider_profiles": {
                    "isolated": {
                        "provider": (
                            "tests.unit.test_chat_settings.ProfileConstructionProvider"
                        ),
                        "provider_config": {},
                        "trust_group": "default",
                    }
                },
                "planned": {
                    "enabled": True,
                    "roles": {
                        "planner": "isolated",
                        "simple_executor": "isolated",
                        "complex_executor": "isolated",
                        "synthesizer": "isolated",
                        "fallback_executor": "isolated",
                    },
                },
            }
        }
    )
    def test_empty_explicit_profile_does_not_inherit_legacy_provider_config(
        self,
    ) -> None:
        from general_manager.chat.planned.config import (
            build_profile_provider,
            get_planned_chat_settings,
        )

        provider = build_profile_provider(
            get_planned_chat_settings().profiles["isolated"]
        )

        assert provider.config == {}
        assert ProfileConstructionProvider.constructed_configs == [{}]

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider": (
                    "tests.unit.test_chat_settings.ProfileConstructionProvider"
                ),
                "provider_config": {
                    "model": "legacy-model",
                    "api_key": "legacy-key",
                    "base_url": "https://legacy.example.test",
                },
                "planned": {"enabled": True},
            }
        }
    )
    def test_omitted_profiles_keep_legacy_provider_configuration(self) -> None:
        from general_manager.chat.planned.config import (
            build_profile_provider,
            get_planned_chat_settings,
        )

        provider = build_profile_provider(
            get_planned_chat_settings().profiles["default"]
        )

        assert provider.config == {
            "model": "legacy-model",
            "api_key": "legacy-key",
            "base_url": "https://legacy.example.test",
        }
