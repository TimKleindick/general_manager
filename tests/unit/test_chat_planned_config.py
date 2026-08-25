from __future__ import annotations

import graphene
import pytest
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.planned.config import (
    ProviderProfile,
    build_profile_provider,
    get_planned_chat_settings,
    profile_for_role,
)
from general_manager.chat.settings import ChatConfigurationError, validate_chat_settings


class ZeroArgumentProvider:
    def complete(self, messages: list[object], tools: list[object]):
        del messages, tools
        yield from ()


def _complete_roles(profile: str) -> dict[str, str]:
    return {
        "planner": profile,
        "simple_executor": profile,
        "complex_executor": profile,
        "synthesizer": profile,
        "fallback_executor": profile,
    }


class PlannedChatSettingsTests(SimpleTestCase):
    def tearDown(self) -> None:
        GraphQL.reset_registry()
        super().tearDown()

    def test_planned_settings_build_implicit_default_profile_from_legacy_provider(
        self,
    ) -> None:
        settings = get_planned_chat_settings()

        assert settings.roles == _complete_roles("default")
        assert settings.profiles["default"].trust_group == "default"
        assert (
            settings.profiles["default"].provider_path
            == "general_manager.chat.providers.OllamaProvider"
        )

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "planned": {"enabled": False, "max_concurrent_tasks": 0},
                "provider_profiles": {"invalid": "not-a-profile"},
            }
        }
    )
    def test_disabled_planned_mode_does_not_validate_planned_settings(self) -> None:
        assert get_planned_chat_settings().enabled is False

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider": "tests.unit.test_chat_planned_config.ZeroArgumentProvider",
                "provider_profiles": {
                    "fast": {
                        "provider": "tests.unit.test_chat_planned_config.ZeroArgumentProvider",
                        "provider_config": {},
                        "trust_group": "local",
                    }
                },
                "planned": {"enabled": True, "roles": {"planner": "fast"}},
            }
        }
    )
    def test_enabled_planned_settings_require_every_role(self) -> None:
        with pytest.raises(ChatConfigurationError, match="required roles"):
            get_planned_chat_settings()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_profiles": {
                    "fast": {
                        "provider": "tests.unit.test_chat_planned_config.ZeroArgumentProvider",
                        "provider_config": {},
                        "trust_group": "local",
                    }
                },
                "planned": {
                    "enabled": True,
                    "roles": _complete_roles("missing"),
                },
            }
        }
    )
    def test_enabled_planned_settings_reject_unknown_role_profile(self) -> None:
        with pytest.raises(ChatConfigurationError, match="unknown profile"):
            get_planned_chat_settings()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_profiles": {
                    "fast": {
                        "provider": "tests.unit.test_chat_planned_config.ZeroArgumentProvider",
                        "provider_config": ["not", "a", "mapping"],
                        "trust_group": "local",
                    }
                },
                "planned": {"enabled": True, "roles": _complete_roles("fast")},
            }
        }
    )
    def test_enabled_planned_settings_require_mapping_profile_config(self) -> None:
        with pytest.raises(ChatConfigurationError, match=r"provider_config.*mapping"):
            get_planned_chat_settings()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "planned": {"enabled": True},
            }
        }
    )
    def test_enabled_planned_settings_require_positive_numeric_bounds(self) -> None:
        for key, value in (
            ("max_concurrent_tasks", 0),
            ("evidence_timeout_seconds", 0),
            ("synthesis_timeout_seconds", -1),
        ):
            with self.subTest(key=key):
                with override_settings(
                    GENERAL_MANAGER={"CHAT": {"planned": {"enabled": True, key: value}}}
                ):
                    with pytest.raises(ChatConfigurationError, match=key):
                        get_planned_chat_settings()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider": "tests.unit.test_chat_planned_config.ZeroArgumentProvider",
                "provider_profiles": {
                    "remote": {
                        "provider": "tests.unit.test_chat_planned_config.ZeroArgumentProvider",
                        "provider_config": {},
                        "trust_group": "remote",
                    },
                    "local": {
                        "provider": "tests.unit.test_chat_planned_config.ZeroArgumentProvider",
                        "provider_config": {},
                        "trust_group": "local",
                    },
                },
                "planned": {
                    "enabled": True,
                    "roles": {
                        **_complete_roles("remote"),
                        "fallback_executor": "local",
                    },
                },
            }
        }
    )
    def test_planned_settings_reject_roles_from_different_trust_groups(self) -> None:
        class Query(graphene.ObjectType):
            ping = graphene.String()

        GraphQL._schema = graphene.Schema(query=Query)

        with pytest.raises(ChatConfigurationError, match="one trust_group"):
            validate_chat_settings()

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "provider_profiles": {
                    "evaluation": {
                        "provider": "tests.unit.test_chat_planned_config.ZeroArgumentProvider",
                        "provider_config": {},
                        "trust_group": "evaluation",
                    },
                    "production": {
                        "provider": "tests.unit.test_chat_planned_config.ZeroArgumentProvider",
                        "provider_config": {},
                        "trust_group": "production",
                    },
                },
                "planned": {
                    "enabled": True,
                    "roles": _complete_roles("evaluation"),
                },
            }
        }
    )
    def test_complete_server_side_role_mapping_can_use_another_trust_group(
        self,
    ) -> None:
        settings = get_planned_chat_settings()

        assert profile_for_role(settings, "planner").trust_group == "evaluation"

    def test_explicit_nonempty_custom_profile_requires_from_config(self) -> None:
        profile = ProviderProfile(
            name="custom",
            provider_path="tests.unit.test_chat_planned_config.ZeroArgumentProvider",
            provider_config={"model": "configured"},
            trust_group="default",
        )

        with pytest.raises(ChatConfigurationError, match="configured construction"):
            build_profile_provider(profile)

    def test_empty_custom_profile_config_preserves_zero_argument_provider(self) -> None:
        profile = ProviderProfile(
            name="custom",
            provider_path="tests.unit.test_chat_planned_config.ZeroArgumentProvider",
            provider_config={},
            trust_group="default",
        )

        assert isinstance(build_profile_provider(profile), ZeroArgumentProvider)
