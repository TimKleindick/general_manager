"""Tests for file-upload startup validation."""

from __future__ import annotations

from unittest.mock import patch
from types import SimpleNamespace
from typing import ClassVar

import pytest

from django.core.checks import Error
from django.core.files.storage import Storage
from django.db import models

from general_manager.uploads.config import FileUploadPolicy
from general_manager.uploads import services
from general_manager.uploads.checks import (
    _AdapterCapabilities,
    register_upload_checks,
    run_upload_checks,
)


def test_system_check_rejects_other_manager_database() -> None:
    errors = run_upload_checks(
        manager_database="analytics",
        intent_database="default",
    )

    assert errors == [
        Error(
            "Upload-enabled managers must use the upload intent database.",
            id="general_manager.uploads.E001",
        )
    ]


def test_system_check_normalizes_empty_manager_database_to_default() -> None:
    assert run_upload_checks(manager_database=None, intent_database="") == []


def test_system_check_is_quiet_when_uploads_are_disabled(settings) -> None:
    settings.GENERAL_MANAGER = {
        "FILE_UPLOADS": {
            "ENABLED": False,
            "STAGING_PREFIX": "../ignored-while-disabled/",
        }
    }

    assert run_upload_checks() == []


def test_system_check_rejects_disabled_uploads_with_editable_orm_file_field(
    settings,
) -> None:
    settings.GENERAL_MANAGER = {"FILE_UPLOADS": {"ENABLED": False}}
    with patch(
        "general_manager.uploads.checks.services._resolve_file_field",
        return_value=(models.Model, models.ImageField()),
    ):
        errors = run_upload_checks(managers=(_FakeManager,))

    assert errors == [
        Error(
            "Editable GraphQL file fields require file uploads to be enabled.",
            id="general_manager.uploads.E006",
        )
    ]


def test_system_check_disabled_uploads_ignore_managers_without_editable_file_fields(
    settings,
) -> None:
    settings.GENERAL_MANAGER = {"FILE_UPLOADS": {"ENABLED": False}}

    class Interface:
        @classmethod
        def get_attribute_types(cls):
            return {"label": {"is_editable": True}}

    class Manager:
        pass

    Manager.Interface = Interface

    assert run_upload_checks(managers=(Manager,)) == []


def test_system_check_rejects_retention_not_exceeding_download_ttl(settings) -> None:
    settings.GENERAL_MANAGER = {
        "FILE_UPLOADS": {
            "ENABLED": True,
            "DOWNLOAD_URL_TTL_SECONDS": 300,
            "TERMINAL_RETENTION_SECONDS": 300,
        }
    }

    errors = run_upload_checks(managers=())

    assert [error.id for error in errors] == ["general_manager.uploads.E002"]


def test_upload_check_registration_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr("general_manager.uploads.checks._registered", False)
    with patch("general_manager.uploads.checks.register") as register:
        register_upload_checks()
        register_upload_checks()

    register.assert_called_once_with("general_manager")


class _FakeInterface:
    database = None

    @classmethod
    def get_attribute_types(cls):
        return {
            "avatar": {
                "orm_field_kind": "image",
                "is_editable": True,
            }
        }


class _FakeManager:
    Interface = _FakeInterface

    class FileUploads:
        fields: ClassVar = {"avatar": FileUploadPolicy(public=True)}


def _enabled(settings) -> None:
    settings.GENERAL_MANAGER = {"FILE_UPLOADS": {"ENABLED": True}}


def test_system_check_rejects_unknown_policy_field(settings) -> None:
    _enabled(settings)

    class Manager(_FakeManager):
        class FileUploads:
            fields: ClassVar = {"missing": FileUploadPolicy()}

    errors = run_upload_checks(managers=(Manager,))

    assert "general_manager.uploads.E003" in {error.id for error in errors}


def test_system_check_rejects_malformed_mime_extension_and_image_policy(
    settings,
) -> None:
    _enabled(settings)

    class Manager(_FakeManager):
        class FileUploads:
            fields: ClassVar = {
                "avatar": FileUploadPolicy(
                    allowed_content_types=("text/plain", "not a mime"),
                    allowed_extensions=("../avatar",),
                )
            }

    with (
        patch(
            "general_manager.uploads.checks.services._resolve_file_field",
            return_value=(models.Model, models.ImageField()),
        ),
        patch(
            "general_manager.uploads.checks._static_adapter_capabilities",
            return_value=_AdapterCapabilities(True, True, False),
        ),
    ):
        errors = run_upload_checks(managers=(Manager,))

    assert "general_manager.uploads.E003" in {error.id for error in errors}


