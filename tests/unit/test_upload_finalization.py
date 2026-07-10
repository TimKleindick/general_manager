"""Tests for atomic ORM upload claims and post-commit finalization."""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import time
from threading import Barrier, Event
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import close_old_connections, connection, models, transaction
from django.db.models import NOT_PROVIDED
from django.test import override_settings
from django.utils import timezone

from general_manager.api.graphql import GraphQL
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.interface.bundles.database import ORM_WRITABLE_CAPABILITIES
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.uploads import services
from general_manager.uploads import finalization
from general_manager.uploads.adapters import (
    ClaimedObject,
    ProxyUploadAdapter,
    UploadAdapterRegistry,
)
from general_manager.uploads.config import FileInspection, FileUploadPolicy
from general_manager.uploads.errors import (
    InvalidImageError,
    InvalidUploadSizeError,
    InvalidFileTypeError,
    UploadAlreadyConsumedError,
    UploadBackendUnsupportedError,
    UploadBindingMismatchError,
    UploadStorageChangedError,
    UploadStorageError,
    UploadSupersededError,
)
from general_manager.uploads.graphql_types import create_stored_file_value
from general_manager.uploads.models import UploadIntent
from general_manager.uploads.tokens import issue_upload_token
from general_manager.uploads.types import (
    ObjectVersion,
    UploadCandidate,
    UploadIntentState,
    UploadOperation,
)


class FinalizationStorage(FileSystemStorage):
    """Distinct local storage class for deterministic adapter selection."""


_STORAGE_ROOT = tempfile.mkdtemp(prefix="gm-finalization-tests-")
_STORAGE = FinalizationStorage(location=_STORAGE_ROOT)


def _detect_octet_stream(_inspection: FileInspection) -> str:
    return "application/octet-stream"


def _upload_to(instance: models.Model, filename: str) -> str:
    return f"{(instance.label or 'blank')[:16]}/{filename}"


class FinalizationRecord(models.Model):
    avatar = models.FileField(storage=_STORAGE, upload_to=_upload_to, blank=True)
    document = models.FileField(storage=_STORAGE, upload_to="documents/", blank=True)
    image = models.ImageField(storage=_STORAGE, upload_to="images/", blank=True)
    label = models.CharField(max_length=64, blank=True)

    class Meta:
        app_label = "general_manager"
        db_table = "gm_test_upload_finalization_record"


class FinalizationInterface(OrmInterfaceBase[FinalizationRecord]):
    _model = FinalizationRecord
    database: ClassVar[str | None] = None
    input_fields: ClassVar[dict[str, Input[type[object]]]] = {
        "id": Input(int)  # type: ignore[dict-item]
    }
    configured_capabilities: ClassVar[tuple] = (ORM_WRITABLE_CAPABILITIES,)

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        common = {
            "type": str,
            "is_required": False,
            "is_editable": True,
            "is_derived": False,
            "default": NOT_PROVIDED,
        }
        return {
            "avatar": {**common, "orm_field_kind": "file"},
            "document": {**common, "orm_field_kind": "file"},
            "image": {**common, "orm_field_kind": "image"},
            "label": common,
        }


class FinalizationManager(GeneralManager):
    _attributes: ClassVar[dict[str, object]] = {}


FinalizationManager.Interface = FinalizationInterface  # type: ignore[assignment]
FinalizationInterface._parent_class = FinalizationManager
FinalizationRecord._general_manager_class = FinalizationManager  # type: ignore[attr-defined]


class FinalizationAdapter(ProxyUploadAdapter):
    adapter_id = "tests.finalization-proxy"
    adapter_version = 1
    fail_materialize: ClassVar[bool] = False
    materialize_calls: ClassVar[int] = 0
    materialized_versions: ClassVar[dict[str, ObjectVersion]] = {}
    deleted_materialized: ClassVar[list[str]] = []
    malformed_final_version: ClassVar[bool] = False
    fail_delete_stage: ClassVar[bool] = False
    fail_delete_materialized: ClassVar[bool] = False
    malformed_old_version: ClassVar[bool] = False

    def storage_fingerprint(self) -> str:
        return "sha256:finalization-storage"

    def inspect_staged(self, stage_key: str) -> ObjectVersion:
        version = super().inspect_staged(stage_key)
        if type(self).malformed_old_version and stage_key.startswith("existing/"):
            return ObjectVersion(
                version_id="unsafe\x00version",
                etag=version.etag,
                checksum_sha256="not-a-sha256",
                size=version.size,
                content_type=version.content_type,
            )
        return version

    def inspect_replaced_object(self, key: str) -> ObjectVersion:
        version = super().inspect_replaced_object(key)
        if type(self).malformed_old_version:
            return ObjectVersion(
                version_id="unsafe\x00version",
                etag=version.etag,
                checksum_sha256="not-a-sha256",
                size=version.size,
                content_type=version.content_type,
            )
        return version

    def materialize(
        self,
        stage_key: str,
        version: ObjectVersion,
        final_key: str,
        *,
        intent_id: object,
    ) -> str:
        type(self).materialize_calls += 1
        if type(self).fail_materialize:
            raise OSError
        actual_key = super().materialize(
            stage_key,
            version,
            final_key,
            intent_id=intent_id,  # type: ignore[arg-type]
        )
        type(self).materialized_versions[actual_key] = ObjectVersion(
            version_id=f"final-{intent_id}",
            etag=f"etag-{intent_id}",
            checksum_sha256=version.checksum_sha256,
            size=version.size,
            content_type=version.content_type,
        )
        return actual_key

    def inspect_materialized(
        self,
        final_key: str,
        source_version: ObjectVersion,
        *,
        intent_id: object,
    ) -> ObjectVersion:
        del source_version, intent_id
        version = type(self).materialized_versions[final_key]
        if type(self).malformed_final_version:
            return ObjectVersion(
                version_id="invalid\x00version",
                etag=version.etag,
                checksum_sha256=version.checksum_sha256,
                size=version.size,
                content_type=version.content_type,
            )
        return version

    def delete_materialized(
        self,
        final_key: str,
        final_version: ObjectVersion,
        *,
        intent_id: object,
    ) -> None:
        del intent_id
        if type(self).fail_delete_materialized:
            raise OSError
        if type(self).materialized_versions.get(final_key) != final_version:
            raise UploadStorageChangedError
        self.storage.delete(final_key)
        type(self).deleted_materialized.append(final_key)

    def delete_object(self, key: str, version: ObjectVersion) -> None:
        if self.inspect_staged(key) != version:
            raise UploadStorageChangedError
        self.storage.delete(key)

    def delete_stage(
        self,
        stage_key: str,
        version: ObjectVersion | None = None,
    ) -> None:
        del version
        if type(self).fail_delete_stage:
            raise OSError
        self.storage.delete(stage_key)


