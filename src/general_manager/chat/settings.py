"""Chat settings helpers and validation."""

from __future__ import annotations

from difflib import get_close_matches
from importlib import import_module
from typing import Any

from django.utils.module_loading import import_string

from general_manager.api.graphql import GraphQL
from general_manager.conf import get_setting


DEFAULT_CHAT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "url": "/chat/",
    "provider": "general_manager.chat.providers.OllamaProvider",
    "provider_config": {},
    "allowed_mutations": [],
    "confirm_mutations": [],
    "permission": None,
    "allowed_origins": None,
    "max_results": 200,
    "query_timeout_seconds": None,
    "max_retries_per_message": 3,
    "rate_limit": {
        "requests": 60,
        "window_seconds": 3600,
        "tokens": None,
        "input_tokens": None,
        "output_tokens": None,
    },
    "audit": {
        "enabled": False,
        "level": "off",
        "logger": None,
        "max_result_size": 4096,
        "redact_fields": ["password", "secret", "token", "key", "credential"],
    },
    "ttl_hours": 24,
    "confirm_timeout_seconds": 30,
    "tool_strategy": "discovery",
    "max_recent_messages": 20,
    "summarize_after": 10,
}


class ChatConfigurationError(ValueError):
    """Raised when chat settings are invalid."""

    @classmethod
    def invalid_settings_mapping(cls) -> ChatConfigurationError:
        return cls("GENERAL_MANAGER['CHAT'] must be a mapping.")

    @classmethod
    def invalid_permission(cls) -> ChatConfigurationError:
        return cls("Chat permission must be a callable or dotted path.")

    @classmethod
    def missing_graphql_schema(cls) -> ChatConfigurationError:
        return cls("GeneralManager chat requires an initialized GraphQL schema.")

    @classmethod
    def unknown_allowed_mutations(cls, names: str) -> ChatConfigurationError:
        return cls(f"Unknown chat allowed_mutations: {names}")

    @classmethod
    def invalid_confirm_mutations(cls, names: str) -> ChatConfigurationError:
        return cls(f"confirm_mutations must also be in allowed_mutations: {names}")


class ProviderDependencyError(Exception):
    """Describe a provider import/configuration dependency failure."""

    def __init__(self, provider_name: str, extra_name: str) -> None:
        self.provider_name = provider_name
        self.extra_name = extra_name
        super().__init__(provider_name, extra_name)

    def __str__(self) -> str:
        return (
            f"To use {self.provider_name}, install: "
            f"pip install general-manager[{self.extra_name}]"
        )


def get_chat_settings() -> dict[str, Any]:
    """Return chat settings merged with defaults."""
    configured = get_setting("CHAT", {})
    if configured is None:
        configured = {}
    if not isinstance(configured, dict):
        raise ChatConfigurationError.invalid_settings_mapping()
    merged = dict(DEFAULT_CHAT_SETTINGS)
    merged.update(configured)
    if isinstance(DEFAULT_CHAT_SETTINGS["rate_limit"], dict):
        rate_limit = dict(DEFAULT_CHAT_SETTINGS["rate_limit"])
        configured_rate_limit = configured.get("rate_limit")
        if isinstance(configured_rate_limit, dict):
            rate_limit.update(configured_rate_limit)
            if (
                "max_requests_per_hour" in configured_rate_limit
                and "requests" not in configured_rate_limit
            ):
                rate_limit["requests"] = configured_rate_limit["max_requests_per_hour"]
                rate_limit["window_seconds"] = 3600
            if (
                "max_tokens_per_hour" in configured_rate_limit
                and "tokens" not in configured_rate_limit
            ):
                rate_limit["tokens"] = configured_rate_limit["max_tokens_per_hour"]
        merged["rate_limit"] = rate_limit
    if isinstance(DEFAULT_CHAT_SETTINGS["audit"], dict):
        audit = dict(DEFAULT_CHAT_SETTINGS["audit"])
        configured_audit = configured.get("audit")
        if isinstance(configured_audit, dict):
            audit.update(configured_audit)
        merged["audit"] = audit
    query_limits = configured.get("query_limits")
    if isinstance(query_limits, dict):
        if "max_results" not in configured and "max_results" in query_limits:
            merged["max_results"] = query_limits["max_results"]
        if (
            "query_timeout_seconds" not in configured
            and "query_timeout_seconds" in query_limits
        ):
            merged["query_timeout_seconds"] = query_limits["query_timeout_seconds"]
        if (
            "max_retries_per_message" not in configured
            and "max_retries_per_message" in query_limits
        ):
            merged["max_retries_per_message"] = query_limits["max_retries_per_message"]
    conversation = configured.get("conversation")
    if isinstance(conversation, dict):
        if (
            "max_recent_messages" not in configured
            and "max_recent_messages" in conversation
        ):
            merged["max_recent_messages"] = conversation["max_recent_messages"]
        if "summarize_after" not in configured and "summarize_after" in conversation:
            merged["summarize_after"] = conversation["summarize_after"]
        if "ttl_hours" not in configured and "ttl_hours" in conversation:
            merged["ttl_hours"] = conversation["ttl_hours"]
    return merged