def test_system_check_rejects_missing_finalization_and_public_capability(
    settings,
) -> None:
    _enabled(settings)
    with (
        patch(
            "general_manager.uploads.checks.services._resolve_file_field",
            return_value=(models.Model, models.ImageField()),
        ),
        patch(
            "general_manager.uploads.checks._static_adapter_capabilities",
            return_value=_AdapterCapabilities(False, False, False),
        ),
    ):
        errors = run_upload_checks(managers=(_FakeManager,))

    assert {"general_manager.uploads.E004", "general_manager.uploads.E005"}.issubset(
        {error.id for error in errors}
    )


def test_system_check_sanitizes_hostile_adapter_failure(settings) -> None:
    _enabled(settings)
    hostile_value = "https://user:password@example.invalid/?X-Amz-Signature=secret"
    with (
        patch(
            "general_manager.uploads.checks.services._resolve_file_field",
            return_value=(models.Model, models.ImageField()),
        ),
        patch(
            "general_manager.uploads.checks._static_adapter_capabilities",
            side_effect=RuntimeError(hostile_value),
        ),
    ):
        errors = run_upload_checks(managers=(_FakeManager,))

    assert [error.id for error in errors] == ["general_manager.uploads.E004"]
    assert hostile_value not in " ".join(str(error) for error in errors)


def test_system_check_never_calls_s3_or_storage_network_capabilities(settings) -> None:
    _enabled(settings)
    calls: list[str] = []

    class Client:
        meta = SimpleNamespace(
            config=SimpleNamespace(signature_version="s3v4"),
            service_model=SimpleNamespace(
                operation_model=lambda _name: SimpleNamespace(
                    input_shape=SimpleNamespace(members={"IfNoneMatch": object()})
                )
            ),
        )

        def get_bucket_versioning(self, **_kwargs):
            calls.append("network")
            return {"Status": "Enabled"}

    class S3LikeStorage(Storage):
        _gm_s3_storage = True
        bucket_name = "bucket"
        s3_client = Client()
        versioning_enabled = True
        object_parameters: ClassVar[dict[str, object]] = {}

    class PrivateManager(_FakeManager):
        class FileUploads:
            fields: ClassVar = {"avatar": FileUploadPolicy(public=False)}

    with patch(
        "general_manager.uploads.checks.services._resolve_file_field",
        return_value=(models.Model, models.ImageField(storage=S3LikeStorage())),
    ):
        run_upload_checks(managers=(PrivateManager,))

    assert calls == []


def test_system_check_does_not_reject_valid_callable_adapter_factory(settings) -> None:
    _enabled(settings)

    class PrivateManager(_FakeManager):
        class FileUploads:
            fields: ClassVar = {"avatar": FileUploadPolicy(public=False)}

    factory = lambda storage: SimpleNamespace(storage=storage)  # noqa: E731
    with (
        patch(
            "general_manager.uploads.checks.services._resolve_file_field",
            return_value=(models.Model, models.ImageField()),
        ),
        patch(
            "general_manager.uploads.checks.services.upload_adapter_registry._resolve_explicit_factory",
            return_value=factory,
        ),
    ):
        errors = run_upload_checks(managers=(PrivateManager,))

    assert "general_manager.uploads.E004" not in {error.id for error in errors}


def test_system_check_does_not_evaluate_hostile_storage_descriptors(settings) -> None:
    _enabled(settings)
    calls: list[str] = []

    class HostileS3(Storage):
        _gm_s3_storage = True
        bucket_name = "bucket"

        @property
        def versioning_enabled(self):
            calls.append("descriptor")
            raise RuntimeError("descriptor-secret")

    class PrivateManager(_FakeManager):
        class FileUploads:
            fields: ClassVar = {"avatar": FileUploadPolicy(public=False)}

    with patch(
        "general_manager.uploads.checks.services._resolve_file_field",
        return_value=(models.Model, models.ImageField(storage=HostileS3())),
    ):
        run_upload_checks(managers=(PrivateManager,))

    assert calls == []