class ExactCleanupFinalizationAdapter(FinalizationAdapter):
    """Test adapter modeling an immutable-version conditional-delete backend."""

    def plan_replaced_object_claim(
        self,
        key: str,
        version: ObjectVersion,
        *,
        cleanup_id: UUID,
    ) -> ClaimedObject:
        del cleanup_id
        return ClaimedObject(key=key, version=version)

    def claim_replaced_object(
        self,
        key: str,
        claimed: ClaimedObject,
        *,
        cleanup_id: UUID,
    ) -> None:
        del cleanup_id
        if key != claimed.key or self.inspect_replaced_object(key) != claimed.version:
            raise UploadStorageChangedError

    def delete_claimed_object(
        self,
        claimed: ClaimedObject,
        *,
        cleanup_id: UUID,
    ) -> None:
        del cleanup_id
        if self.inspect_replaced_object(claimed.key) != claimed.version:
            raise UploadStorageChangedError
        self.storage.delete(claimed.key)


@pytest.fixture(scope="module", autouse=True)
def finalization_table(django_db_setup: object, django_db_blocker: object):
    del django_db_setup
    with django_db_blocker.unblock():  # type: ignore[attr-defined]
        with connection.schema_editor() as editor:
            editor.create_model(FinalizationRecord)
    yield
    with django_db_blocker.unblock():  # type: ignore[attr-defined]
        with connection.schema_editor() as editor:
            editor.delete_model(FinalizationRecord)


@pytest.fixture(autouse=True)
def finalization_runtime(monkeypatch: pytest.MonkeyPatch):
    registry = UploadAdapterRegistry()
    registry.register(FinalizationStorage, FinalizationAdapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)
    GraphQL.manager_registry[FinalizationManager.__name__] = FinalizationManager
    FinalizationAdapter.fail_materialize = False
    FinalizationAdapter.materialize_calls = 0
    FinalizationAdapter.materialized_versions = {}
    FinalizationAdapter.deleted_materialized = []
    FinalizationAdapter.malformed_final_version = False
    FinalizationAdapter.fail_delete_stage = False
    FinalizationAdapter.fail_delete_materialized = False
    FinalizationAdapter.malformed_old_version = False
    yield
    GraphQL.manager_registry.pop(FinalizationManager.__name__, None)


def _uploaded_intent(
    user: models.Model,
    *,
    field_name: str = "avatar",
    operation: UploadOperation = UploadOperation.CREATE,
    target_id: str | None = None,
    content: bytes = b"uploaded-content",
    content_type: str = "application/octet-stream",
) -> tuple[UploadIntent, UploadCandidate]:
    _token, digest = issue_upload_token()
    stage_key = f"gm-staging/tests/{digest[:16]}"
    adapter = FinalizationAdapter(_STORAGE)
    version = adapter.save_stage(
        stage_key,
        [content],
        content_type=content_type,
        checksum_sha256=None,
        size=len(content),
    )
    intent = UploadIntent.objects.create(
        user=user,
        token_digest=digest,
        manager_name=FinalizationManager.__name__,
        field_name=field_name,
        operation=operation.value,
        target_id=target_id,
        adapter_id=adapter.adapter_id,
        adapter_version=str(adapter.adapter_version),
        storage_fingerprint=adapter.storage_fingerprint(),
        staging_key=stage_key,
        original_filename="portrait.bin",
        declared_size=version.size,
        declared_content_type=version.content_type or content_type,
        declared_checksum_sha256=version.checksum_sha256,
        verified_size=version.size,
        verified_content_type=version.content_type,
        verified_checksum_sha256=version.checksum_sha256,
        object_version=asdict(version),
        state=UploadIntentState.UPLOADED.value,
        expires_at=timezone.now() + timedelta(minutes=15),
        uploaded_at=timezone.now(),
    )
    return intent, UploadCandidate(
        intent_id=intent.id,
        filename=intent.original_filename,
        size=version.size,
        content_type=version.content_type or content_type,
        checksum_sha256=version.checksum_sha256,
    )


