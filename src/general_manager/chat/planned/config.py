"""Normalized provider-profile settings for planned chat."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from inspect import signature
from math import isfinite
from types import MappingProxyType
from typing import Any, cast

from django.utils.module_loading import import_string

from general_manager.chat.providers.base import BaseLLMProvider
from general_manager.chat.settings import ChatConfigurationError, get_chat_settings


REQUIRED_ROLES = (
    "planner",
    "simple_executor",
    "complex_executor",
    "synthesizer",
    "fallback_executor",
)

_POSITIVE_INTEGER = "{key} must be a positive integer."
_POSITIVE_NUMBER = "{key} must be a positive number."
_ENABLED_BOOLEAN = "enabled must be a boolean."
_PROFILES_MAPPING = "provider_profiles must be a mapping."
_PROFILE_NAME = "provider profile names must be non-empty strings."
_PROFILE_MAPPING = "provider profile {name!r} must be a mapping."
_PROFILE_PROVIDER = "provider profile {name!r} must define a provider."
_PROFILE_CONFIG = "provider profile {name!r} provider_config must be a mapping."
_PROFILE_TRUST_GROUP = "provider profile {name!r} must define a trust_group."
_PLANNED_MAPPING = "planned must be a mapping."
_ROLES_MAPPING = "roles must be a mapping."
_REQUIRED_ROLES_MESSAGE = "roles must define all required roles: {roles}."
_ROLE_PROFILE = "roles must map role names to profile names."
_UNKNOWN_ROLES = "roles contain unknown roles: {roles}."
_UNKNOWN_PROFILE = "role {role!r} references unknown profile {profile_name!r}."
_ONE_TRUST_GROUP = "all mapped profiles must use one trust_group."
_UNKNOWN_ROLE = "unknown role {role!r}."
_CONFIGURED_CONSTRUCTION = (
    "provider profile {profile_name!r} requires configured construction."
)


@dataclass(frozen=True)
class ProviderProfile:
    """One configured provider available to planned chat."""

    name: str
    provider_path: str
    provider_config: Mapping[str, Any]
    trust_group: str
    configured: bool = False


@dataclass(frozen=True)
class PlannedChatSettings:
    """Immutable normalized settings used by planned chat."""

    enabled: bool
    profiles: Mapping[str, ProviderProfile]
    roles: Mapping[str, str]
    catalog_source: object
    max_concurrent_tasks: int = 3
    evidence_timeout_seconds: float = 90.0
    synthesis_timeout_seconds: float = 30.0


def _error(detail: str) -> ChatConfigurationError:
    return ChatConfigurationError.invalid_planned_settings(detail)


def _detail(template: str, **values: object) -> str:
    return template.format(**values)


def _required_roles(profile_name: str) -> dict[str, str]:
    return {role: profile_name for role in REQUIRED_ROLES}


def _positive_int(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _error(_detail(_POSITIVE_INTEGER, key=key))
    return value


def _positive_float(value: object, key: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise _error(_detail(_POSITIVE_NUMBER, key=key))
    return float(value)


def _implicit_profile(settings: Mapping[str, Any]) -> ProviderProfile:
    configured = settings.get("provider_config", {})
    provider_config = configured if isinstance(configured, Mapping) else {}
    return ProviderProfile(
        name="default",
        provider_path=str(settings["provider"]),
        provider_config=MappingProxyType(deepcopy(dict(provider_config))),
        trust_group="default",
        configured=False,
    )


def _copy_catalog_source(source: object) -> object:
    """Detach mapping-backed catalog configuration from mutable settings."""
    if isinstance(source, Mapping):
        return deepcopy(dict(source))
    return source


def _normalize_profiles(
    configured_profiles: object,
    settings: Mapping[str, Any],
) -> tuple[Mapping[str, ProviderProfile], bool]:
    if configured_profiles is None:
        configured_profiles = {}
    if not isinstance(configured_profiles, Mapping):
        raise _error(_PROFILES_MAPPING)
    if not configured_profiles:
        profile = _implicit_profile(settings)
        return MappingProxyType({profile.name: profile}), False

    profiles: dict[str, ProviderProfile] = {}
    for name, raw_profile in configured_profiles.items():
        if not isinstance(name, str) or not name:
            raise _error(_PROFILE_NAME)
        if not isinstance(raw_profile, Mapping):
            raise _error(_detail(_PROFILE_MAPPING, name=name))
        provider_path = raw_profile.get("provider")
        if not isinstance(provider_path, str) or not provider_path:
            raise _error(_detail(_PROFILE_PROVIDER, name=name))
        provider_config = raw_profile.get("provider_config", {})
        if not isinstance(provider_config, Mapping):
            raise _error(_detail(_PROFILE_CONFIG, name=name))
        trust_group = raw_profile.get("trust_group")
        if not isinstance(trust_group, str) or not trust_group:
            raise _error(_detail(_PROFILE_TRUST_GROUP, name=name))
        profiles[name] = ProviderProfile(
            name=name,
            provider_path=provider_path,
            provider_config=MappingProxyType(deepcopy(dict(provider_config))),
            trust_group=trust_group,
            configured=True,
        )
    return MappingProxyType(profiles), True


def get_planned_chat_settings() -> PlannedChatSettings:
    """Return immutable planned-chat settings normalized from chat settings."""
    settings = get_chat_settings()
    raw_planned = settings.get("planned", {})
    if not isinstance(raw_planned, Mapping):
        raise _error(_PLANNED_MAPPING)
    enabled = raw_planned.get("enabled", False)
    if not isinstance(enabled, bool):
        raise _error(_ENABLED_BOOLEAN)

    if not enabled:
        implicit_profile = _implicit_profile(settings)
        return PlannedChatSettings(
            enabled=False,
            profiles=MappingProxyType({implicit_profile.name: implicit_profile}),
            roles=MappingProxyType(_required_roles(implicit_profile.name)),
            catalog_source=_copy_catalog_source(raw_planned.get("catalog")),
        )

    profiles, has_explicit_profiles = _normalize_profiles(
        settings.get("provider_profiles", {}), settings
    )
    raw_roles = raw_planned.get("roles")
    if raw_roles is None and not has_explicit_profiles:
        raw_roles = _required_roles("default")
    if not isinstance(raw_roles, Mapping):
        raise _error(_ROLES_MAPPING)
    missing_roles = [role for role in REQUIRED_ROLES if role not in raw_roles]
    if missing_roles:
        raise _error(_detail(_REQUIRED_ROLES_MESSAGE, roles=", ".join(missing_roles)))
    unknown_roles = sorted(set(raw_roles) - set(REQUIRED_ROLES))
    if unknown_roles:
        raise _error(_detail(_UNKNOWN_ROLES, roles=", ".join(unknown_roles)))
    roles: dict[str, str] = {}
    for role, profile_name in raw_roles.items():
        if not isinstance(role, str) or not isinstance(profile_name, str):
            raise _error(_ROLE_PROFILE)
        if profile_name not in profiles:
            raise _error(
                _detail(_UNKNOWN_PROFILE, role=role, profile_name=profile_name)
            )
        roles[role] = profile_name
    mapped_trust_groups = {profiles[roles[role]].trust_group for role in REQUIRED_ROLES}
    if len(mapped_trust_groups) != 1:
        raise _error(_ONE_TRUST_GROUP)

    return PlannedChatSettings(
        enabled=True,
        profiles=profiles,
        roles=MappingProxyType(roles),
        catalog_source=_copy_catalog_source(raw_planned.get("catalog")),
        max_concurrent_tasks=_positive_int(
            raw_planned.get("max_concurrent_tasks", 3), "max_concurrent_tasks"
        ),
        evidence_timeout_seconds=_positive_float(
            raw_planned.get("evidence_timeout_seconds", 90.0),
            "evidence_timeout_seconds",
        ),
        synthesis_timeout_seconds=_positive_float(
            raw_planned.get("synthesis_timeout_seconds", 30.0),
            "synthesis_timeout_seconds",
        ),
    )


def profile_for_role(settings: PlannedChatSettings, role: str) -> ProviderProfile:
    """Return the configured provider profile assigned to a planned-chat role."""
    try:
        return settings.profiles[settings.roles[role]]
    except KeyError as exc:
        raise _error(_detail(_UNKNOWN_ROLE, role=role)) from exc


def build_profile_provider(profile: ProviderProfile) -> BaseLLMProvider:
    """Build a provider from a profile without changing legacy settings."""
    provider_cls = import_string(profile.provider_path)
    if profile.configured or profile.provider_config:
        from_config = getattr(provider_cls, "from_config", None)
        if not callable(from_config):
            raise _error(_detail(_CONFIGURED_CONSTRUCTION, profile_name=profile.name))
        return cast(BaseLLMProvider, from_config(profile.provider_config))
    return cast(BaseLLMProvider, provider_cls())


def validate_profile_provider(profile: ProviderProfile) -> BaseLLMProvider:
    """Build a profile provider and run its supported configuration check."""
    provider = build_profile_provider(profile)
    check_configuration = getattr(provider, "check_configuration", None)
    if not callable(check_configuration):
        return provider
    try:
        signature(check_configuration).bind(profile.provider_config)
    except TypeError:
        check_configuration()
    else:
        check_configuration(profile.provider_config)
    return provider
