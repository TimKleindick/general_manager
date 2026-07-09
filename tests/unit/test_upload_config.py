from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from general_manager.uploads.config import (
    FileUploadConfigurationError,
    FileUploadPolicy,
    FileUploadSettings,
    get_file_upload_settings,
    merge_file_upload_policy,
)


def test_upload_settings_default_to_secure_finite_values(settings) -> None:
    settings.GENERAL_MANAGER = {}

    value = get_file_upload_settings()

    assert value == FileUploadSettings(
        enabled=False,
        http_upload_path="gm/uploads/",
        staging_prefix="gm-staging/",
        intent_database="default",
        max_bytes=25_000_000,
        max_pending_intents_per_user=20,
        max_pending_bytes_per_user=100_000_000,
        max_image_pixels=40_000_000,
        token_ttl_seconds=900,
        download_url_ttl_seconds=300,
        delete_replaced_files=False,
    )
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


def test_file_policy_defensively_copies_allowed_value_sequences() -> None:
    content_types = ["image/png"]
    extensions = [".png"]

    policy = FileUploadPolicy(
        allowed_content_types=content_types,
        allowed_extensions=extensions,
    )
    content_types.append("image/jpeg")
    extensions.append(".jpg")

    assert policy.allowed_content_types == ("image/png",)
    assert policy.allowed_extensions == (".png",)


@pytest.mark.parametrize(
    ("content_types", "extensions"),
    [
        ([], None),
        ([""], None),
        (["   "], None),
        ([1], None),
        ({"image/png"}, None),
        (object(), None),
        (None, []),
        (None, [""]),
        (None, [object()]),
    ],
)
def test_file_policy_rejects_empty_or_non_string_allowed_values(
    content_types: object,
    extensions: object,
) -> None:
    with pytest.raises(FileUploadConfigurationError):
        FileUploadPolicy(
            allowed_content_types=content_types,  # type: ignore[arg-type]
            allowed_extensions=extensions,  # type: ignore[arg-type]
        )


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


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("STAGING_PREFIX", "gm\x00-staging/"),
        ("HTTP_UPLOAD_PATH", "gm/\nuploads/"),
        ("STAGING_PREFIX", "%2e%2E/gm-staging/"),
        ("STAGING_PREFIX", "gm%2fstaging/"),
        ("STAGING_PREFIX", "gm%5Cstaging/"),
        ("HTTP_UPLOAD_PATH", "http://[/"),
    ],
)
def test_upload_settings_reject_encoded_or_malformed_paths_as_unsafe(
    settings,
    name: str,
    value: str,
) -> None:
    settings.GENERAL_MANAGER = {"FILE_UPLOADS": {name: value}}

    with pytest.raises(
        FileUploadConfigurationError,
        match=rf"^{name} must be a safe relative path\.$",
    ):
        get_file_upload_settings()


@pytest.mark.parametrize(
    "value",
    [
        "gm/\x85/",
        "gm/\u202e/",
        "gm/\ud800/",
        "gm/\ue000/",
        "gm/\u0378/",
        "gm/%C2%85/",
        "gm/%E2%80%AE/",
        "gm/%EE%80%80/",
        "gm/%CD%B8/",
        "gm/%FF/",
        "gm/%E2%80/",
        "gm/%GG/",
    ],
)
def test_upload_settings_reject_unicode_controls_and_malformed_escapes(
    settings,
    value: str,
) -> None:
    settings.GENERAL_MANAGER = {"FILE_UPLOADS": {"STAGING_PREFIX": value}}

    with pytest.raises(
        FileUploadConfigurationError,
        match=r"^STAGING_PREFIX must be a safe relative path\.$",
    ):
        get_file_upload_settings()


def test_upload_settings_allow_printable_unicode_path_segments(settings) -> None:
    settings.GENERAL_MANAGER = {"FILE_UPLOADS": {"STAGING_PREFIX": "gm/über/資料/"}}

    assert get_file_upload_settings().staging_prefix == "gm/über/資料/"


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
