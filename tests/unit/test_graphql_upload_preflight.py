"""Tests for redacted upload-token preflight in generated mutations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import timedelta
from types import SimpleNamespace
from typing import ClassVar, IO
import tempfile
import traceback
from uuid import UUID

import graphene
import pytest
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.db.models import NOT_PROVIDED
from django.test import override_settings
from django.utils import timezone
from graphql import ExecutionResult, GraphQLError

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_mutations import generate_update_mutation_class
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.uploads.adapters import (
    ProxyUploadAdapter,
    UploadAdapterRegistry,
    UploadInstructions,
)
from general_manager.uploads.models import UploadIntent
from general_manager.uploads.errors import (
    UploadDatabaseMismatchError,
    UploadStorageError,
)
from general_manager.uploads import services
from general_manager.uploads.tokens import issue_upload_token
from general_manager.uploads.types import (
    UploadCandidate,
    UploadIntentState,
    UploadOperation,
    ObjectVersion,
    ChecksumAlgorithm,
    UploadTransport,
)


class PreflightStorage(FileSystemStorage):
    """Unique storage type for deterministic adapter registration."""


_STORAGE = PreflightStorage(
    location=f"{tempfile.gettempdir()}/general-manager-preflight-tests"
)


class DirectPreflightStorage(FileSystemStorage):
    """Unique storage type for direct immutable-version tests."""


_DIRECT_STORAGE = DirectPreflightStorage(
    location=f"{tempfile.gettempdir()}/general-manager-direct-preflight-tests"
)


class PreflightRecord(models.Model):
    avatar = models.ImageField(storage=_STORAGE, upload_to="avatars/", blank=True)
    document = models.FileField(storage=_STORAGE, upload_to="documents/", blank=True)
    direct_document = models.FileField(
        storage=_DIRECT_STORAGE,
        upload_to="direct/",
        blank=True,
    )
    direct_document_2 = models.FileField(
        storage=_DIRECT_STORAGE,
        upload_to="direct/",
        blank=True,
    )
    label = models.CharField(max_length=255)
    website = models.URLField()
    configured_path = models.FilePathField(path="/var/empty")

    class Meta:
        app_label = "general_manager"
        managed = False


class PreflightInterface(OrmInterfaceBase[PreflightRecord]):
    _model = PreflightRecord
    database: ClassVar[str | None] = None
    input_fields: ClassVar[dict[str, Input[type[object]]]] = {
        "id": Input(int)  # type: ignore[dict-item]
    }

    def __init__(self, id: object) -> None:
        self.identification = {"id": int(id)}

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        return {
            "avatar": {
                "type": str,
                "orm_field_kind": "image",
                "is_required": False,
                "is_editable": True,
                "is_derived": False,
                "default": NOT_PROVIDED,
            },
            "document": {
                "type": str,
                "orm_field_kind": "file",
                "is_required": False,
                "is_editable": True,
                "is_derived": False,
                "default": NOT_PROVIDED,
            },
            "direct_document": {
                "type": str,
                "orm_field_kind": "file",
                "is_required": False,
                "is_editable": True,
                "is_derived": False,
                "default": NOT_PROVIDED,
            },
            "direct_document_2": {
                "type": str,
                "orm_field_kind": "file",
                "is_required": False,
                "is_editable": True,
                "is_derived": False,
                "default": NOT_PROVIDED,
            },
            "label": {
                "type": str,
                "is_required": False,
                "is_editable": True,
                "is_derived": False,
                "default": NOT_PROVIDED,
            },
            "website": {
                "type": str,
                "is_required": False,
                "is_editable": True,
                "is_derived": False,
                "default": NOT_PROVIDED,
            },
            "configured_path": {
                "type": str,
                "is_required": False,
                "is_editable": True,
                "is_derived": False,
                "default": NOT_PROVIDED,
            },
        }


class AnalyticsWriteRouter:
    """Router that must not override a falsey interface alias during preflight."""

    def db_for_write(self, model: type[models.Model], **hints: object) -> str:
        del model, hints
        return "analytics"


class PreflightManager(GeneralManager):
    _attributes: ClassVar[dict[str, object]] = {}

    @classmethod
    def create(cls, **kwargs: object) -> PreflightManager:
        _RECEIVED_CREATES.append(kwargs)
        return cls(id=1)

    def update(self, **kwargs: object) -> PreflightManager:
        _RECEIVED_UPDATES.append(kwargs)
        if _FAIL_UPDATE:
            raise PermissionError
        return self


PreflightManager.Interface = PreflightInterface  # type: ignore[assignment]


class PreflightProxyAdapter(ProxyUploadAdapter):
    adapter_id = "tests.preflight-proxy"
    adapter_version = 1

    def storage_fingerprint(self) -> str:
        return "sha256:preflight-storage"

    def inspect_staged(self, stage_key: str) -> ObjectVersion:
        version = _PROXY_PENDING_VERSIONS.get(stage_key)
        if version is not None:
            return version
        return super().inspect_staged(stage_key)


class DirectPreflightAdapter:
    """Minimal direct adapter with deterministic immutable metadata."""

    adapter_id = "tests.preflight-direct"
    adapter_version = 3
    versions: ClassVar[dict[str, ObjectVersion]] = {}
    inspect_calls: ClassVar[list[str]] = []
    inspect_hook: ClassVar[Callable[[str, ObjectVersion], None] | None] = None
    inspect_error: ClassVar[Exception | None] = None

    def __init__(self, storage: FileSystemStorage) -> None:
        self.storage = storage

    @property
    def supports_public_urls(self) -> bool:
        return False

    @classmethod
    def supports_direct(cls, storage: FileSystemStorage) -> bool:
        del storage
        return True

    def create_upload_instructions(self, **kwargs: object) -> UploadInstructions:
        del kwargs
        return UploadInstructions(
            transport=UploadTransport.DIRECT,
            method="PUT",
            url="https://upload.example.test/signed",
        )

    def inspect_staged(self, stage_key: str) -> ObjectVersion:
        type(self).inspect_calls.append(stage_key)
        if type(self).inspect_error is not None:
            raise type(self).inspect_error
        version = type(self).versions[stage_key]
        hook = type(self).inspect_hook
        if hook is not None:
            hook(stage_key, version)
        return version

    def save_stage(
        self,
        stage_key: str,
        chunks: Iterable[bytes],
        *,
        content_type: str | None,
        checksum_sha256: str | None = None,
        size: int | None = None,
    ) -> ObjectVersion:
        """Expose a hybrid proxy capability without changing direct preflight."""
        del stage_key, chunks, content_type, checksum_sha256, size
        raise NotImplementedError

    def materialize(
        self,
        stage_key: str,
        version: ObjectVersion,
        final_key: str,
        *,
        intent_id: UUID,
    ) -> str:
        del stage_key, version, intent_id
        return final_key

    def open_stage(self, stage_key: str, version: ObjectVersion) -> IO[bytes]:
        del stage_key, version
        raise NotImplementedError

    def delete_stage(
        self,
        stage_key: str,
        version: ObjectVersion | None = None,
    ) -> None:
        del stage_key, version

    def private_download_url(self, key: str, *, expires_in: int) -> str:
        del key, expires_in
        raise NotImplementedError

    def inspect_download(
        self,
        key: str,
        version: ObjectVersion,
    ) -> ObjectVersion:
        del key
        return version

    def open_download(self, key: str, version: ObjectVersion) -> IO[bytes]:
        del key, version
        raise NotImplementedError

    def public_url(self, key: str) -> str:
        del key
        raise NotImplementedError

    def storage_fingerprint(self) -> str:
        return "sha256:direct-preflight-storage"


_RECEIVED_UPDATES: list[dict[str, object]] = []
_RECEIVED_CREATES: list[dict[str, object]] = []
_FAIL_UPDATE = False
_PROXY_PENDING_VERSIONS: dict[str, ObjectVersion] = {}


@pytest.fixture(autouse=True)
def _upload_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    global _FAIL_UPDATE
    GraphQL.reset_registry()
    GraphQL.manager_registry[PreflightManager.__name__] = PreflightManager
    registry = UploadAdapterRegistry()
    registry.register(PreflightStorage, PreflightProxyAdapter)
    registry.register(DirectPreflightStorage, DirectPreflightAdapter)
    monkeypatch.setattr(services, "upload_adapter_registry", registry)
    _RECEIVED_UPDATES.clear()
    _RECEIVED_CREATES.clear()
    DirectPreflightAdapter.versions.clear()
    DirectPreflightAdapter.inspect_calls.clear()
    DirectPreflightAdapter.inspect_hook = None
    DirectPreflightAdapter.inspect_error = None
    _PROXY_PENDING_VERSIONS.clear()
    _FAIL_UPDATE = False
    yield
    GraphQL.reset_registry()


@pytest.fixture
def user(django_user_model: type[models.Model]) -> models.Model:
    return django_user_model.objects.create_user(username="preflight-user")


def _uploaded_intent(
    user: models.Model,
    *,
    field: str = "avatar",
    target_id: str = serialize_dependency_identifier({"id": 7}),
    state: UploadIntentState = UploadIntentState.UPLOADED,
    expires_delta: timedelta = timedelta(minutes=5),
    operation: UploadOperation = UploadOperation.UPDATE,
    adapter_id: str = PreflightProxyAdapter.adapter_id,
    adapter_version: int = PreflightProxyAdapter.adapter_version,
    storage_fingerprint: str = "sha256:preflight-storage",
    stage_key: str = "gm-staging/private-stage-key",
) -> tuple[UploadIntent, str]:
    token, digest = issue_upload_token()
    intent = UploadIntent.objects.create(
        user=user,
        token_digest=digest,
        manager_name=PreflightManager.__name__,
        field_name=field,
        operation=operation.value,
        target_id=target_id,
        adapter_id=adapter_id,
        adapter_version=str(adapter_version),
        storage_fingerprint=storage_fingerprint,
        staging_key=stage_key,
        original_filename=f"{field}.png",
        declared_size=3,
        declared_content_type="image/png",
        declared_checksum_sha256="a" * 64,
        verified_size=3,
        verified_content_type="image/png",
        verified_checksum_sha256="a" * 64,
        object_version={
            "version_id": None,
            "etag": None,
            "checksum_sha256": "a" * 64,
            "size": 3,
            "content_type": "image/png",
        },
        state=state.value,
        expires_at=timezone.now() + expires_delta,
        uploaded_at=timezone.now(),
    )
    return intent, token


def _execute_update(user: models.Model, **kwargs: object) -> dict[str, object]:
    mutation = generate_update_mutation_class(
        PreflightManager,
        {"success": graphene.Boolean()},
    )
    assert mutation is not None
    info = SimpleNamespace(context=SimpleNamespace(user=user))
    return mutation.mutate(None, info, id="007", **kwargs)


def _execute_create(user: models.Model, **kwargs: object) -> dict[str, object]:
    from general_manager.api.graphql_mutations import generate_create_mutation_class

    mutation = generate_create_mutation_class(
        PreflightManager,
        {"success": graphene.Boolean()},
    )
    assert mutation is not None
    info = SimpleNamespace(context=SimpleNamespace(user=user))
    return mutation.mutate(None, info, **kwargs)


def _execute_update_schema(
    user: models.Model,
    *,
    include_null: bool,
) -> ExecutionResult:
    mutation_type = generate_update_mutation_class(
        PreflightManager,
        {"success": graphene.Boolean()},
    )
    assert mutation_type is not None

    class Query(graphene.ObjectType):
        ready = graphene.Boolean(default_value=True)

    mutation_root = type(
        "Mutation",
        (graphene.ObjectType,),
        {"updateProfile": mutation_type.Field()},
    )
    schema = graphene.Schema(query=Query, mutation=mutation_root)
    avatar_argument = ", avatar: null" if include_null else ""
    return schema.execute(
        f'mutation {{ updateProfile(id: "7"{avatar_argument}) {{ success }} }}',
        context_value=SimpleNamespace(user=user),
    )


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_permission_receives_candidate_not_raw_token(user: models.Model) -> None:
    intent, token = _uploaded_intent(user)

    result = _execute_update(user, avatar=token)

    assert result["success"] is True
    assert _RECEIVED_UPDATES
    candidate = _RECEIVED_UPDATES[0]["avatar"]
    assert isinstance(candidate, UploadCandidate)
    assert candidate.intent_id == intent.id
    assert token not in repr(candidate)
    assert intent.token_digest not in repr(candidate)
    assert intent.staging_key not in repr(candidate)


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_wrong_binding_fails_before_permission_and_redacts_error(
    user: models.Model,
) -> None:
    _intent, token = _uploaded_intent(user, field="avatar")

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, document=token)

    assert caught.value.extensions == {"code": "UPLOAD_BINDING_MISMATCH"}
    assert not _RECEIVED_UPDATES
    assert token not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_error_traceback_locals_and_candidate_serialization_contain_no_secrets(
    user: models.Model,
) -> None:
    intent, token = _uploaded_intent(user)

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, document=token)

    traceback_exception = traceback.TracebackException.from_exception(
        caught.value,
        capture_locals=True,
    )
    production_locals = repr(
        [
            frame.locals
            for frame in traceback_exception.stack
            if "/src/general_manager/" in frame.filename
        ]
    )
    public_error = repr(
        (caught.value.args, caught.value.extensions, caught.value.formatted)
    )
    for rendered in (production_locals, public_error):
        assert token not in rendered
        assert intent.token_digest not in rendered
        assert intent.staging_key not in rendered

    _RECEIVED_UPDATES.clear()
    _execute_update(user, avatar=token)
    candidate = _RECEIVED_UPDATES[0]["avatar"]
    assert isinstance(candidate, UploadCandidate)
    serialized = repr(asdict(candidate))
    assert token not in serialized
    assert intent.token_digest not in serialized
    assert intent.staging_key not in serialized


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_runtime_metadata_failure_scrubs_token_before_safe_error(
    user: models.Model,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _intent, token = _uploaded_intent(user)
    mutation = generate_update_mutation_class(
        PreflightManager,
        {"success": graphene.Boolean()},
    )
    assert mutation is not None

    def fail_metadata(_cls: object) -> dict[str, dict[str, object]]:
        raise RuntimeError("metadata-hook-secret")

    monkeypatch.setattr(
        PreflightInterface,
        "get_attribute_types",
        classmethod(fail_metadata),
    )
    info = SimpleNamespace(context=SimpleNamespace(user=user))

    with pytest.raises(GraphQLError) as caught:
        mutation.mutate(None, info, id="7", avatar=token)

    production_locals = repr(
        [
            frame.locals
            for frame in traceback.TracebackException.from_exception(
                caught.value,
                capture_locals=True,
            ).stack
            if "/src/general_manager/" in frame.filename
        ]
    )
    assert caught.value.extensions == {"code": "UPLOAD_STORAGE_ERROR"}
    assert token not in production_locals
    assert "metadata-hook-secret" not in str(caught.value)
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_only_real_orm_file_metadata_is_preflighted(user: models.Model) -> None:
    _execute_update(user, label="ordinary-string")

    assert _RECEIVED_UPDATES
    assert _RECEIVED_UPDATES[0]["label"] == "ordinary-string"


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_explicit_null_and_omitted_file_fields_remain_distinct(
    user: models.Model,
) -> None:
    _execute_update(user, avatar=None)
    assert _RECEIVED_UPDATES
    assert "avatar" in _RECEIVED_UPDATES[0]
    assert _RECEIVED_UPDATES[0]["avatar"] is None
    assert "document" not in _RECEIVED_UPDATES[0]


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_graphql_execution_keeps_omitted_and_explicit_null_distinct(
    user: models.Model,
) -> None:
    omitted = _execute_update_schema(user, include_null=False)
    assert omitted.errors is None
    assert _RECEIVED_UPDATES == [{"creator_id": user.pk}]

    _RECEIVED_UPDATES.clear()
    explicit_null = _execute_update_schema(user, include_null=True)
    assert explicit_null.errors is None
    assert _RECEIVED_UPDATES == [{"creator_id": user.pk, "avatar": None}]


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
@pytest.mark.parametrize(
    ("state", "expires_delta", "code"),
    [
        (UploadIntentState.PENDING, timedelta(minutes=5), "UPLOAD_INCOMPLETE"),
        (UploadIntentState.CONSUMED, timedelta(minutes=5), "UPLOAD_ALREADY_CONSUMED"),
        (UploadIntentState.SUPERSEDED, timedelta(minutes=5), "UPLOAD_SUPERSEDED"),
        (UploadIntentState.UPLOADED, timedelta(seconds=-1), "UPLOAD_EXPIRED"),
    ],
)
def test_preflight_maps_state_and_expiry_before_permission(
    user: models.Model,
    state: UploadIntentState,
    expires_delta: timedelta,
    code: str,
) -> None:
    _intent, token = _uploaded_intent(
        user,
        state=state,
        expires_delta=expires_delta,
    )

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, avatar=token)

    assert caught.value.extensions == {"code": code}
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_pending_proxy_cannot_bypass_transfer_state_when_stage_exists(
    user: models.Model,
) -> None:
    intent, token = _uploaded_intent(user, state=UploadIntentState.PENDING)
    _PROXY_PENDING_VERSIONS[intent.staging_key] = _direct_version()

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, avatar=token)

    intent.refresh_from_db()
    assert caught.value.extensions == {"code": "UPLOAD_INCOMPLETE"}
    assert intent.state == UploadIntentState.PENDING.value
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_missing_token_is_enumeration_resistant(user: models.Model) -> None:
    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, avatar="opaque-token-that-does-not-exist")

    assert caught.value.extensions == {"code": "UPLOAD_TOKEN_INVALID"}
    assert caught.value.message == "The file upload could not be completed."
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_canonical_update_target_binding_accepts_equivalent_id(
    user: models.Model,
) -> None:
    _intent, token = _uploaded_intent(user)

    _execute_update(user, avatar=token)

    assert _RECEIVED_UPDATES
    assert isinstance(_RECEIVED_UPDATES[0]["avatar"], UploadCandidate)


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_create_preflight_uses_create_binding_before_manager_call(
    user: models.Model,
) -> None:
    intent, token = _uploaded_intent(
        user,
        operation=UploadOperation.CREATE,
        target_id=None,
    )

    _execute_create(user, avatar=token)

    assert _RECEIVED_CREATES
    candidate = _RECEIVED_CREATES[0]["avatar"]
    assert isinstance(candidate, UploadCandidate)
    assert candidate.intent_id == intent.id


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_wrong_actor_is_token_invalid_before_manager_call(
    user: models.Model,
    django_user_model: type[models.Model],
) -> None:
    _intent, token = _uploaded_intent(user)
    other = django_user_model.objects.create_user(username="other-preflight-user")

    with pytest.raises(GraphQLError) as caught:
        _execute_update(other, avatar=token)

    assert caught.value.extensions == {"code": "UPLOAD_TOKEN_INVALID"}
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_url_and_filepath_strings_are_not_treated_as_upload_tokens(
    user: models.Model,
) -> None:
    _execute_update(
        user,
        website="https://example.test/file",
        configured_path="/var/empty/example.txt",
    )

    assert _RECEIVED_UPDATES == [
        {
            "creator_id": user.pk,
            "website": "https://example.test/file",
            "configured_path": "/var/empty/example.txt",
        }
    ]


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_adapter_identity_change_fails_before_manager_call(user: models.Model) -> None:
    _intent, token = _uploaded_intent(
        user,
        adapter_version=999,
    )

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, avatar=token)

    assert caught.value.extensions == {"code": "UPLOAD_STORAGE_CHANGED"}
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_tampered_filename_cannot_enter_candidate_or_permission_payload(
    user: models.Model,
) -> None:
    intent, token = _uploaded_intent(user)
    UploadIntent.objects.filter(pk=intent.pk).update(
        original_filename="../storage-secret.txt"
    )

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, avatar=token)

    assert caught.value.extensions == {"code": "UPLOAD_STORAGE_CHANGED"}
    assert not _RECEIVED_UPDATES


def _direct_version(
    *,
    checksum: str = "a" * 64,
    size: int = 3,
    content_type: str = "image/png",
) -> ObjectVersion:
    return ObjectVersion(
        version_id="version-1",
        etag="etag-1",
        checksum_sha256=checksum,
        size=size,
        content_type=content_type,
    )


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_direct_preflight_persists_exact_immutable_version_before_manager(
    user: models.Model,
) -> None:
    stage_key = "gm-staging/direct-one"
    intent, token = _uploaded_intent(
        user,
        field="direct_document",
        state=UploadIntentState.PENDING,
        adapter_id=DirectPreflightAdapter.adapter_id,
        adapter_version=DirectPreflightAdapter.adapter_version,
        storage_fingerprint="sha256:direct-preflight-storage",
        stage_key=stage_key,
    )
    DirectPreflightAdapter.versions[stage_key] = _direct_version()

    _execute_update(user, direct_document=token)

    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value
    assert intent.object_version == {
        "version_id": "version-1",
        "etag": "etag-1",
        "checksum_sha256": "a" * 64,
        "size": 3,
        "content_type": "image/png",
    }
    assert isinstance(_RECEIVED_UPDATES[0]["direct_document"], UploadCandidate)


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_promoted_direct_intent_retries_after_permission_denial_without_reinspection(
    user: models.Model,
) -> None:
    global _FAIL_UPDATE
    stage_key = "gm-staging/direct-retry"
    intent, token = _uploaded_intent(
        user,
        field="direct_document",
        state=UploadIntentState.PENDING,
        adapter_id=DirectPreflightAdapter.adapter_id,
        adapter_version=DirectPreflightAdapter.adapter_version,
        storage_fingerprint="sha256:direct-preflight-storage",
        stage_key=stage_key,
    )
    DirectPreflightAdapter.versions[stage_key] = _direct_version()
    _FAIL_UPDATE = True

    with pytest.raises(GraphQLError) as denied:
        _execute_update(user, direct_document=token)

    assert denied.value.extensions == {"code": "PERMISSION_DENIED"}
    intent.refresh_from_db()
    assert intent.state == UploadIntentState.UPLOADED.value
    assert DirectPreflightAdapter.inspect_calls == [stage_key]

    _FAIL_UPDATE = False
    _RECEIVED_UPDATES.clear()
    _execute_update(user, direct_document=token)

    assert isinstance(_RECEIVED_UPDATES[0]["direct_document"], UploadCandidate)
    assert DirectPreflightAdapter.inspect_calls == [stage_key]


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_concurrent_equivalent_direct_promotion_is_idempotent(
    user: models.Model,
) -> None:
    stage_key = "gm-staging/direct-concurrent-promotion"
    intent, token = _uploaded_intent(
        user,
        field="direct_document",
        state=UploadIntentState.PENDING,
        adapter_id=DirectPreflightAdapter.adapter_id,
        adapter_version=DirectPreflightAdapter.adapter_version,
        storage_fingerprint="sha256:direct-preflight-storage",
        stage_key=stage_key,
    )
    version = _direct_version()
    DirectPreflightAdapter.versions[stage_key] = version

    def promote_while_inspecting(_stage_key: str, inspected: ObjectVersion) -> None:
        UploadIntent.objects.filter(pk=intent.pk).update(
            state=UploadIntentState.UPLOADED.value,
            verified_size=inspected.size,
            verified_content_type=inspected.content_type,
            verified_checksum_sha256=inspected.checksum_sha256,
            object_version={
                "version_id": inspected.version_id,
                "etag": inspected.etag,
                "checksum_sha256": inspected.checksum_sha256,
                "size": inspected.size,
                "content_type": inspected.content_type,
            },
            uploaded_at=timezone.now(),
        )

    DirectPreflightAdapter.inspect_hook = promote_while_inspecting

    _execute_update(user, direct_document=token)

    assert isinstance(_RECEIVED_UPDATES[0]["direct_document"], UploadCandidate)
    assert DirectPreflightAdapter.inspect_calls == [stage_key]


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_mixed_batch_revalidates_uploaded_version_before_any_promotion(
    user: models.Model,
) -> None:
    uploaded, uploaded_token = _uploaded_intent(user, field="avatar")
    stage_key = "gm-staging/direct-mixed-batch"
    pending, pending_token = _uploaded_intent(
        user,
        field="direct_document",
        state=UploadIntentState.PENDING,
        adapter_id=DirectPreflightAdapter.adapter_id,
        adapter_version=DirectPreflightAdapter.adapter_version,
        storage_fingerprint="sha256:direct-preflight-storage",
        stage_key=stage_key,
    )
    DirectPreflightAdapter.versions[stage_key] = _direct_version()

    def change_uploaded_version(_stage_key: str, _inspected: ObjectVersion) -> None:
        UploadIntent.objects.filter(pk=uploaded.pk).update(
            verified_checksum_sha256="b" * 64,
            object_version={
                **uploaded.object_version,
                "checksum_sha256": "b" * 64,
            },
        )

    DirectPreflightAdapter.inspect_hook = change_uploaded_version

    with pytest.raises(GraphQLError) as caught:
        _execute_update(
            user,
            avatar=uploaded_token,
            direct_document=pending_token,
        )

    pending.refresh_from_db()
    assert caught.value.extensions == {"code": "UPLOAD_CHECKSUM_MISMATCH"}
    assert pending.state == UploadIntentState.PENDING.value
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_direct_preflight_rejects_unsafe_immutable_identity(user: models.Model) -> None:
    stage_key = "gm-staging/direct-unsafe-version"
    intent, token = _uploaded_intent(
        user,
        field="direct_document",
        state=UploadIntentState.PENDING,
        adapter_id=DirectPreflightAdapter.adapter_id,
        adapter_version=DirectPreflightAdapter.adapter_version,
        storage_fingerprint="sha256:direct-preflight-storage",
        stage_key=stage_key,
    )
    DirectPreflightAdapter.versions[stage_key] = ObjectVersion(
        version_id="unsafe\nidentity",
        etag="etag-1",
        checksum_sha256="a" * 64,
        size=3,
        content_type="image/png",
    )

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, direct_document=token)

    intent.refresh_from_db()
    assert caught.value.extensions == {"code": "UPLOAD_STORAGE_CHANGED"}
    assert intent.state == UploadIntentState.PENDING.value


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_direct_inspection_storage_failure_stays_storage_error(
    user: models.Model,
) -> None:
    intent, token = _uploaded_intent(
        user,
        field="direct_document",
        state=UploadIntentState.PENDING,
        adapter_id=DirectPreflightAdapter.adapter_id,
        adapter_version=DirectPreflightAdapter.adapter_version,
        storage_fingerprint="sha256:direct-preflight-storage",
        stage_key="gm-staging/direct-storage-error",
    )
    DirectPreflightAdapter.inspect_error = UploadStorageError()

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, direct_document=token)

    intent.refresh_from_db()
    assert caught.value.extensions == {"code": "UPLOAD_STORAGE_ERROR"}
    assert intent.state == UploadIntentState.PENDING.value


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
@pytest.mark.parametrize(
    ("version", "code"),
    [
        (_direct_version(size=4), "UPLOAD_SIZE_MISMATCH"),
        (_direct_version(checksum="b" * 64), "UPLOAD_CHECKSUM_MISMATCH"),
        (_direct_version(content_type="application/pdf"), "INVALID_FILE_TYPE"),
    ],
)
def test_direct_metadata_mismatch_has_specific_stable_code(
    user: models.Model,
    version: ObjectVersion,
    code: str,
) -> None:
    stage_key = "gm-staging/direct-mismatch"
    intent, token = _uploaded_intent(
        user,
        field="direct_document",
        state=UploadIntentState.PENDING,
        adapter_id=DirectPreflightAdapter.adapter_id,
        adapter_version=DirectPreflightAdapter.adapter_version,
        storage_fingerprint="sha256:direct-preflight-storage",
        stage_key=stage_key,
    )
    DirectPreflightAdapter.versions[stage_key] = version

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, direct_document=token)

    intent.refresh_from_db()
    assert caught.value.extensions == {"code": code}
    assert intent.state == UploadIntentState.PENDING.value
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_multiple_direct_fields_validate_in_order_without_partial_persistence(
    user: models.Model,
) -> None:
    first, first_token = _uploaded_intent(
        user,
        field="direct_document",
        state=UploadIntentState.PENDING,
        adapter_id=DirectPreflightAdapter.adapter_id,
        adapter_version=DirectPreflightAdapter.adapter_version,
        storage_fingerprint="sha256:direct-preflight-storage",
        stage_key="gm-staging/a-first",
    )
    second, second_token = _uploaded_intent(
        user,
        field="direct_document_2",
        state=UploadIntentState.PENDING,
        adapter_id=DirectPreflightAdapter.adapter_id,
        adapter_version=DirectPreflightAdapter.adapter_version,
        storage_fingerprint="sha256:direct-preflight-storage",
        stage_key="gm-staging/b-second",
    )
    DirectPreflightAdapter.versions[first.staging_key] = _direct_version()
    DirectPreflightAdapter.versions[second.staging_key] = _direct_version(size=4)

    with pytest.raises(GraphQLError):
        _execute_update(
            user,
            direct_document_2=second_token,
            direct_document=first_token,
        )

    first.refresh_from_db()
    second.refresh_from_db()
    assert DirectPreflightAdapter.inspect_calls == [
        first.staging_key,
        second.staging_key,
    ]
    assert first.state == UploadIntentState.PENDING.value
    assert second.state == UploadIntentState.PENDING.value
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_preflight_enforces_same_database_before_intent_query(
    user: models.Model,
) -> None:
    _intent, token = _uploaded_intent(user)
    previous = PreflightInterface.database
    PreflightInterface.database = "analytics"
    try:
        with pytest.raises(GraphQLError) as caught:
            _execute_update(user, avatar=token)
    finally:
        PreflightInterface.database = previous

    assert caught.value.extensions == {"code": "UPLOAD_DATABASE_MISMATCH"}
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "INTENT_DATABASE": "analytics",
        }
    },
    DATABASE_ROUTERS=["tests.unit.test_graphql_upload_preflight.AnalyticsWriteRouter"],
)
def test_falsey_interface_alias_normalizes_to_default_not_router_alias(
    user: models.Model,
) -> None:
    with override_settings(DATABASE_ROUTERS=[]):
        _intent, token = _uploaded_intent(user)

    with pytest.raises(GraphQLError) as caught:
        _execute_update(user, avatar=token)

    assert caught.value.extensions == {"code": "UPLOAD_DATABASE_MISMATCH"}
    assert not _RECEIVED_UPDATES


@pytest.mark.django_db
@override_settings(
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "INTENT_DATABASE": "analytics",
        }
    },
    DATABASE_ROUTERS=["tests.unit.test_graphql_upload_preflight.AnalyticsWriteRouter"],
)
def test_begin_rejects_falsey_interface_alias_before_issuing_unusable_token(
    user: models.Model,
) -> None:
    before = UploadIntent.objects.using("default").count()
    request = services.BeginFileUploadRequest(
        manager=PreflightManager.__name__,
        field="avatar",
        operation=UploadOperation.CREATE,
        filename="avatar.png",
        size=3,
        content_type="image/png",
        checksum=services.UploadChecksum(
            algorithm=ChecksumAlgorithm.SHA256,
            digest="a" * 64,
        ),
    )

    with pytest.raises(UploadDatabaseMismatchError):
        services.begin_file_upload(user=user, request=request)

    assert UploadIntent.objects.using("default").count() == before


@pytest.mark.django_db
@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True}})
def test_stale_generated_manager_is_rejected_before_permission(
    user: models.Model,
) -> None:
    _intent, token = _uploaded_intent(user)
    mutation = generate_update_mutation_class(
        PreflightManager,
        {"success": graphene.Boolean()},
    )
    assert mutation is not None
    GraphQL.manager_registry.clear()
    info = SimpleNamespace(context=SimpleNamespace(user=user))

    with pytest.raises(GraphQLError) as caught:
        mutation.mutate(None, info, id="7", avatar=token)

    assert caught.value.extensions == {"code": "UPLOAD_BINDING_MISMATCH"}
    assert not _RECEIVED_UPDATES
