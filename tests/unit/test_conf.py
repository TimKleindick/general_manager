from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from general_manager.conf import get_setting


class GeneralManagerSettingsTests(SimpleTestCase):
    @override_settings(
        GENERAL_MANAGER={"VALIDATE_INPUT_VALUES": "nested"},
        GENERAL_MANAGER_VALIDATE_INPUT_VALUES="prefixed",
        VALIDATE_INPUT_VALUES="top-level",
    )
    def test_get_setting_prefers_nested_general_manager_value(self) -> None:
        assert get_setting("VALIDATE_INPUT_VALUES") == "nested"

    @override_settings(
        GENERAL_MANAGER={},
        GENERAL_MANAGER_VALIDATE_INPUT_VALUES="prefixed",
        VALIDATE_INPUT_VALUES="top-level",
    )
    def test_get_setting_uses_prefixed_legacy_value_before_top_level(self) -> None:
        assert get_setting("VALIDATE_INPUT_VALUES") == "prefixed"

    @override_settings(GENERAL_MANAGER={}, AUTOCREATE_GRAPHQL=True)
    def test_get_setting_uses_top_level_value(self) -> None:
        assert get_setting("AUTOCREATE_GRAPHQL") is True

    @override_settings(GENERAL_MANAGER={})
    def test_get_setting_returns_default_when_missing(self) -> None:
        assert get_setting("MISSING_SETTING", default="fallback") == "fallback"

    @override_settings(
        GENERAL_MANAGER=["not", "a", "dict"],
        GENERAL_MANAGER_VALIDATE_INPUT_VALUES="prefixed",
    )
    def test_get_setting_ignores_non_dict_general_manager(self) -> None:
        assert get_setting("VALIDATE_INPUT_VALUES") == "prefixed"

    @override_settings(
        GENERAL_MANAGER={"VALIDATE_INPUT_VALUES": None},
        GENERAL_MANAGER_VALIDATE_INPUT_VALUES="prefixed",
    )
    def test_get_setting_returns_nested_none_as_configured_value(self) -> None:
        assert get_setting("VALIDATE_INPUT_VALUES", default="fallback") is None
