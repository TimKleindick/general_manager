"""Tests for secure creation of field-bound upload intents."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import os
import subprocess
import sys
import tempfile
import textwrap
from threading import Barrier
import traceback
from typing import ClassVar
from unittest.mock import patch
from urllib.parse import quote
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.cache.backends.base import BaseCache, DEFAULT_TIMEOUT
from django.core.files.storage import FileSystemStorage
from django.db import (
    OperationalError,
    close_old_connections,
    connections,
    models,
    transaction,
)
from django.test import (
    SimpleTestCase,
    skipUnlessDBFeature,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.utils import timezone

from general_manager.api.graphql import GraphQL
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.uploads.adapters import (
    ProxyUploadAdapter,
    UploadAdapterRegistry,
    UploadInstructions,
)
from general_manager.uploads.config import FileUploadPolicy
from general_manager.uploads.errors import (
    InvalidFileTypeError,
    InvalidUploadChecksumError,
    InvalidUploadFilenameError,
    InvalidUploadSizeError,
    UploadAuthenticationError,
    UploadDatabaseMismatchError,
    UploadFieldInvalidError,
    UploadManagerInvalidError,
    UploadOperationInvalidError,
    UploadQuotaExceededError,
    UploadRateLimitExceededError,
    UploadStorageError,
    UploadTargetUnavailableError,
    UploadError,
)
from general_manager.uploads.models import UploadIntent
from general_manager.uploads.services import (
    BeginFileUploadRequest,
    UploadChecksum,
    begin_file_upload,
    set_begin_upload_rate_limit_hook,
)
from general_manager.uploads.types import (
    ChecksumAlgorithm,
    UploadIntentState,
    UploadOperation,
    UploadTransport,
)


_STORAGE = FileSystemStorage(
    location=f"{tempfile.gettempdir()}/general-manager-upload-service-tests"
)


class NonAtomicIncrementCache(BaseCache):
    """Backend double whose inherited ``incr`` is get-plus-set."""

    def __init__(self, location: str, params: dict[str, object]) -> None:
        del location
        super().__init__(params)

    def add(
        self,
        key: str,
        value: object,
        timeout: object = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> bool:
        del key, value, timeout, version
        return True

    def get(
        self,
        key: str,
        default: object = None,
        version: int | None = None,
    ) -> object:
        del key, version
        return default

    def set(
        self,
        key: str,
        value: object,
        timeout: object = DEFAULT_TIMEOUT,
        version: int | None = None,
    ) -> None:
        del key, value, timeout, version


class RaisingCache(BaseCache):
    """Backend double that fails before a counter can be inspected."""

    def __init__(self, location: str, params: dict[str, object]) -> None:
        del location, params
        raise RuntimeError("cache-constructor-secret-must-not-escape")


class UploadProfileRecord(models.Model):
    avatar = models.ImageField(storage=_STORAGE, upload_to="avatars/")
    document = models.FileField(storage=_STORAGE, upload_to="documents/")
    locked_file = models.FileField(
        storage=_STORAGE,
        upload_to="locked/",
        editable=False,
    )
    label = models.CharField(max_length=255)

    class Meta:
        app_label = "general_manager"
        managed = False


class UploadProfileInterface(OrmInterfaceBase[UploadProfileRecord]):
    _model = UploadProfileRecord
    database: ClassVar[str | None] = None
    supported_operations: ClassVar[frozenset[str]] = frozenset({"create", "update"})
    input_fields: ClassVar[dict[str, Input[type[object]]]] = {
        "id": Input(int)  # type: ignore[dict-item]
    }

    def __init__(self, id: object) -> None:
        canonical_id = int(id)  # type: ignore[arg-type]
        if canonical_id != 7:
            raise UploadProfileRecord.DoesNotExist
        self.identification = {"id": canonical_id}

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        return {
            "avatar": {
                "type": str,
                "orm_field_kind": "image",
                "is_editable": True,
            },
            "document": {
                "type": str,
                "orm_field_kind": "file",
                "is_editable": True,
            },
            "locked_file": {
                "type": str,
                "orm_field_kind": "file",
                "is_editable": False,
            },
            "label": {"type": str, "is_editable": True},
        }

    @classmethod
    def get_capabilities(cls) -> frozenset[str]:  # type: ignore[override]
        return cls.supported_operations


class UploadProfile(GeneralManager):
    class FileUploads:
        fields: ClassVar[dict[str, FileUploadPolicy]] = {
            "avatar": FileUploadPolicy(
                max_bytes=10,
                allowed_content_types=("image/png",),
                allowed_extensions=(".png",),
                public=False,
            )
        }


UploadProfile.Interface = UploadProfileInterface  # type: ignore[assignment]


class RecordingAdapter(ProxyUploadAdapter):
    adapter_id = "tests.recording"
    adapter_version = 9

    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        self.instruction_kwargs = kwargs
        required_headers = dict(kwargs["headers"])  # type: ignore[arg-type]
        return UploadInstructions(
            transport=UploadTransport.PROXY,
            method="PUT",
            url="/safe-upload-url",
            headers={
                **required_headers,
                "X-Upload-Authorization": "temporary-transfer-secret",
            },
        )

    def storage_fingerprint(self) -> str:
        return "sha256:test-storage-fingerprint"


class FailingFingerprintAdapter(RecordingAdapter):
    def storage_fingerprint(self) -> str:
        raise RuntimeError("storage-credential-must-not-escape")


class InvalidIdentityAdapter(RecordingAdapter):
    adapter_id = ""
    adapter_version = True  # type: ignore[assignment]


class NonInstructionAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        del kwargs
        return object()  # type: ignore[return-value]


class FieldExposingAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        return UploadInstructions(
            transport=UploadTransport.PROXY,
            method="PUT",
            url="/safe-upload-url",
            fields={"key": str(kwargs["stage_key"])},
        )


class InvalidHeaderAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        del kwargs
        return UploadInstructions(
            transport=UploadTransport.PROXY,
            method="PUT",
            url="/safe-upload-url",
            headers={"Bad\nHeader": "value"},
        )


class InvalidMethodAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        del kwargs
        return UploadInstructions(
            transport=UploadTransport.PROXY,
            method="POST",
            url="/safe-upload-url",
        )


class RaisingInstructionAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        del kwargs
        raise RuntimeError("adapter-credential-must-not-escape")


class HostileUploadError(UploadError):
    code = "SECRET_ERROR_CODE"
    default_message = "hook-or-adapter-secret-must-not-escape"


class HostileUploadErrorAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        del kwargs
        error = HostileUploadError()
        error.__cause__ = RuntimeError("adapter-cause-secret-must-not-escape")
        raise error


class MutatingIdentityAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        self.adapter_id = "tests.changed"  # type: ignore[misc]
        return super().create_upload_instructions(**kwargs)


class RaisingMutatedIdentityAdapter(RecordingAdapter):
    identity_unavailable = False

    def __getattribute__(self, name: str) -> object:
        if name == "adapter_id" and object.__getattribute__(
            self, "identity_unavailable"
        ):
            raise RuntimeError("adapter-identity-secret-must-not-escape")
        return super().__getattribute__(name)

    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        instructions = super().create_upload_instructions(**kwargs)
        self.identity_unavailable = True
        return instructions


class EncodedStageUrlAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        encoded_key = quote(str(kwargs["stage_key"]), safe="").replace("%2F", "%2f")
        return UploadInstructions(
            transport=UploadTransport.PROXY,
            method="PUT",
            url=f"/upload/{encoded_key}",
        )


class ReplacedAuthorizationAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        del kwargs
        return UploadInstructions(
            transport=UploadTransport.PROXY,
            method="PUT",
            url="/safe-upload-url",
            headers={"Authorization": "GMUpload replaced"},
        )


class DuplicateAuthorizationAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        expected = dict(kwargs["headers"])  # type: ignore[arg-type]
        return UploadInstructions(
            transport=UploadTransport.PROXY,
            method="PUT",
            url="/safe-upload-url",
            headers={
                **expected,
                "authorization": expected["Authorization"],
            },
        )


class InsecureDirectAdapter(RecordingAdapter):
    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        del kwargs
        return UploadInstructions(
            transport=UploadTransport.DIRECT,
            method="PUT",
            url="http://objects.example.test/upload?signature=secret",
            headers={"Content-Type": "image/png"},
        )


class UnsafeIdentityAdapter(RecordingAdapter):
    adapter_id = "tests unsafe\nidentity"


def valid_request(**overrides: object) -> BeginFileUploadRequest:
    request = BeginFileUploadRequest(
        manager="UploadProfile",
        field="avatar",
        operation=UploadOperation.CREATE,
        object_id=None,
        filename="avatar.png",
        size=3,
        content_type="image/png",
        checksum=UploadChecksum(
            algorithm=ChecksumAlgorithm.SHA256,
            digest="a" * 64,
        ),
    )
    return replace(request, **overrides)


def assert_safe_exception(error: BaseException, *, marker: str) -> None:
    """Assert a public upload error retains no untrusted exception chain."""

    assert error.__cause__ is None
    assert error.__context__ is None
    assert marker not in str(error)
    assert marker not in repr(error)
    assert marker not in "".join(traceback.format_exception(error))


def test_begin_request_defaults_optional_create_target_to_none() -> None:
    request = BeginFileUploadRequest(
        manager="UploadProfile",
        field="avatar",
        operation=UploadOperation.CREATE,
        filename="avatar.png",
        size=3,
        content_type="image/png",
        checksum=UploadChecksum(
            algorithm=ChecksumAlgorithm.SHA256,
            digest="a" * 64,
        ),
    )

    assert request.object_id is None


def test_transfer_credentials_are_bound_distinct_tamper_safe_and_expiring() -> None:
    from general_manager.uploads import services

    intent_id = UUID("00000000-0000-0000-0000-000000000007")
    with patch("django.core.signing.time.time", return_value=100):
        credential = services.issue_upload_transfer_credential(
            intent_id=intent_id,
            owner_pk=42,
            adapter_id="proxy",
        )

    with patch("django.core.signing.time.time", return_value=104):
        assert services.verify_upload_transfer_credential(
            credential,
            intent_id=intent_id,
            owner_pk=42,
            adapter_id="proxy",
            max_age=5,
        )
        assert not services.verify_upload_transfer_credential(
            credential,
            intent_id=intent_id,
            owner_pk=99,
            adapter_id="proxy",
            max_age=5,
        )
        assert not services.verify_upload_transfer_credential(
            credential + "tampered",
            intent_id=intent_id,
            owner_pk=42,
            adapter_id="proxy",
            max_age=5,
        )
    with patch("django.core.signing.time.time", return_value=106):
        assert not services.verify_upload_transfer_credential(
            credential,
            intent_id=intent_id,
            owner_pk=42,
            adapter_id="proxy",
            max_age=5,
        )


@override_settings(
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "MAX_BYTES": 100,
            "MAX_PENDING_INTENTS_PER_USER": 2,
            "MAX_PENDING_BYTES_PER_USER": 10,
        }
    }
)
class BeginFileUploadTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.user = get_user_model().objects.create_user(username="upload-service")

    def setUp(self) -> None:
        cache.clear()
        GraphQL.manager_registry = {"UploadProfile": UploadProfile}

    def tearDown(self) -> None:
        cache.clear()
        GraphQL.reset_registry()

    def test_creation_binds_owner_manager_field_operation_and_digest(self) -> None:
        result = begin_file_upload(user=self.user, request=valid_request())

        intent = UploadIntent.objects.get(pk=result.intent_id)
        assert intent.user == self.user
        assert (intent.manager_name, intent.field_name) == (
            "UploadProfile",
            "avatar",
        )
        assert intent.operation == UploadOperation.CREATE
        assert intent.target_id is None
        assert intent.declared_checksum_sha256 == "a" * 64
        assert intent.token_digest != result.token
        assert intent.matches_token(result.token)
        assert intent.staging_key.startswith("gm-staging/")
        assert intent.staging_key not in repr(result)

    def test_creation_requires_an_authenticated_saved_owner(self) -> None:
        unsaved = get_user_model()(username="unsaved")

        for user in (AnonymousUser(), unsaved, None):
            with self.subTest(user=user), pytest.raises(UploadAuthenticationError):
                begin_file_upload(user=user, request=valid_request())

        assert UploadIntent.objects.count() == 0

    def test_manager_field_file_kind_and_editability_are_validated(self) -> None:
        invalid_requests = (
            (valid_request(manager="Missing"), UploadManagerInvalidError),
            (valid_request(field="missing"), UploadFieldInvalidError),
            (valid_request(field="label"), UploadFieldInvalidError),
            (valid_request(field="locked_file"), UploadFieldInvalidError),
        )

        for request, error in invalid_requests:
            with self.subTest(request=request), pytest.raises(error):
                begin_file_upload(user=self.user, request=request)

    def test_operation_requires_the_correct_target_shape_and_canonicalizes_id(
        self,
    ) -> None:
        with pytest.raises(UploadOperationInvalidError):
            begin_file_upload(
                user=self.user,
                request=valid_request(object_id="7"),
            )
        with pytest.raises(UploadOperationInvalidError):
            begin_file_upload(
                user=self.user,
                request=valid_request(operation=UploadOperation.UPDATE),
            )

        result = begin_file_upload(
            user=self.user,
            request=valid_request(
                operation=UploadOperation.UPDATE,
                object_id="7",
            ),
        )

        assert UploadIntent.objects.get(pk=result.intent_id).target_id == '{"id": 7}'

    def test_operation_must_be_supported_by_the_orm_manager(self) -> None:
        UploadProfileInterface.supported_operations = frozenset({"create"})
        try:
            with pytest.raises(UploadOperationInvalidError):
                begin_file_upload(
                    user=self.user,
                    request=valid_request(
                        operation=UploadOperation.UPDATE,
                        object_id="7",
                    ),
                )
        finally:
            UploadProfileInterface.supported_operations = frozenset(
                {"create", "update"}
            )

    def test_overridden_orm_operation_is_supported_like_graphql_generation(
        self,
    ) -> None:
        original_update = UploadProfileInterface.update

        def overridden_update(self: object, **kwargs: object) -> object:
            del self, kwargs
            return object()

        UploadProfileInterface.update = overridden_update  # type: ignore[method-assign]
        UploadProfileInterface.supported_operations = frozenset({"create"})
        try:
            begin_file_upload(
                user=self.user,
                request=valid_request(
                    operation=UploadOperation.UPDATE,
                    object_id="7",
                ),
            )
        finally:
            UploadProfileInterface.update = original_update  # type: ignore[method-assign]
            UploadProfileInterface.supported_operations = frozenset(
                {"create", "update"}
            )

    def test_missing_and_forbidden_update_targets_share_one_safe_error(self) -> None:
        missing = valid_request(
            operation=UploadOperation.UPDATE,
            object_id="999",
        )
        with pytest.raises(UploadTargetUnavailableError) as missing_error:
            begin_file_upload(user=self.user, request=missing)

        class DenyReadPermission:
            def __init__(self, instance: object, user: object) -> None:
                del instance, user

            def can_read_instance(self) -> bool:
                return False

        UploadProfile.Permission = DenyReadPermission  # type: ignore[assignment]
        try:
            forbidden = valid_request(
                operation=UploadOperation.UPDATE,
                object_id="7",
            )
            with pytest.raises(UploadTargetUnavailableError) as forbidden_error:
                begin_file_upload(user=self.user, request=forbidden)
        finally:
            del UploadProfile.Permission

        assert missing_error.value.code == forbidden_error.value.code
        assert str(missing_error.value) == str(forbidden_error.value)
        assert "999" not in str(missing_error.value)

    def test_filename_rejects_paths_controls_malformed_and_overlong_values(
        self,
    ) -> None:
        invalid_filenames = [
            "../avatar.png",
            "folder/avatar.png",
            r"folder\avatar.png",
            "C:avatar.png",
            ".",
            "..",
            "bad\x00name.png",
            "bad\ud800name.png",
            "",
            "a" * 252 + ".png",
        ]
        for filename in invalid_filenames:
            with (
                self.subTest(filename=filename),
                pytest.raises(InvalidUploadFilenameError),
            ):
                begin_file_upload(
                    user=self.user,
                    request=valid_request(filename=filename),
                )

    def test_filename_is_unicode_normalized_before_persistence(self) -> None:
        result = begin_file_upload(
            user=self.user,
            request=valid_request(filename="cafe\u0301.png"),
        )

        assert UploadIntent.objects.get(pk=result.intent_id).original_filename == (
            "caf\N{LATIN SMALL LETTER E WITH ACUTE}.png"
        )

    def test_size_content_type_extension_and_checksum_policy_are_enforced(self) -> None:
        invalid_requests = (
            (valid_request(size=-1), InvalidUploadSizeError),
            (valid_request(size=True), InvalidUploadSizeError),
            (valid_request(size=11), InvalidUploadSizeError),
            (valid_request(content_type="image/jpeg"), InvalidFileTypeError),
            (valid_request(filename="avatar.jpg"), InvalidFileTypeError),
            (
                valid_request(
                    checksum=UploadChecksum(
                        algorithm=ChecksumAlgorithm.SHA256,
                        digest="not-a-digest",
                    )
                ),
                InvalidUploadChecksumError,
            ),
        )

        for request, error in invalid_requests:
            with self.subTest(request=request), pytest.raises(error):
                begin_file_upload(user=self.user, request=request)

    def test_pending_count_and_byte_quotas_are_enforced(self) -> None:
        begin_file_upload(user=self.user, request=valid_request(size=6))

        with pytest.raises(UploadQuotaExceededError):
            begin_file_upload(user=self.user, request=valid_request(size=5))

        begin_file_upload(user=self.user, request=valid_request(size=4))
        with pytest.raises(UploadQuotaExceededError):
            begin_file_upload(user=self.user, request=valid_request(size=0))

    def test_terminal_intents_do_not_consume_pending_quota(self) -> None:
        first = begin_file_upload(user=self.user, request=valid_request(size=10))
        UploadIntent.objects.filter(pk=first.intent_id).update(
            state=UploadIntentState.REJECTED
        )

        begin_file_upload(user=self.user, request=valid_request(size=10))

    def test_finalizing_intents_continue_to_consume_pending_quota(self) -> None:
        first = begin_file_upload(user=self.user, request=valid_request(size=10))
        UploadIntent.objects.filter(pk=first.intent_id).update(
            state=UploadIntentState.FINALIZING
        )

        with pytest.raises(UploadQuotaExceededError):
            begin_file_upload(user=self.user, request=valid_request(size=1))

    def test_expired_finalizing_intents_consume_quota_until_terminal(self) -> None:
        first = begin_file_upload(user=self.user, request=valid_request(size=10))
        UploadIntent.objects.filter(pk=first.intent_id).update(
            state=UploadIntentState.FINALIZING,
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        with pytest.raises(UploadQuotaExceededError):
            begin_file_upload(user=self.user, request=valid_request(size=1))

    def test_expired_pending_intents_do_not_consume_pending_quota(self) -> None:
        first = begin_file_upload(user=self.user, request=valid_request(size=10))
        UploadIntent.objects.filter(pk=first.intent_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        begin_file_upload(user=self.user, request=valid_request(size=10))

    def test_pluggable_rate_limit_hook_runs_before_intent_creation(self) -> None:
        calls: list[tuple[object, object]] = []

        def deny(user: object, request: object) -> bool:
            calls.append((user, request))
            return True

        previous = set_begin_upload_rate_limit_hook(deny)
        request = valid_request()
        try:
            with pytest.raises(UploadRateLimitExceededError):
                begin_file_upload(user=self.user, request=request)
        finally:
            set_begin_upload_rate_limit_hook(previous)

        assert calls == [(self.user, request)]
        assert UploadIntent.objects.count() == 0

    @override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {
                "ENABLED": True,
                "BEGIN_RATE_LIMIT_WINDOW_SECONDS": 60,
                "MAX_BEGIN_ATTEMPTS_PER_USER": 1,
                "MAX_BEGIN_ATTEMPTS_GLOBAL": 10,
            }
        }
    )
    def test_default_rate_limiter_counts_invalid_requests_per_user(self) -> None:
        invalid = valid_request(manager="Missing")
        with pytest.raises(UploadManagerInvalidError):
            begin_file_upload(user=self.user, request=invalid)

        with pytest.raises(UploadRateLimitExceededError):
            begin_file_upload(user=self.user, request=invalid)

    @override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {
                "ENABLED": True,
                "BEGIN_RATE_LIMIT_WINDOW_SECONDS": 60,
                "MAX_BEGIN_ATTEMPTS_PER_USER": 10,
                "MAX_BEGIN_ATTEMPTS_GLOBAL": 1,
            }
        }
    )
    def test_default_rate_limiter_counts_invalid_requests_globally(self) -> None:
        other = get_user_model().objects.create_user(username="upload-rate-other")
        invalid = valid_request(manager="Missing")
        with pytest.raises(UploadManagerInvalidError):
            begin_file_upload(user=self.user, request=invalid)

        with pytest.raises(UploadRateLimitExceededError):
            begin_file_upload(user=other, request=invalid)

    @override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {
                "ENABLED": True,
                "BEGIN_RATE_LIMIT_WINDOW_SECONDS": 60,
                "MAX_BEGIN_ATTEMPTS_PER_USER": 10,
                "MAX_BEGIN_ATTEMPTS_GLOBAL": 1,
            }
        }
    )
    def test_global_rate_limiter_counts_anonymous_attempts(self) -> None:
        with pytest.raises(UploadAuthenticationError):
            begin_file_upload(user=AnonymousUser(), request=valid_request())

        with pytest.raises(UploadRateLimitExceededError):
            begin_file_upload(user=AnonymousUser(), request=valid_request())

    def test_cache_backend_failures_return_a_safe_stable_upload_error(self) -> None:
        unsafe_marker = "redis-password-must-not-escape"
        with (
            patch.object(
                cache,
                "add",
                side_effect=RuntimeError(unsafe_marker),
            ),
            pytest.raises(UploadStorageError) as captured,
        ):
            begin_file_upload(user=self.user, request=valid_request())

        assert captured.value.code == "UPLOAD_STORAGE_ERROR"
        assert_safe_exception(captured.value, marker=unsafe_marker)
        assert UploadIntent.objects.count() == 0

    def test_default_rate_limiter_rejects_non_atomic_increment_backends(self) -> None:
        with tempfile.TemporaryDirectory() as cache_directory:
            backend_settings = (
                {
                    "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
                    "LOCATION": cache_directory,
                },
                {
                    "BACKEND": (
                        "tests.unit.test_upload_services.NonAtomicIncrementCache"
                    ),
                    "LOCATION": "non-atomic-test-cache",
                },
            )

            for configured_backend in backend_settings:
                with (
                    self.subTest(backend=configured_backend["BACKEND"]),
                    override_settings(CACHES={"default": configured_backend}),
                    pytest.raises(UploadStorageError),
                ):
                    begin_file_upload(user=self.user, request=valid_request())

        assert UploadIntent.objects.count() == 0

    @override_settings(
        CACHES={
            "default": {
                "BACKEND": "tests.unit.test_upload_services.RaisingCache",
                "LOCATION": "broken-cache",
            }
        }
    )
    def test_rate_limit_backend_construction_failures_are_sanitized(self) -> None:
        with pytest.raises(UploadStorageError) as captured:
            begin_file_upload(user=self.user, request=valid_request())

        assert_safe_exception(
            captured.value,
            marker="cache-constructor-secret-must-not-escape",
        )
        assert UploadIntent.objects.count() == 0

    @override_settings(
        CACHES={
            "default": {
                "BACKEND": ("tests.unit.test_upload_services.NonAtomicIncrementCache"),
                "LOCATION": "non-atomic-test-cache",
            }
        }
    )
    def test_rate_limit_hook_is_the_escape_hatch_for_non_atomic_caches(self) -> None:
        previous = set_begin_upload_rate_limit_hook(lambda _user, _request: None)
        try:
            begin_file_upload(user=self.user, request=valid_request())
        finally:
            set_begin_upload_rate_limit_hook(previous)

        assert UploadIntent.objects.count() == 1

    @override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {
                "ENABLED": True,
                "BEGIN_RATE_LIMIT_WINDOW_SECONDS": 60,
                "MAX_BEGIN_ATTEMPTS_PER_USER": 10,
                "MAX_BEGIN_ATTEMPTS_GLOBAL": 1,
            }
        }
    )
    def test_locmem_rate_limiter_enforces_the_exact_concurrent_limit(self) -> None:
        barrier = Barrier(2)
        invalid = valid_request(manager="Missing")

        def attempt() -> type[BaseException] | None:
            try:
                barrier.wait(timeout=5)
                begin_file_upload(user=self.user, request=invalid)
            except BaseException as error:  # noqa: BLE001 - records thread outcome
                return type(error)
            return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _index: attempt(), range(2)))

        assert outcomes.count(UploadManagerInvalidError) == 1
        assert outcomes.count(UploadRateLimitExceededError) == 1

    def test_injected_rate_limit_failures_are_sanitized(self) -> None:
        unsafe_marker = "hook-backend-password-must-not-escape"

        def broken_hook(user: object, request: object) -> None:
            del user, request
            raise RuntimeError(unsafe_marker)

        previous = set_begin_upload_rate_limit_hook(broken_hook)
        try:
            with pytest.raises(UploadStorageError) as captured:
                begin_file_upload(user=self.user, request=valid_request())
        finally:
            set_begin_upload_rate_limit_hook(previous)

        assert_safe_exception(captured.value, marker=unsafe_marker)

    def test_injected_rate_limit_decision_failures_are_sanitized(self) -> None:
        class BrokenDecision:
            def __bool__(self) -> bool:
                raise RuntimeError("hook-decision-secret-must-not-escape")

        previous = set_begin_upload_rate_limit_hook(
            lambda _user, _request: BrokenDecision()
        )
        try:
            with pytest.raises(UploadStorageError) as captured:
                begin_file_upload(user=self.user, request=valid_request())
        finally:
            set_begin_upload_rate_limit_hook(previous)

        assert "hook-decision-secret-must-not-escape" not in str(captured.value)
        assert UploadIntent.objects.count() == 0

    def test_injected_hook_cannot_publish_a_custom_upload_error(self) -> None:
        unsafe_marker = "hook-or-adapter-secret-must-not-escape"

        def hostile_hook(_user: object, _request: object) -> None:
            error = HostileUploadError()
            error.__cause__ = RuntimeError("hook-cause-secret-must-not-escape")
            raise error

        previous = set_begin_upload_rate_limit_hook(hostile_hook)
        try:
            with pytest.raises(UploadStorageError) as captured:
                begin_file_upload(user=self.user, request=valid_request())
        finally:
            set_begin_upload_rate_limit_hook(previous)

        assert type(captured.value) is UploadStorageError
        assert captured.value.code == "UPLOAD_STORAGE_ERROR"
        assert_safe_exception(captured.value, marker=unsafe_marker)
        assert_safe_exception(
            captured.value,
            marker="hook-cause-secret-must-not-escape",
        )

    def test_injected_hook_rate_limit_decision_is_reissued_without_details(
        self,
    ) -> None:
        unsafe_marker = "limiter-detail-must-not-escape"

        def denying_hook(_user: object, _request: object) -> None:
            raise UploadRateLimitExceededError(unsafe_marker)

        previous = set_begin_upload_rate_limit_hook(denying_hook)
        try:
            with pytest.raises(UploadRateLimitExceededError) as captured:
                begin_file_upload(user=self.user, request=valid_request())
        finally:
            set_begin_upload_rate_limit_hook(previous)

        assert type(captured.value) is UploadRateLimitExceededError
        assert str(captured.value) == UploadRateLimitExceededError.default_message
        assert_safe_exception(captured.value, marker=unsafe_marker)

    def test_effective_manager_database_must_match_intent_database(self) -> None:
        UploadProfileInterface.database = "replica"
        try:
            with pytest.raises(UploadDatabaseMismatchError):
                begin_file_upload(user=self.user, request=valid_request())
        finally:
            UploadProfileInterface.database = None

    def test_effective_manager_database_uses_django_write_router(self) -> None:
        with (
            patch(
                "general_manager.uploads.services.router.db_for_write",
                return_value="replica",
            ),
            pytest.raises(UploadDatabaseMismatchError),
        ):
            begin_file_upload(user=self.user, request=valid_request())

    @override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {
                "ENABLED": True,
                "MAX_PENDING_INTENTS_PER_USER": 5,
                "MAX_PENDING_BYTES_PER_USER": 100,
                "MAX_PENDING_INTENTS_GLOBAL": 1,
                "MAX_PENDING_BYTES_GLOBAL": 100,
            }
        }
    )
    def test_global_pending_intent_quota_applies_across_users(self) -> None:
        other = get_user_model().objects.create_user(username="upload-other")
        begin_file_upload(user=self.user, request=valid_request(size=1))

        with pytest.raises(UploadQuotaExceededError):
            begin_file_upload(user=other, request=valid_request(size=1))

    @override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {
                "ENABLED": True,
                "MAX_PENDING_INTENTS_PER_USER": 5,
                "MAX_PENDING_BYTES_PER_USER": 100,
                "MAX_PENDING_INTENTS_GLOBAL": 5,
                "MAX_PENDING_BYTES_GLOBAL": 10,
            }
        }
    )
    def test_global_pending_byte_quota_applies_across_users(self) -> None:
        other = get_user_model().objects.create_user(username="upload-byte-other")
        begin_file_upload(user=self.user, request=valid_request(size=6))

        with pytest.raises(UploadQuotaExceededError):
            begin_file_upload(user=other, request=valid_request(size=5))

    def test_proxy_instructions_receive_a_distinct_transfer_credential(self) -> None:
        result = begin_file_upload(user=self.user, request=valid_request())

        authorization = result.instructions.headers["Authorization"]
        assert authorization.startswith("GMUpload ")
        assert result.token not in authorization
        assert authorization not in repr(result)

    def test_adapter_identity_fingerprint_and_safe_instructions_are_persisted(
        self,
    ) -> None:
        registry = UploadAdapterRegistry()
        adapters: list[RecordingAdapter] = []

        def build(storage: object) -> RecordingAdapter:
            adapter = RecordingAdapter(storage)  # type: ignore[arg-type]
            adapters.append(adapter)
            return adapter

        registry.register(FileSystemStorage, build)

        from general_manager.uploads import services

        original_registry = services.upload_adapter_registry
        services.upload_adapter_registry = registry
        try:
            result = begin_file_upload(user=self.user, request=valid_request())
        finally:
            services.upload_adapter_registry = original_registry

        intent = UploadIntent.objects.get(pk=result.intent_id)
        assert intent.adapter_id == "tests.recording"
        assert intent.adapter_version == "9"
        assert intent.storage_fingerprint == "sha256:test-storage-fingerprint"
        assert result.instructions.url == "/safe-upload-url"
        assert result.instructions.headers["X-Upload-Authorization"] == (
            "temporary-transfer-secret"
        )
        assert set(result.instructions.headers) == {
            "Authorization",
            "X-Upload-Authorization",
        }
        assert intent.staging_key == adapters[0].instruction_kwargs["stage_key"]

        safe_representation = repr(result)
        assert result.token not in safe_representation
        assert intent.token_digest not in safe_representation
        assert intent.staging_key not in safe_representation
        assert "temporary-transfer-secret" not in safe_representation

        assert result.instructions.headers["Authorization"].startswith("GMUpload ")

    def test_adapter_failures_are_sanitized_and_do_not_create_an_intent(self) -> None:
        registry = UploadAdapterRegistry()
        registry.register(FileSystemStorage, FailingFingerprintAdapter)

        from general_manager.uploads import services

        original_registry = services.upload_adapter_registry
        services.upload_adapter_registry = registry
        try:
            with pytest.raises(UploadStorageError) as error:
                begin_file_upload(user=self.user, request=valid_request())
        finally:
            services.upload_adapter_registry = original_registry

        assert_safe_exception(
            error.value,
            marker="storage-credential-must-not-escape",
        )
        assert UploadIntent.objects.count() == 0

    def test_adapter_identity_and_instruction_failures_drop_untrusted_chains(
        self,
    ) -> None:
        from general_manager.uploads import services

        original_registry = services.upload_adapter_registry
        cases = (
            (
                RaisingInstructionAdapter,
                "adapter-credential-must-not-escape",
            ),
            (
                RaisingMutatedIdentityAdapter,
                "adapter-identity-secret-must-not-escape",
            ),
            (
                HostileUploadErrorAdapter,
                "hook-or-adapter-secret-must-not-escape",
            ),
        )
        for adapter_class, unsafe_marker in cases:
            with self.subTest(adapter=adapter_class.__name__):
                registry = UploadAdapterRegistry()
                registry.register(FileSystemStorage, adapter_class)
                services.upload_adapter_registry = registry
                try:
                    with pytest.raises(UploadStorageError) as captured:
                        begin_file_upload(user=self.user, request=valid_request())
                finally:
                    services.upload_adapter_registry = original_registry

                assert_safe_exception(captured.value, marker=unsafe_marker)
                assert UploadIntent.objects.count() == 0

    @override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {
                "ENABLED": True,
                "MAX_PENDING_INTENTS_PER_USER": 1,
                "MAX_PENDING_BYTES_PER_USER": 10,
                "MAX_PENDING_INTENTS_GLOBAL": 1,
                "MAX_PENDING_BYTES_GLOBAL": 10,
            }
        }
    )
    def test_buggy_adapter_outputs_are_sanitized_without_stranding_quota(self) -> None:
        from general_manager.uploads import services

        original_registry = services.upload_adapter_registry
        for adapter_class in (
            InvalidIdentityAdapter,
            NonInstructionAdapter,
            FieldExposingAdapter,
            InvalidHeaderAdapter,
            InvalidMethodAdapter,
            RaisingInstructionAdapter,
            MutatingIdentityAdapter,
            RaisingMutatedIdentityAdapter,
            EncodedStageUrlAdapter,
            ReplacedAuthorizationAdapter,
            DuplicateAuthorizationAdapter,
            InsecureDirectAdapter,
            UnsafeIdentityAdapter,
        ):
            with self.subTest(adapter=adapter_class.__name__):
                registry = UploadAdapterRegistry()
                registry.register(FileSystemStorage, adapter_class)
                services.upload_adapter_registry = registry
                try:
                    with pytest.raises(UploadStorageError) as captured:
                        begin_file_upload(user=self.user, request=valid_request())
                finally:
                    services.upload_adapter_registry = original_registry

                assert "credential-must-not-escape" not in str(captured.value)
                assert UploadIntent.objects.count() == 0

                begin_file_upload(user=self.user, request=valid_request())
                assert UploadIntent.objects.count() == 1
                UploadIntent.objects.all().delete()

    @override_settings(
        DEBUG=True,
        GENERAL_MANAGER={
            "FILE_UPLOADS": {
                "ENABLED": True,
                "ALLOW_INSECURE_HTTP": True,
            }
        },
    )
    def test_insecure_direct_url_requires_debug_and_explicit_opt_in(self) -> None:
        from general_manager.uploads import services

        registry = UploadAdapterRegistry()
        registry.register(FileSystemStorage, InsecureDirectAdapter)
        original_registry = services.upload_adapter_registry
        services.upload_adapter_registry = registry
        try:
            result = begin_file_upload(user=self.user, request=valid_request())
        finally:
            services.upload_adapter_registry = original_registry

        assert result.instructions.url.startswith("http://")

    def test_admissions_use_one_fixed_durable_global_quota_lock(self) -> None:
        from general_manager.uploads import models as upload_models

        assert hasattr(upload_models, "UploadQuotaLock")
        lock_model = upload_models.UploadQuotaLock
        begin_file_upload(user=self.user, request=valid_request(size=1))
        first_generation = lock_model.objects.get(pk=1).generation
        UploadIntent.objects.all().delete()

        lower_pk_user = get_user_model().objects.create_user(
            pk=-1,
            username="lower-pk-upload-user",
        )
        begin_file_upload(user=lower_pk_user, request=valid_request(size=1))

        assert lock_model.objects.count() == 1
        assert lock_model.objects.get(pk=1).generation == first_generation + 1

    def test_sqlite_busy_retry_exhaustion_is_sanitized(self) -> None:
        with (
            patch(
                "general_manager.uploads.services._acquire_global_quota_lock",
                side_effect=OperationalError(
                    "database is locked: storage-password-must-not-escape"
                ),
            ) as acquire,
            pytest.raises(UploadStorageError) as captured,
        ):
            begin_file_upload(user=self.user, request=valid_request(size=1))

        assert acquire.call_count > 1
        assert "storage-password-must-not-escape" not in str(captured.value)
        assert UploadIntent.objects.count() == 0

    def test_sqlite_busy_retry_starts_a_fresh_atomic_admission(self) -> None:
        from general_manager.uploads import services

        original_acquire = services._acquire_global_quota_lock
        atomic_states: list[bool] = []
        busy_error = OperationalError("database is locked")

        def fail_once(database_alias: str) -> None:
            atomic_states.append(connections[database_alias].in_atomic_block)
            if len(atomic_states) == 1:
                raise busy_error
            original_acquire(database_alias)

        with patch(
            "general_manager.uploads.services._acquire_global_quota_lock",
            side_effect=fail_once,
        ):
            begin_file_upload(user=self.user, request=valid_request(size=1))

        assert atomic_states == [True, True]
        assert UploadIntent.objects.count() == 1

    def test_sqlite_admission_fails_before_side_effects_in_application_atomic(
        self,
    ) -> None:
        hook_calls = 0

        def recording_hook(_user: object, _request: object) -> None:
            nonlocal hook_calls
            hook_calls += 1

        previous = set_begin_upload_rate_limit_hook(recording_hook)
        try:
            with (
                transaction.atomic(),
                pytest.raises(UploadStorageError) as captured,
            ):
                begin_file_upload(user=self.user, request=valid_request(size=1))
        finally:
            set_begin_upload_rate_limit_hook(previous)

        assert_safe_exception(captured.value, marker="database is locked")
        assert hook_calls == 0
        assert UploadIntent.objects.count() == 0

    @override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": False}})
    def test_disabled_sqlite_admission_still_fails_before_atomic_side_effects(
        self,
    ) -> None:
        hook_calls = 0

        def recording_hook(_user: object, _request: object) -> None:
            nonlocal hook_calls
            hook_calls += 1

        previous = set_begin_upload_rate_limit_hook(recording_hook)
        try:
            with transaction.atomic(), pytest.raises(UploadStorageError):
                begin_file_upload(user=self.user, request=valid_request(size=1))
        finally:
            set_begin_upload_rate_limit_hook(previous)

        assert hook_calls == 0
        assert UploadIntent.objects.count() == 0

    def test_non_busy_database_errors_are_not_retried(self) -> None:
        with (
            patch(
                "general_manager.uploads.services._acquire_global_quota_lock",
                side_effect=OperationalError("no such table: quota_lock"),
            ) as acquire,
            pytest.raises(OperationalError, match="no such table"),
        ):
            begin_file_upload(user=self.user, request=valid_request(size=1))

        assert acquire.call_count == 1
        assert UploadIntent.objects.count() == 0


@override_settings(
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "MAX_PENDING_INTENTS_PER_USER": 1,
            "MAX_PENDING_BYTES_PER_USER": 10,
        }
    }
)
class ConcurrentUploadQuotaTests(TransactionTestCase):
    """Exercise row-lock serialization on databases that implement it."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="upload-concurrent")
        GraphQL.manager_registry = {"UploadProfile": UploadProfile}

    def tearDown(self) -> None:
        GraphQL.reset_registry()

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_begins_cannot_both_cross_the_count_quota(self) -> None:
        barrier = Barrier(2)

        def begin() -> type[BaseException] | None:
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.user.pk)
                barrier.wait(timeout=5)
                begin_file_upload(user=actor, request=valid_request(size=1))
            except BaseException as error:  # noqa: BLE001 - records thread outcome
                return type(error)
            finally:
                close_old_connections()
            return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _index: begin(), range(2)))

        assert outcomes.count(None) == 1
        assert outcomes.count(UploadQuotaExceededError) == 1
        assert UploadIntent.objects.count() == 1

    @override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {
                "ENABLED": True,
                "MAX_PENDING_INTENTS_PER_USER": 2,
                "MAX_PENDING_BYTES_PER_USER": 10,
                "MAX_PENDING_INTENTS_GLOBAL": 1,
                "MAX_PENDING_BYTES_GLOBAL": 10,
            }
        }
    )
    def test_concurrent_users_cannot_both_cross_the_global_quota(self) -> None:
        second_user = get_user_model().objects.create_user(
            username="upload-concurrent-other"
        )
        user_ids = (self.user.pk, second_user.pk)
        barrier = Barrier(2)

        def begin(user_id: object) -> type[BaseException] | None:
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user_id)
                barrier.wait(timeout=5)
                begin_file_upload(user=actor, request=valid_request(size=1))
            except BaseException as error:  # noqa: BLE001 - records thread outcome
                return type(error)
            finally:
                close_old_connections()
            return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(begin, user_ids))

        assert outcomes.count(None) == 1
        assert outcomes.count(UploadQuotaExceededError) == 1
        assert UploadIntent.objects.count() == 1