def test_system_check_warns_when_s3_direct_security_is_not_static(settings) -> None:
    _enabled(settings)

    class S3WithoutSigV4(Storage):
        _gm_s3_storage = True
        bucket_name = "bucket"
        versioning_enabled = True
        object_parameters: ClassVar[dict[str, object]] = {}

    class PrivateManager(_FakeManager):
        class FileUploads:
            fields: ClassVar = {"avatar": FileUploadPolicy(public=False)}

    with patch(
        "general_manager.uploads.checks.services._resolve_file_field",
        return_value=(models.Model, models.ImageField(storage=S3WithoutSigV4())),
    ):
        errors = run_upload_checks(managers=(PrivateManager,))

    assert "general_manager.uploads.W001" in {error.id for error in errors}


def test_system_check_rejects_public_builtin_s3_with_custom_domain(settings) -> None:
    _enabled(settings)

    class Client:
        meta = SimpleNamespace(
            config=SimpleNamespace(signature_version="s3v4"),
            service_model=SimpleNamespace(
                operation_model=lambda _name: SimpleNamespace(
                    input_shape=SimpleNamespace(members={"IfNoneMatch": object()})
                )
            ),
        )

    class PublicCustomDomainS3(Storage):
        _gm_s3_storage = True
        bucket_name = "bucket"
        s3_client = Client()
        versioning_enabled = True
        public = True
        upload_staging_prefix_private = True
        custom_domain = "cdn.example.test"
        object_parameters: ClassVar[dict[str, object]] = {}

    with patch(
        "general_manager.uploads.checks.services._resolve_file_field",
        return_value=(
            models.Model,
            models.ImageField(storage=PublicCustomDomainS3()),
        ),
    ):
        errors = run_upload_checks(managers=(_FakeManager,))

    assert "general_manager.uploads.E005" in {error.id for error in errors}


def test_system_check_rejects_non_string_policy_key(settings) -> None:
    _enabled(settings)

    class Manager(_FakeManager):
        class FileUploads:
            fields: ClassVar = {1: FileUploadPolicy()}

    errors = run_upload_checks(managers=(Manager,))

    assert "general_manager.uploads.E003" in {error.id for error in errors}


def test_system_check_rejects_malformed_unmatched_registration(settings) -> None:
    _enabled(settings)
    registrations = {Storage: "not-callable"}
    with patch.object(
        services.upload_adapter_registry,
        "_registrations",
        registrations,
    ):
        errors = run_upload_checks(managers=())

    assert "general_manager.uploads.E004" in {error.id for error in errors}


def test_system_check_accepts_static_public_factory_capability(settings) -> None:
    _enabled(settings)
    factory_called = "system checks must not call factories"

    class PropertyAdapterFactory:
        upload_adapter_capabilities: ClassVar = {
            "adapter_id": "custom-public",
            "adapter_version": 1,
            "finalization": True,
            "public": True,
        }

        def __call__(self, storage):
            raise AssertionError(factory_called)

    with (
        patch(
            "general_manager.uploads.checks.services._resolve_file_field",
            return_value=(models.Model, models.ImageField()),
        ),
        patch(
            "general_manager.uploads.checks.services.upload_adapter_registry._resolve_explicit_factory",
            return_value=PropertyAdapterFactory(),
        ),
    ):
        errors = run_upload_checks(managers=(_FakeManager,))

    assert "general_manager.uploads.E005" not in {error.id for error in errors}


@pytest.mark.parametrize(
    ("adapter_id", "adapter_version"),
    (("unsafe/id", 1), ("safe-id", True), ("safe-id", 0)),
)
def test_system_check_rejects_class_factory_identity_runtime_would_reject(
    settings, adapter_id, adapter_version
) -> None:
    _enabled(settings)

    class InvalidAdapterFactory:
        pass

    InvalidAdapterFactory.adapter_id = adapter_id
    InvalidAdapterFactory.adapter_version = adapter_version
    with (
        patch(
            "general_manager.uploads.checks.services._resolve_file_field",
            return_value=(models.Model, models.ImageField()),
        ),
        patch(
            "general_manager.uploads.checks.services.upload_adapter_registry._resolve_explicit_factory",
            return_value=InvalidAdapterFactory,
        ),
    ):
        errors = run_upload_checks(managers=(_FakeManager,))

    assert "general_manager.uploads.E004" in {error.id for error in errors}
