"""Django system checks for chat configuration."""

from __future__ import annotations

from django.core.checks import Error

from general_manager.chat.settings import (
    ChatConfigurationError,
    ProviderDependencyError,
    get_chat_settings,
    is_chat_enabled,
    validate_chat_settings,
)


def check_chat_configuration(*_args: object, **_kwargs: object) -> list[Error]:
    """Validate chat configuration when the feature is enabled."""
    if not is_chat_enabled():
        return []
    try:
        validate_chat_settings()
    except ProviderDependencyError as exc:
        return [Error(str(exc), id="general_manager.chat.E003")]
    except ChatConfigurationError as exc:
        if str(exc) == "GeneralManager chat requires an initialized GraphQL schema.":
            return [
                Error(
                    str(exc),
                    hint="Enable GraphQL schema generation before enabling chat.",
                    id="general_manager.chat.E001",
                )
            ]
        return [Error(str(exc), id="general_manager.chat.E002")]
    except ImportError as exc:
        settings = get_chat_settings()
        return [
            Error(
                (
                    "Failed to import configured chat provider "
                    f"'{settings['provider']}': {exc}"
                ),
                id="general_manager.chat.E004",
            )
        ]
    return []