class SQLiteUploadQuotaIntegrationTests(SimpleTestCase):
    """Exercise retry-safe admission against a real file-backed SQLite DB."""

    def test_file_backed_sqlite_serializes_concurrent_global_quota(self) -> None:
        script = textwrap.dedent(
            """
            from concurrent.futures import ThreadPoolExecutor
            import sys
            from threading import Barrier

            from tests import test_settings

            test_settings.DATABASES["default"]["NAME"] = sys.argv[1]

            import django

            django.setup()

            from django.contrib.auth import get_user_model
            from django.core.cache import cache
            from django.core.management import call_command
            from django.db import close_old_connections
            from django.test import override_settings
            from general_manager.api.graphql import GraphQL
            from tests.unit.test_upload_services import UploadProfile, valid_request

            call_command("migrate", verbosity=0, interactive=False)
            cache.clear()
            first = get_user_model().objects.create_user(username="sqlite-first")
            second = get_user_model().objects.create_user(username="sqlite-second")
            GraphQL.manager_registry = {"UploadProfile": UploadProfile}
            barrier = Barrier(2)

            def begin(user_id):
                close_old_connections()
                try:
                    actor = get_user_model().objects.get(pk=user_id)
                    barrier.wait(timeout=5)
                    begin_file_upload = __import__(
                        "general_manager.uploads.services",
                        fromlist=["begin_file_upload"],
                    ).begin_file_upload
                    begin_file_upload(
                        user=actor,
                        request=valid_request(size=1),
                    )
                except BaseException as error:
                    return type(error).__name__
                finally:
                    close_old_connections()
                return "ok"

            upload_settings = {
                "FILE_UPLOADS": {
                    "ENABLED": True,
                    "MAX_PENDING_INTENTS_PER_USER": 2,
                    "MAX_PENDING_BYTES_PER_USER": 10,
                    "MAX_PENDING_INTENTS_GLOBAL": 1,
                    "MAX_PENDING_BYTES_GLOBAL": 10,
                }
            }
            with override_settings(GENERAL_MANAGER=upload_settings):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(begin, (first.pk, second.pk)))

            assert sorted(outcomes) == ["UploadQuotaExceededError", "ok"], outcomes
            """
        )
        self._run_sqlite_script(script)

    def test_file_backed_sqlite_atomic_call_fails_before_side_effects(self) -> None:
        script = textwrap.dedent(
            """
            from concurrent.futures import ThreadPoolExecutor
            import sys
            from threading import Barrier

            from tests import test_settings

            test_settings.DATABASES["default"]["NAME"] = sys.argv[1]

            import django

            django.setup()

            from django.contrib.auth import get_user_model
            from django.core.cache import cache
            from django.core.management import call_command
            from django.db import close_old_connections, transaction
            from django.test import override_settings
            from general_manager.api.graphql import GraphQL
            from general_manager.uploads.models import UploadIntent
            from tests.unit.test_upload_services import UploadProfile, valid_request

            call_command("migrate", verbosity=0, interactive=False)
            cache.clear()
            first = get_user_model().objects.create_user(username="sqlite-first")
            second = get_user_model().objects.create_user(username="sqlite-second")
            GraphQL.manager_registry = {"UploadProfile": UploadProfile}
            barrier = Barrier(2)

            def begin(user_id):
                close_old_connections()
                try:
                    actor = get_user_model().objects.get(pk=user_id)
                    barrier.wait(timeout=5)
                    begin_file_upload = __import__(
                        "general_manager.uploads.services",
                        fromlist=["begin_file_upload"],
                    ).begin_file_upload
                    with transaction.atomic():
                        begin_file_upload(
                            user=actor,
                            request=valid_request(size=1),
                        )
                except BaseException as error:
                    return type(error).__name__
                finally:
                    close_old_connections()
                return "ok"

            upload_settings = {
                "FILE_UPLOADS": {
                    "ENABLED": True,
                    "MAX_PENDING_INTENTS_PER_USER": 2,
                    "MAX_PENDING_BYTES_PER_USER": 10,
                    "MAX_PENDING_INTENTS_GLOBAL": 1,
                    "MAX_PENDING_BYTES_GLOBAL": 10,
                    "MAX_BEGIN_ATTEMPTS_PER_USER": 10,
                    "MAX_BEGIN_ATTEMPTS_GLOBAL": 1,
                }
            }
            with override_settings(GENERAL_MANAGER=upload_settings):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    outcomes = list(executor.map(begin, (first.pk, second.pk)))

                assert sorted(outcomes) == [
                    "UploadStorageError",
                    "UploadStorageError",
                ], outcomes
                assert UploadIntent.objects.count() == 0

                begin_file_upload = __import__(
                    "general_manager.uploads.services",
                    fromlist=["begin_file_upload"],
                ).begin_file_upload
                begin_file_upload(
                    user=first,
                    request=valid_request(size=1),
                )

            assert UploadIntent.objects.count() == 1
            """
        )

        self._run_sqlite_script(script)

    def _run_sqlite_script(self, script: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, "upload-quota.sqlite3")
            result = subprocess.run(  # noqa: S603
                [sys.executable, "-c", script, database_path],
                cwd=os.getcwd(),
                env={
                    **os.environ,
                    "DJANGO_SETTINGS_MODULE": "tests.test_settings",
                    "PYTHONPATH": os.pathsep.join(
                        (os.path.join(os.getcwd(), "src"), os.getcwd())
                    ),
                },
                capture_output=True,
                text=True,
                check=False,
            )

        if result.returncode != 0:
            self.fail(result.stderr or result.stdout or "SQLite quota check failed")