def is_chat_enabled() -> bool:
    """Return whether chat is enabled."""
    return bool(get_chat_settings()["enabled"])


def import_provider() -> type[Any]:
    """Import the configured provider class."""
    provider_path = str(get_chat_settings()["provider"])
    try:
        provider_cls = import_string(provider_path)
    except ImportError as exc:
        module_path, _, class_name = provider_path.rpartition(".")
        if module_path:
            module = import_module(module_path)
            provider_name = class_name or provider_path
            extra_name = getattr(module, "PROVIDER_EXTRA", {}).get(provider_name)
            if extra_name is not None:
                raise ProviderDependencyError(provider_name, extra_name) from exc
        raise
    required_extra = getattr(provider_cls, "required_extra", None)
    check_configuration = getattr(provider_cls, "check_configuration", None)
    if callable(check_configuration):
        try:
            check_configuration()
        except ImportError as exc:
            if required_extra:
                raise ProviderDependencyError(
                    getattr(provider_cls, "__name__", provider_path),
                    str(required_extra),
                ) from exc
            raise
    return provider_cls


def get_permission_callable() -> Any:
    """Import the optional endpoint permission callable."""
    permission = get_chat_settings()["permission"]
    if permission is None:
        return None
    if callable(permission):
        return permission
    if not isinstance(permission, str):
        raise ChatConfigurationError.invalid_permission()
    return import_string(permission)


def get_chat_permission() -> Any:
    """Return the optional chat endpoint permission callable."""
    return get_permission_callable()


def validate_chat_settings() -> dict[str, Any]:
    """Validate chat settings and return the normalized configuration."""
    settings = get_chat_settings()
    schema = GraphQL.get_schema()
    if schema is None:
        raise ChatConfigurationError.missing_graphql_schema()
    import_provider()
    get_permission_callable()
    mutation_type = getattr(schema.graphql_schema, "mutation_type", None)
    available_mutations = (
        set(mutation_type.fields.keys()) if mutation_type is not None else set()
    )
    allowed_mutations = set(settings["allowed_mutations"])
    unknown_mutations = sorted(allowed_mutations - available_mutations)
    if unknown_mutations:
        suggestions: list[str] = []
        for name in unknown_mutations:
            matches = get_close_matches(name, sorted(available_mutations), n=1)
            if matches:
                suggestions.append(f"{name} (did you mean: {matches[0]}?)")
            else:
                suggestions.append(name)
        joined = ", ".join(suggestions)
        raise ChatConfigurationError.unknown_allowed_mutations(joined)
    confirm_mutations = set(settings["confirm_mutations"])
    unknown_confirmations = sorted(confirm_mutations - allowed_mutations)
    if unknown_confirmations:
        joined = ", ".join(unknown_confirmations)
        raise ChatConfigurationError.invalid_confirm_mutations(joined)
    return settings
