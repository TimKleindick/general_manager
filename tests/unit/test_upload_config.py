from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_manager.uploads.config import (
    FileUploadConfigurationError,
    FileUploadPolicy,
    get_file_upload_settings,
    merge_file_upload_policy,
)


def test_upload_settings_default_to_secure_finite_values(settings) -> None:
    settings.GENERAL_MANAGER = {}

    value = get_file_upload_settings()

    assert value.enabled is False
    assert value.intent_database == "default"
    assert value.max_bytes > 0
    assert value.max_pending_intents_per_user > 0
    assert value.download_url_ttl_seconds > 0


def test_file_policy_overrides_global_limits_without_mutating_defaults() -> None:
    base = FileUploadPolicy(max_bytes=25_000_000, public=False)
    override = FileUploadPolicy(max_bytes=5_000_000)

    merged = merge_file_upload_policy(base, override)

    assert merged.max_bytes == 5_000_000
    assert merged.public is False
    assert base.max_bytes == 25_000_000
    with pytest.raises(FrozenInstanceError):
        base.max_bytes = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "configured",
    [
        {"UNKNOWN": True},
        {"MAX_BYTES": True},
        {"MAX_BYTES": 0},
        {"MAX_PENDING_INTENTS_PER_USER": -1},
        {"DOWNLOAD_URL_TTL_SECONDS": 0},
        {"HTTP_UPLOAD_PATH": "../uploads/"},
        {"STAGING_PREFIX": "/gm-staging/"},
    ],
)
def test_upload_settings_reject_invalid_values(settings, configured: object) -> None:
    settings.GENERAL_MANAGER = {"FILE_UPLOADS": configured}

    with pytest.raises(FileUploadConfigurationError):
        get_file_upload_settings()


@pytest.mark.parametrize("configured", [None, [], "uploads"])
def test_upload_settings_require_a_mapping(settings, configured: object) -> None:
    settings.GENERAL_MANAGER = {"FILE_UPLOADS": configured}

    with pytest.raises(FileUploadConfigurationError):
        get_file_upload_settings()


@pytest.mark.parametrize("alias", [None, ""])
def test_upload_settings_normalize_empty_database_aliases(
    settings, alias: object
) -> None:
    settings.GENERAL_MANAGER = {"FILE_UPLOADS": {"INTENT_DATABASE": alias}}

    assert get_file_upload_settings().intent_database == "default"