def _use_exact_cleanup_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = UploadAdapterRegistry()
    registry.register(FinalizationStorage, ExactCleanupFinalizationAdapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_create_commits_reserved_key_then_post_commit_consumes(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-create")
    intent, candidate = _uploaded_intent(user)

    result = FinalizationInterface.create(
        creator_id=user.pk,
        avatar=candidate,
        label="created",
    )

    record = FinalizationRecord.objects.get(pk=result["id"])
    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert record.avatar.name == intent.final_key
    assert record.avatar.name.startswith("created/")
    assert intent.id.hex in record.avatar.name
    assert not _STORAGE.exists(intent.staging_key)
    assert _STORAGE.exists(record.avatar.name)


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "DOWNLOAD_URL_TTL_SECONDS": 60,
            "TERMINAL_RETENTION_SECONDS": 86400,
        }
    }
)
def test_retention_preserves_consumed_download_metadata_while_binding_is_live(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="retained-download-owner")
    intent, candidate = _uploaded_intent(user)
    result = FinalizationInterface.create(creator_id=user.pk, avatar=candidate)
    record = FinalizationRecord.objects.get(pk=result["id"])
    intent.refresh_from_db()
    old = timezone.now() - timedelta(days=2)
    UploadIntent.objects.filter(pk=intent.pk).update(
        cleanup_completed_at=old,
        updated_at=old,
    )
    issued_at = old + timedelta(seconds=86399)
    manager = FinalizationManager._from_trusted_orm_instance(record)
    issued = create_stored_file_value(
        manager,
        SimpleNamespace(context=SimpleNamespace()),
        field_name="avatar",
        manager_name=FinalizationManager.__name__,
        now=lambda: issued_at,
    )
    assert issued is not None
    assert issued.download_url is not None
    assert issued.download_url_expires_at == issued_at + timedelta(seconds=60)

    future = issued_at + timedelta(seconds=120)
    counts = finalization.run_upload_cleanup(
        batch_size=10, older_than_seconds=1, at=future
    )

    assert counts.deleted == 0
    assert counts.skipped == 1
    assert UploadIntent.objects.filter(pk=intent.pk).exists()
    assert record.avatar.name == intent.final_key
    resolved_later = create_stored_file_value(
        manager,
        SimpleNamespace(context=SimpleNamespace()),
        field_name="avatar",
        manager_name=FinalizationManager.__name__,
        now=lambda: future,
    )
    assert resolved_later is not None
    assert resolved_later.download_url is not None
    assert resolved_later.checksum == intent.verified_checksum_sha256
    assert resolved_later.original_name == intent.original_filename


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_retention_deletes_consumed_metadata_after_binding_changes(
    django_user_model: type[models.Model],
    caplog,
) -> None:
    caplog.set_level("INFO", logger="general_manager.uploads")
    user = django_user_model.objects.create_user(username="released-download-owner")
    intent, candidate = _uploaded_intent(user)
    result = FinalizationInterface.create(creator_id=user.pk, avatar=candidate)
    record = FinalizationRecord.objects.get(pk=result["id"])
    record.avatar = "created/replacement.bin"
    record.save(update_fields=("avatar",))
    intent.refresh_from_db()
    old = timezone.now() - timedelta(days=2)
    UploadIntent.objects.filter(pk=intent.pk).update(
        cleanup_completed_at=old,
        updated_at=old,
    )

    counts = finalization.run_upload_cleanup(batch_size=10, older_than_seconds=1)

    assert counts.deleted == 1
    assert not UploadIntent.objects.filter(pk=intent.pk).exists()
    deletion = next(
        record.upload
        for record in caplog.records
        if getattr(record, "upload", {}).get("event") == "upload_operation"
        and record.upload.get("result") == "completed"
    )
    assert deletion["intent_id"] == str(intent.id)
    assert deletion["adapter"] == intent.adapter_id
    assert deletion["manager"] == intent.manager_name
    assert deletion["field"] == intent.field_name
    assert deletion["state"] == UploadIntentState.CONSUMED.value
    assert deletion["declared_size"] == intent.declared_size
    assert FinalizationAdapter.materialize_calls == 1
    assert intent.target_id is None
    assert intent.final_target_pk == str(record.pk)


