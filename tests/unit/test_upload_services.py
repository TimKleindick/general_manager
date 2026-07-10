"""Tests for secure creation of field-bound upload intents."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import tempfile
from threading import Barrier
from typing import ClassVar
from unittest.mock import patch
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.storage import FileSystemStorage
from django.db import close_old_connections, models
from django.test import (
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
        return UploadInstructions(
            transport=UploadTransport.PROXY,
            method="PUT",
            url="/safe-upload-url",
            headers={"X-Upload-Authorization": "temporary-transfer-secret"},
        )

    def storage_fingerprint(self) -> str:
        return "sha256:test-storage-fingerprint"


class FailingFingerprintAdapter(RecordingAdapter):
    def storage_fingerprint(self) -> str:
        raise RuntimeError("storage-credential-must-not-escape")


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

    assert "42" not in credential
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
        GraphQL.manager_registry = {"UploadProfile": UploadProfile}

    def tearDown(self) -> None:
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
        assert result.instructions.headers == {
            "X-Upload-Authorization": "temporary-transfer-secret"
        }
        assert intent.staging_key == adapters[0].instruction_kwargs["stage_key"]

        safe_representation = repr(result)
        assert result.token not in safe_representation
        assert intent.token_digest not in safe_representation
        assert intent.staging_key not in safe_representation
        assert "temporary-transfer-secret" not in safe_representation

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

        assert "storage-credential-must-not-escape" not in str(error.value)
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