def test_finalization_target_lookup_supports_uuid_primary_keys() -> None:
    class UUIDTarget(models.Model):
        id = models.UUIDField(primary_key=True)

        class Meta:
            app_label = "general_manager"

    target_id = uuid4()

    assert finalization._parse_target_pk(str(target_id), UUIDTarget) == target_id


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_replacement_failure_retains_stage_old_file_and_finalizing_state(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-failure")
    _STORAGE.save("existing/old.bin", ContentFile(b"old-content"))
    record = FinalizationRecord.objects.create(
        avatar="existing/old.bin",
        label="before",
    )
    target_id = serialize_dependency_identifier({"id": record.pk})
    intent, candidate = _uploaded_intent(
        user,
        operation=UploadOperation.UPDATE,
        target_id=target_id,
    )
    FinalizationAdapter.fail_materialize = True

    FinalizationInterface(record.pk).update(
        creator_id=user.pk,
        avatar=candidate,
        label="after",
    )

    record.refresh_from_db()
    intent.refresh_from_db()
    assert record.avatar.name == intent.final_key
    assert record.label == "after"
    assert record.avatar.name.startswith("after/")
    assert intent.state == UploadIntentState.FINALIZING.value
    assert intent.old_key == "existing/old.bin"
    assert intent.finalization_attempt_count == 1
    assert intent.finalization_error_code == "UPLOAD_STORAGE_ERROR"
    assert intent.cleanup_lease_expires_at is not None
    assert intent.cleanup_lease_expires_at > timezone.now()
    assert intent.cleanup_lease_token == ""
    assert _STORAGE.exists(intent.staging_key)
    assert _STORAGE.exists("existing/old.bin")


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_replaced_staged_bytes_fail_before_domain_write(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-replaced-stage")
    intent, candidate = _uploaded_intent(user)
    Path(_STORAGE.path(intent.staging_key)).write_bytes(b"hostile-replacement")

    with pytest.raises(UploadStorageChangedError):
        FinalizationInterface.create(
            creator_id=user.pk,
            avatar=candidate,
            label="must-not-persist",
        )

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value
    assert not FinalizationRecord.objects.filter(label="must-not-persist").exists()
    assert FinalizationAdapter.materialize_calls == 0


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_adapter_without_exact_finalization_contract_fails_before_domain_write(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-legacy-adapter")
    intent, candidate = _uploaded_intent(user)
    delegate = FinalizationAdapter(_STORAGE)

    class LegacyAdapter:
        adapter_id = delegate.adapter_id
        adapter_version = delegate.adapter_version

        def inspect_staged(self, key: str) -> ObjectVersion:
            return delegate.inspect_staged(key)

        def materialize(self, *args: object, **kwargs: object) -> str:
            return delegate.materialize(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        services,
        "_resolve_intent_adapter",
        lambda *_args, **_kwargs: LegacyAdapter(),
    )

    with pytest.raises(UploadBackendUnsupportedError):
        FinalizationInterface.create(
            creator_id=user.pk,
            avatar=candidate,
            label="must-not-persist",
        )

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value
    assert not FinalizationRecord.objects.filter(label="must-not-persist").exists()


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_image_field_decodes_staged_content_before_domain_write(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-invalid-image")
    intent, candidate = _uploaded_intent(
        user,
        field_name="image",
        content=b"not-an-image",
    )

    with pytest.raises(InvalidImageError):
        FinalizationInterface.create(
            creator_id=user.pk,
            image=candidate,
            label="must-not-persist",
        )

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.REJECTED.value
    assert intent.finalization_error_code == "INVALID_IMAGE"
    assert not FinalizationRecord.objects.filter(label="must-not-persist").exists()


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_image_field_rejects_decoded_media_type_mismatch(
    django_user_model: type[models.Model],
) -> None:
    from PIL import Image

    encoded = BytesIO()
    Image.new("RGB", (1, 1)).save(encoded, format="PNG")
    user = django_user_model.objects.create_user(username="finalize-image-mime")
    intent, candidate = _uploaded_intent(
        user,
        field_name="image",
        content=encoded.getvalue(),
        content_type="application/octet-stream",
    )

    with pytest.raises(InvalidFileTypeError):
        FinalizationInterface.create(creator_id=user.pk, image=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.REJECTED.value
    assert intent.finalization_error_code == "INVALID_FILE_TYPE"


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
@pytest.mark.parametrize(
    ("policy", "error_type", "error_code"),
    (
        (
            FileUploadPolicy(max_bytes=2),
            InvalidUploadSizeError,
            "INVALID_UPLOAD_SIZE",
        ),
        (
            FileUploadPolicy(
                allowed_content_types=("text/plain",),
                content_inspector=_detect_octet_stream,
            ),
            InvalidFileTypeError,
            "INVALID_FILE_TYPE",
        ),
        (
            FileUploadPolicy(allowed_extensions=(".png",)),
            InvalidFileTypeError,
            "INVALID_FILE_TYPE",
        ),
    ),
)
def test_consume_rechecks_tightened_field_policy_before_domain_write(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
    policy: FileUploadPolicy,
    error_type: type[Exception],
    error_code: str,
) -> None:
    user = django_user_model.objects.create_user(username=f"policy-{error_code}")
    intent, candidate = _uploaded_intent(user, content=b"data")
    monkeypatch.setattr(
        FinalizationManager,
        "FileUploads",
        SimpleNamespace(fields={"avatar": policy}),
        raising=False,
    )

    with pytest.raises(error_type):
        FinalizationInterface.create(
            creator_id=user.pk,
            avatar=candidate,
            label="must-not-persist",
        )

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.REJECTED.value
    assert intent.finalization_error_code == error_code
    assert not FinalizationRecord.objects.filter(label="must-not-persist").exists()


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_policy_resolution_failure_is_sanitized_and_keeps_intent_uploaded(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="policy-resolution-failure")
    intent, candidate = _uploaded_intent(user)

    def fail_policy(*args: object, **kwargs: object) -> FileUploadPolicy:
        del args, kwargs
        raise RuntimeError

    monkeypatch.setattr(services, "_resolve_policy", fail_policy)

    with pytest.raises(UploadStorageError):
        FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "MAX_INSPECTION_BYTES": 3}}
)
def test_file_content_inspector_receives_only_bounded_redacted_context(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[FileInspection] = []

    def inspect_content(value: FileInspection) -> str:
        received.append(value)
        return "application/octet-stream"

    monkeypatch.setattr(
        FinalizationManager,
        "FileUploads",
        SimpleNamespace(
            fields={"avatar": FileUploadPolicy(content_inspector=inspect_content)}
        ),
        raising=False,
    )
    user = django_user_model.objects.create_user(username="bounded-inspector")
    _intent, candidate = _uploaded_intent(user, content=b"data")

    FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    assert len(received) == 1
    assert received[0].content == b"dat"
    assert received[0].truncated is True
    assert received[0].size == 4
    assert "dat" not in repr(received[0])


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "MAX_INSPECTION_BYTES": 3}}
)
def test_file_content_inspector_accumulates_short_nonempty_reads(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[FileInspection] = []

    class OneByteRead(BytesIO):
        def read(self, size: int = -1) -> bytes:
            return super().read(1 if size != 0 else 0)

    def inspect_content(value: FileInspection) -> str:
        received.append(value)
        return "application/octet-stream"

    def open_short_read(
        _adapter: FinalizationAdapter,
        _stage_key: str,
        _version: ObjectVersion,
    ) -> OneByteRead:
        return OneByteRead(b"data")

    monkeypatch.setattr(
        FinalizationManager,
        "FileUploads",
        SimpleNamespace(
            fields={"avatar": FileUploadPolicy(content_inspector=inspect_content)}
        ),
        raising=False,
    )
    monkeypatch.setattr(FinalizationAdapter, "open_stage", open_short_read)
    user = django_user_model.objects.create_user(username="short-read-inspector")
    _intent, candidate = _uploaded_intent(user, content=b"data")

    FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    assert received[0].content == b"dat"
    assert received[0].truncated is True


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_hostile_content_inspector_is_sanitized_without_rejecting_intent(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_inspector(_value: FileInspection) -> str:
        raise RuntimeError

    monkeypatch.setattr(
        FinalizationManager,
        "FileUploads",
        SimpleNamespace(
            fields={"avatar": FileUploadPolicy(content_inspector=fail_inspector)}
        ),
        raising=False,
    )
    user = django_user_model.objects.create_user(username="hostile-inspector")
    intent, candidate = _uploaded_intent(user)

    with pytest.raises(UploadStorageError):
        FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_decompression_bomb_error_is_stably_rejected(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    encoded = BytesIO()
    Image.new("RGB", (1, 1)).save(encoded, format="PNG")
    user = django_user_model.objects.create_user(username="image-bomb")
    intent, candidate = _uploaded_intent(
        user,
        field_name="image",
        content=encoded.getvalue(),
        content_type="image/png",
    )

    def raise_decompression_bomb(*_args: object, **_kwargs: object) -> None:
        raise Image.DecompressionBombError

    monkeypatch.setattr(Image, "open", raise_decompression_bomb)

    with pytest.raises(InvalidImageError):
        FinalizationInterface.create(creator_id=user.pk, image=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.REJECTED.value
    assert intent.finalization_error_code == "INVALID_IMAGE"


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_valid_image_dimensions_are_persisted_before_finalization(
    django_user_model: type[models.Model],
) -> None:
    from PIL import Image

    encoded = BytesIO()
    Image.new("RGB", (3, 2)).save(encoded, format="PNG")
    user = django_user_model.objects.create_user(username="finalize-image-size")
    intent, candidate = _uploaded_intent(
        user,
        field_name="image",
        content=encoded.getvalue(),
        content_type="image/png",
    )

    FinalizationInterface.create(creator_id=user.pk, image=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert intent.verified_width == 3
    assert intent.verified_height == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "limits",
    [
        {"MAX_IMAGE_WIDTH": 2, "MAX_IMAGE_HEIGHT": 10},
        {"MAX_IMAGE_WIDTH": 10, "MAX_IMAGE_HEIGHT": 1},
    ],
)
def test_image_dimensions_exceeding_either_finite_limit_are_rejected(
    django_user_model: type[models.Model],
    limits: dict[str, int],
) -> None:
    from PIL import Image

    encoded = BytesIO()
    Image.new("RGB", (3, 2)).save(encoded, format="PNG")
    user = django_user_model.objects.create_user(
        username=f"image-limits-{limits['MAX_IMAGE_WIDTH']}"
    )
    intent, candidate = _uploaded_intent(
        user,
        field_name="image",
        content=encoded.getvalue(),
        content_type="image/png",
    )

    with (
        override_settings(
            GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, **limits}}
        ),
        pytest.raises(InvalidImageError),
    ):
        FinalizationInterface.create(creator_id=user.pk, image=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.REJECTED.value
    assert intent.finalization_error_code == "INVALID_IMAGE"


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_one_intent_cannot_be_claimed_for_multiple_fields(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-duplicate")
    intent, candidate = _uploaded_intent(user)

    with pytest.raises(UploadBindingMismatchError):
        FinalizationInterface.create(
            creator_id=user.pk,
            avatar=candidate,
            document=candidate,
            label="must-not-persist",
        )

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value
    assert not FinalizationRecord.objects.filter(label="must-not-persist").exists()


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_domain_validation_rollback_leaves_intent_uploaded_and_no_final_object(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-rollback")
    intent, candidate = _uploaded_intent(user)

    with pytest.raises(ValidationError):
        FinalizationInterface.create(
            creator_id=user.pk,
            avatar=candidate,
            label="x" * 65,
        )

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value
    assert intent.final_key is None
    assert _STORAGE.exists(intent.staging_key)
    assert FinalizationAdapter.materialize_calls == 0


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_failed_callback_can_be_reconciled_idempotently(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-retry")
    intent, candidate = _uploaded_intent(user)
    FinalizationAdapter.fail_materialize = True
    result = FinalizationInterface.create(creator_id=user.pk, avatar=candidate)
    intent.refresh_from_db()
    assert intent.state == UploadIntentState.FINALIZING.value

    FinalizationAdapter.fail_materialize = False
    UploadIntent.objects.filter(pk=intent.pk).update(
        cleanup_lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    finalization.finalize_upload_intent(intent.id)
    finalization.finalize_upload_intent(intent.id)

    intent.refresh_from_db()
    record = FinalizationRecord.objects.get(pk=result["id"])
    assert intent.state == UploadIntentState.CONSUMED.value
    assert intent.finalization_attempt_count == 2
    assert FinalizationAdapter.materialize_calls == 2
    assert _STORAGE.exists(record.avatar.name)
    assert not _STORAGE.exists(intent.staging_key)


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_adapter_resolution_failure_records_a_finalization_attempt(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-adapter-change")
    intent, candidate = _uploaded_intent(user)
    callbacks: list[object] = []

    def capture_callback(callback: object, **kwargs: object) -> None:
        del kwargs
        callbacks.append(callback)

    monkeypatch.setattr(finalization.transaction, "on_commit", capture_callback)
    FinalizationInterface.create(creator_id=user.pk, avatar=candidate)
    UploadIntent.objects.filter(pk=intent.pk).update(storage_fingerprint="changed")
    callback = callbacks[0]
    assert callable(callback)
    callback()

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.FINALIZING.value
    assert intent.finalization_attempt_count == 1
    assert intent.finalization_error_code == "UPLOAD_STORAGE_CHANGED"


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_changed_target_before_callback_is_superseded_without_deleting_newer_file(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-superseded")
    intent, candidate = _uploaded_intent(user)
    callbacks: list[object] = []

    def capture_callback(callback: object, **kwargs: object) -> None:
        del kwargs
        callbacks.append(callback)

    monkeypatch.setattr(finalization.transaction, "on_commit", capture_callback)
    result = FinalizationInterface.create(creator_id=user.pk, avatar=candidate)
    intent.refresh_from_db()
    assert intent.state == UploadIntentState.FINALIZING.value
    assert len(callbacks) == 1

    _STORAGE.save("newer/current.bin", ContentFile(b"newer"))
    FinalizationRecord.objects.filter(pk=result["id"]).update(
        avatar="newer/current.bin"
    )
    callback = callbacks[0]
    assert callable(callback)
    callback()

    intent.refresh_from_db()
    record = FinalizationRecord.objects.get(pk=result["id"])
    assert intent.state == UploadIntentState.SUPERSEDED.value
    assert record.avatar.name == "newer/current.bin"
    assert _STORAGE.exists("newer/current.bin")
    assert not _STORAGE.exists(intent.staging_key)
    assert FinalizationAdapter.materialize_calls == 0


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_superseded_retry_deletes_exact_materialized_version_after_state_loss(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-owned-cleanup")
    intent, candidate = _uploaded_intent(user)

    def fail_state_update(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        raise OSError

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            finalization,
            "_complete_finalization",
            fail_state_update,
        )
        result = FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    reserved_key = intent.final_key
    assert reserved_key and _STORAGE.exists(reserved_key)
    _STORAGE.save("newer/superseding.bin", ContentFile(b"newer"))
    FinalizationRecord.objects.filter(pk=result["id"]).update(
        avatar="newer/superseding.bin"
    )
    UploadIntent.objects.filter(pk=intent.pk).update(
        cleanup_lease_expires_at=timezone.now() - timedelta(seconds=1)
    )

    finalization.finalize_upload_intent(intent.id)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.SUPERSEDED.value
    assert not _STORAGE.exists(reserved_key)
    assert FinalizationAdapter.deleted_materialized == [reserved_key]
    assert _STORAGE.exists("newer/superseding.bin")


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_replaced_old_object_is_retained_when_exact_version_changes_before_cleanup(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-old-race")
    old_key = "existing/raced-old.bin"
    _STORAGE.save(old_key, ContentFile(b"old-version"))
    record = FinalizationRecord.objects.create(avatar=old_key)
    intent, candidate = _uploaded_intent(
        user,
        operation=UploadOperation.UPDATE,
        target_id=serialize_dependency_identifier({"id": record.pk}),
    )
    callbacks: list[object] = []

    def capture_callback(callback: object, **kwargs: object) -> None:
        del kwargs
        callbacks.append(callback)

    monkeypatch.setattr(finalization.transaction, "on_commit", capture_callback)
    FinalizationInterface(record.pk).update(creator_id=user.pk, avatar=candidate)
    Path(_STORAGE.path(old_key)).write_bytes(b"newer-version-at-same-key")
    callback = callbacks[0]
    assert callable(callback)
    callback()

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert Path(_STORAGE.path(old_key)).read_bytes() == b"newer-version-at-same-key"


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_malformed_old_object_version_is_never_passed_to_destructive_cleanup(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-bad-old-version")
    old_key = "existing/malformed-old.bin"
    _STORAGE.save(old_key, ContentFile(b"old"))
    record = FinalizationRecord.objects.create(avatar=old_key)
    intent, candidate = _uploaded_intent(
        user,
        operation=UploadOperation.UPDATE,
        target_id=serialize_dependency_identifier({"id": record.pk}),
    )
    FinalizationAdapter.malformed_old_version = True

    FinalizationInterface(record.pk).update(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert intent.old_object_version == {}
    assert _STORAGE.exists(old_key)


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_sqlite_application_atomic_fails_before_inspection_or_claim(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-nested-atomic")
    intent, candidate = _uploaded_intent(user)

    with transaction.atomic():
        with pytest.raises(UploadStorageError):
            FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value
    assert intent.final_key is None
    assert FinalizationAdapter.materialize_calls == 0


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
@pytest.mark.parametrize(
    "unsafe_key",
    (
        "uploads/control\x00name.bin",
        "uploads/" + ("x" * 1100),
        "gm-staging/escaped.bin",
        "gm-upload-meta/escaped.json",
    ),
)
def test_unsafe_reserved_final_key_is_rejected_before_domain_write(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
    unsafe_key: str,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-unsafe-key")
    intent, candidate = _uploaded_intent(user)
    field = FinalizationRecord._meta.get_field("avatar")
    monkeypatch.setattr(field, "generate_filename", lambda *_args: unsafe_key)

    with pytest.raises(UploadStorageChangedError):
        FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value
    assert intent.final_key is None


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_upload_to_must_preserve_intent_uuid_in_reserved_key(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-constant-key")
    intent, candidate = _uploaded_intent(user)
    field = FinalizationRecord._meta.get_field("avatar")
    monkeypatch.setattr(
        field,
        "generate_filename",
        lambda *_args: "uploads/constant.bin",
    )

    with pytest.raises(UploadStorageChangedError):
        FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value
    assert intent.final_key is None


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_consumed_candidate_replay_cannot_write_second_domain_row(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-replay")
    _intent, candidate = _uploaded_intent(user)
    FinalizationInterface.create(creator_id=user.pk, avatar=candidate, label="first")

    with pytest.raises(UploadAlreadyConsumedError):
        FinalizationInterface.create(
            creator_id=user.pk,
            avatar=candidate,
            label="second",
        )

    assert FinalizationRecord.objects.filter(label="first").count() == 1
    assert not FinalizationRecord.objects.filter(label="second").exists()


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_superseded_candidate_preserves_stable_state_error(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-state-race")
    intent, candidate = _uploaded_intent(user)
    UploadIntent.objects.filter(pk=intent.pk).update(
        state=UploadIntentState.SUPERSEDED.value
    )

    with pytest.raises(UploadSupersededError):
        FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    assert not FinalizationRecord.objects.exists()


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_filesystem_replacement_completes_exact_old_file_cleanup(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-delete-old")
    _STORAGE.save("existing/delete-after.bin", ContentFile(b"old"))
    record = FinalizationRecord.objects.create(avatar="existing/delete-after.bin")
    intent, candidate = _uploaded_intent(
        user,
        operation=UploadOperation.UPDATE,
        target_id=serialize_dependency_identifier({"id": record.pk}),
    )

    FinalizationInterface(record.pk).update(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert not _STORAGE.exists("existing/delete-after.bin")
    assert intent.old_cleanup_key
    assert intent.old_cleanup_completed_at is not None


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_exact_cleanup_adapter_completes_durable_replacement_saga(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_exact_cleanup_adapter(monkeypatch)
    user = django_user_model.objects.create_user(username="finalize-exact-old")
    old_key = "existing/exact-delete.bin"
    _STORAGE.save(old_key, ContentFile(b"old"))
    record = FinalizationRecord.objects.create(avatar=old_key)
    intent, candidate = _uploaded_intent(
        user,
        operation=UploadOperation.UPDATE,
        target_id=serialize_dependency_identifier({"id": record.pk}),
    )

    FinalizationInterface(record.pk).update(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert intent.old_cleanup_key == old_key
    assert intent.old_cleanup_version == intent.old_object_version
    assert intent.old_cleanup_completed_at is not None
    assert not _STORAGE.exists(old_key)


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_exact_cleanup_persisted_plan_finishes_after_setting_is_disabled(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_exact_cleanup_adapter(monkeypatch)
    user = django_user_model.objects.create_user(username="finalize-exact-toggle")
    old_key = "existing/exact-toggle.bin"
    _STORAGE.save(old_key, ContentFile(b"old"))
    record = FinalizationRecord.objects.create(avatar=old_key)
    intent, candidate = _uploaded_intent(
        user,
        operation=UploadOperation.UPDATE,
        target_id=serialize_dependency_identifier({"id": record.pk}),
    )

    def crash_after_plan(*_args: object, **_kwargs: object) -> None:
        raise OSError

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            ExactCleanupFinalizationAdapter,
            "claim_replaced_object",
            crash_after_plan,
        )
        FinalizationInterface(record.pk).update(creator_id=user.pk, avatar=candidate)
    intent.refresh_from_db()
    assert intent.old_cleanup_key == old_key
    assert intent.old_cleanup_completed_at is None
    assert _STORAGE.exists(old_key)

    with override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": False}
        }
    ):
        finalization.finalize_upload_intent(intent.id)

    intent.refresh_from_db()
    assert intent.old_cleanup_completed_at is not None
    assert not _STORAGE.exists(old_key)


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_recreated_old_key_survives_repeated_terminal_cleanup(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-old-recreated")
    old_key = "existing/recreated.bin"
    _STORAGE.save(old_key, ContentFile(b"same-bytes"))
    record = FinalizationRecord.objects.create(avatar=old_key)
    intent, candidate = _uploaded_intent(
        user,
        operation=UploadOperation.UPDATE,
        target_id=serialize_dependency_identifier({"id": record.pk}),
    )
    FinalizationInterface(record.pk).update(creator_id=user.pk, avatar=candidate)
    intent.refresh_from_db()
    assert intent.old_cleanup_completed_at is not None
    assert not _STORAGE.exists(old_key)
    _STORAGE.save(old_key, ContentFile(b"same-bytes"))

    finalization.finalize_upload_intent(intent.id)

    intent.refresh_from_db()
    assert intent.old_cleanup_completed_at is not None
    assert _STORAGE.exists(old_key)


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_concurrent_terminal_cleanup_is_idempotent_after_filesystem_claim(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-old-concurrent")
    old_key = "existing/concurrent-recreation.bin"
    _STORAGE.save(old_key, ContentFile(b"same"))
    record = FinalizationRecord.objects.create(avatar=old_key)
    intent, candidate = _uploaded_intent(
        user,
        operation=UploadOperation.UPDATE,
        target_id=serialize_dependency_identifier({"id": record.pk}),
    )
    FinalizationInterface(record.pk).update(creator_id=user.pk, avatar=candidate)
    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert intent.old_cleanup_key

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(
            executor.map(
                lambda _index: finalization.finalize_upload_intent(intent.id), range(2)
            )
        )

    intent.refresh_from_db()
    assert intent.old_cleanup_completed_at is not None
    assert not _STORAGE.exists(old_key)
    assert not _STORAGE.exists(intent.old_cleanup_key)


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_old_cleanup_recovers_crash_after_durable_plan_before_storage_claim(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-old-claim-crash")
    old_key = "existing/claim-crash.bin"
    _STORAGE.save(old_key, ContentFile(b"old"))
    record = FinalizationRecord.objects.create(avatar=old_key)
    intent, candidate = _uploaded_intent(
        user,
        operation=UploadOperation.UPDATE,
        target_id=serialize_dependency_identifier({"id": record.pk}),
    )

    def crash_before_storage_claim(*_args: object, **_kwargs: object) -> None:
        raise OSError

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            FinalizationAdapter,
            "claim_replaced_object",
            crash_before_storage_claim,
        )
        FinalizationInterface(record.pk).update(creator_id=user.pk, avatar=candidate)
    intent.refresh_from_db()
    assert intent.old_cleanup_key
    assert intent.old_cleanup_version == intent.old_object_version
    assert _STORAGE.exists(old_key)

    with override_settings(
        GENERAL_MANAGER={
            "FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": False}
        }
    ):
        finalization.finalize_upload_intent(intent.id)

    intent.refresh_from_db()
    assert intent.old_cleanup_completed_at is not None
    assert not _STORAGE.exists(old_key)


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_omitted_file_field_keeps_existing_binding_and_bytes(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-omitted")
    old_key = "existing/omitted.bin"
    _STORAGE.save(old_key, ContentFile(b"old"))
    record = FinalizationRecord.objects.create(avatar=old_key)
    before = UploadIntent.objects.count()

    FinalizationInterface(record.pk).update(creator_id=user.pk, label="changed")

    record.refresh_from_db()
    assert record.avatar.name == old_key
    assert _STORAGE.exists(old_key)
    assert UploadIntent.objects.count() == before


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "DELETE_REPLACED_FILES": True}}
)
def test_explicit_null_clear_retains_old_bytes_without_inventing_intent_saga(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-clear")
    old_key = "existing/clear.bin"
    _STORAGE.save(old_key, ContentFile(b"old"))
    record = FinalizationRecord.objects.create(avatar=old_key)
    before = UploadIntent.objects.count()

    FinalizationInterface(record.pk).update(creator_id=user.pk, avatar=None)

    record.refresh_from_db()
    assert not record.avatar.name
    assert _STORAGE.exists(old_key)
    assert UploadIntent.objects.count() == before


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_multiple_intents_are_claimed_together(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-multiple")
    first, avatar = _uploaded_intent(user, field_name="avatar", content=b"avatar")
    second, document = _uploaded_intent(
        user,
        field_name="document",
        content=b"document",
    )

    result = FinalizationInterface.create(
        creator_id=user.pk,
        avatar=avatar,
        document=document,
    )

    record = FinalizationRecord.objects.get(pk=result["id"])
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.state == second.state == UploadIntentState.CONSUMED.value
    assert record.avatar.name == first.final_key
    assert record.document.name == second.final_key
    assert _STORAGE.exists(record.avatar.name)
    assert _STORAGE.exists(record.document.name)


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_materialized_final_is_reused_after_state_update_failure(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-crash-point")
    intent, candidate = _uploaded_intent(user)

    def fail_state_update(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        raise OSError

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            finalization,
            "_complete_finalization",
            fail_state_update,
        )
        result = FinalizationInterface.create(creator_id=user.pk, avatar=candidate)
    intent.refresh_from_db()
    record = FinalizationRecord.objects.get(pk=result["id"])
    assert intent.state == UploadIntentState.FINALIZING.value
    assert intent.finalization_error_code == "UPLOAD_STORAGE_ERROR"
    assert _STORAGE.exists(record.avatar.name)
    assert _STORAGE.exists(intent.staging_key)
    UploadIntent.objects.filter(pk=intent.pk).update(
        cleanup_lease_expires_at=timezone.now() - timedelta(seconds=1)
    )

    finalization.finalize_upload_intent(intent.id)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert FinalizationAdapter.materialize_calls == 2
    assert _STORAGE.exists(record.avatar.name)
    assert not _STORAGE.exists(intent.staging_key)


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_consumed_cleanup_failure_is_retryable_without_rematerializing(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-consumed-cleanup")
    intent, candidate = _uploaded_intent(user)
    FinalizationAdapter.fail_delete_stage = True
    FinalizationInterface.create(creator_id=user.pk, avatar=candidate)
    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert _STORAGE.exists(intent.staging_key)

    FinalizationAdapter.fail_delete_stage = False
    finalization.finalize_upload_intent(intent.id)

    assert not _STORAGE.exists(intent.staging_key)
    assert FinalizationAdapter.materialize_calls == 1


@pytest.mark.django_db(transaction=True)
@override_settings(
    GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "CLEANUP_LEASE_SECONDS": 1}}
)
def test_blocking_materialization_heartbeat_prevents_second_worker_reclaim(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-heartbeat")
    intent, candidate = _uploaded_intent(user)
    callbacks: list[object] = []

    def capture_callback(callback: object, **_kwargs: object) -> None:
        callbacks.append(callback)

    monkeypatch.setattr(finalization.transaction, "on_commit", capture_callback)
    FinalizationInterface.create(creator_id=user.pk, avatar=candidate)
    intent.refresh_from_db()
    assert intent.state == UploadIntentState.FINALIZING.value

    entered = Event()
    release = Event()
    original_materialize = FinalizationAdapter.materialize

    def blocking_materialize(self, *args: object, **kwargs: object) -> str:
        owned = UploadIntent.objects.get(pk=intent.id)
        assert owned.cleanup_lease_token
        assert owned.cleanup_lease_expires_at is not None
        assert owned.cleanup_lease_expires_at > timezone.now()
        entered.set()
        assert release.wait(timeout=5)
        return original_materialize(self, *args, **kwargs)  # type: ignore[arg-type]

    def finalize() -> str:
        close_old_connections()
        try:
            return finalization.finalize_upload_intent(intent.id)
        finally:
            close_old_connections()

    with (
        patch.object(FinalizationAdapter, "materialize", blocking_materialize),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        future = executor.submit(finalize)
        assert entered.wait(timeout=5)
        time.sleep(1.25)
        second_claim = finalization._start_finalization_attempt(
            intent.id,
            alias="default",
            expected_lease_expires_at=None,
            expected_lease_token=None,
        )
        assert second_claim is None
        release.set()
        assert future.result(timeout=5) == UploadIntentState.CONSUMED.value

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.CONSUMED.value
    assert intent.cleanup_lease_expires_at is None
    assert intent.cleanup_lease_token == ""
    assert intent.cleanup_completed_at is not None
    assert FinalizationAdapter.materialize_calls == 1


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_superseded_cleanup_failure_is_retryable_from_terminal_state(
    django_user_model: type[models.Model],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = django_user_model.objects.create_user(username="finalize-super-cleanup")
    intent, candidate = _uploaded_intent(user)
    callbacks: list[object] = []

    def capture_callback(callback: object, **kwargs: object) -> None:
        del kwargs
        callbacks.append(callback)

    monkeypatch.setattr(finalization.transaction, "on_commit", capture_callback)
    result = FinalizationInterface.create(creator_id=user.pk, avatar=candidate)
    FinalizationRecord.objects.filter(pk=result["id"]).update(avatar="newer.bin")
    FinalizationAdapter.fail_delete_stage = True
    callback = callbacks[0]
    assert callable(callback)
    callback()
    intent.refresh_from_db()
    assert intent.state == UploadIntentState.SUPERSEDED.value
    assert _STORAGE.exists(intent.staging_key)

    FinalizationAdapter.fail_delete_stage = False
    finalization.finalize_upload_intent(intent.id)

    assert not _STORAGE.exists(intent.staging_key)


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_malformed_final_object_identity_remains_recoverable_finalizing(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-bad-final-version")
    intent, candidate = _uploaded_intent(user)
    FinalizationAdapter.malformed_final_version = True

    result = FinalizationInterface.create(creator_id=user.pk, avatar=candidate)

    intent.refresh_from_db()
    record = FinalizationRecord.objects.get(pk=result["id"])
    assert intent.state == UploadIntentState.FINALIZING.value
    assert intent.finalization_error_code == "UPLOAD_STORAGE_CHANGED"
    assert intent.final_object_version == {}
    assert record.avatar.name == intent.final_key


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_concurrent_consumption_has_one_winner_and_one_stable_replay_error(
    django_user_model: type[models.Model],
) -> None:
    user = django_user_model.objects.create_user(username="finalize-concurrent")
    intent, candidate = _uploaded_intent(user)
    barrier = Barrier(2)

    def consume(label: str) -> str:
        barrier.wait()
        try:
            FinalizationInterface.create(
                creator_id=user.pk,
                avatar=candidate,
                label=label,
            )
        except UploadAlreadyConsumedError:
            return "replayed"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(consume, ("one", "two")))

    intent.refresh_from_db()
    assert outcomes == ["created", "replayed"]
    assert intent.state == UploadIntentState.CONSUMED.value
    assert FinalizationRecord.objects.filter(label__in=("one", "two")).count() == 1
