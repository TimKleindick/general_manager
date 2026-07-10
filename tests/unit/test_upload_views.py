"""HTTP contract tests for the bounded proxy upload endpoint."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Barrier, Event
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.db import OperationalError, close_old_connections, models
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
from django.utils import timezone

from general_manager.api.graphql import GraphQL
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.uploads.adapters import (
    ProxyUploadAdapter,
    UploadAdapterRegistry,
)
from general_manager.uploads.config import FileUploadSettings
from general_manager.uploads.models import UploadIntent
from general_manager.uploads.services import issue_upload_transfer_credential
from general_manager.uploads.types import ObjectVersion, UploadIntentState


pytestmark = pytest.mark.django_db

_STORAGE = FileSystemStorage(
    location=Path(tempfile.gettempdir()) / "general-manager-upload-view-tests"
)
_RATE_CACHE_LOCATION = str(
    Path(tempfile.gettempdir()) / "general-manager-upload-transfer-rate-cache"
)
_ENABLED_UPLOADS = {
    "FILE_UPLOADS": {
        "ENABLED": True,
        "TRANSFER_LEASE_SECONDS": 30,
        "TRANSFER_CREDENTIAL_TTL_SECONDS": 120,
        "TRANSFER_RATE_LIMIT_WINDOW_SECONDS": 60,
        "MAX_TRANSFER_ATTEMPTS_PER_USER": 100,
        "MAX_TRANSFER_ATTEMPTS_GLOBAL": 1000,
    }
}


class ProxyUploadRecord(models.Model):
    avatar = models.FileField(storage=_STORAGE, upload_to="avatars/")

    class Meta:
        app_label = "general_manager"
        managed = False


class ProxyUploadInterface(OrmInterfaceBase[ProxyUploadRecord]):
    _model = ProxyUploadRecord

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        return {
            "avatar": {
                "type": str,
                "orm_field_kind": "file",
                "is_editable": True,
            }
        }


class ProxyUploadManager(GeneralManager):
    pass


ProxyUploadManager.Interface = ProxyUploadInterface  # type: ignore[assignment]


class RecordingProxyAdapter(ProxyUploadAdapter):
    adapter_id = "tests.proxy-stream"
    adapter_version = 1

    def __init__(self) -> None:
        super().__init__(_STORAGE)
        self.chunks: list[bytes] = []
        self.deleted: list[str] = []

    def save_stage(
        self,
        stage_key: str,
        chunks: Iterable[bytes],
        *,
        content_type: str | None,
        checksum_sha256: str | None = None,
        size: int | None = None,
    ) -> ObjectVersion:
        del stage_key
        digest = hashlib.sha256()
        count = 0
        for chunk in chunks:
            self.chunks.append(chunk)
            digest.update(chunk)
            count += len(chunk)
        return ObjectVersion(
            version_id=None,
            etag=None,
            checksum_sha256=digest.hexdigest(),
            size=count,
            content_type=content_type,
        )

    def delete_stage(
        self,
        stage_key: str,
        version: ObjectVersion | None = None,
    ) -> None:
        del version
        self.deleted.append(stage_key)

    def storage_fingerprint(self) -> str:
        return "sha256:test-proxy-stream"


class FailingProxyAdapter(RecordingProxyAdapter):
    def save_stage(self, *args: object, **kwargs: object) -> ObjectVersion:
        del args, kwargs
        raise RuntimeError("storage-password-must-not-escape")

    def delete_stage(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("cleanup-password-must-not-escape")


class MalformedVersionAdapter(RecordingProxyAdapter):
    def save_stage(self, *args: object, **kwargs: object) -> ObjectVersion:
        del args, kwargs
        return object()  # type: ignore[return-value]


class BlockingDeleteAdapter(RecordingProxyAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.delete_started = Event()
        self.allow_delete = Event()

    def delete_stage(
        self,
        stage_key: str,
        version: ObjectVersion | None = None,
    ) -> None:
        self.delete_started.set()
        if not self.allow_delete.wait(timeout=5):
            raise _DeleteTimeoutError
        super().delete_stage(stage_key, version)


class _DeleteTimeoutError(RuntimeError):
    """Keep a blocked cleanup test from hanging its executor."""


@dataclass(frozen=True)
class PendingUpload:
    intent: UploadIntent
    authorization: str
    consumption_token: str


@pytest.fixture(autouse=True)
def upload_manager_registry() -> Iterable[None]:
    previous = GraphQL.manager_registry
    GraphQL.manager_registry = {ProxyUploadManager.__name__: ProxyUploadManager}
    try:
        yield
    finally:
        GraphQL.manager_registry = previous


@pytest.fixture
def owner() -> Any:
    return get_user_model().objects.create_user(username=f"owner-{uuid4().hex}")


@pytest.fixture
def other_user() -> Any:
    return get_user_model().objects.create_user(username=f"other-{uuid4().hex}")


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> RecordingProxyAdapter:
    from general_manager.uploads import views

    value = RecordingProxyAdapter()
    registry = UploadAdapterRegistry()
    registry.register(FileSystemStorage, lambda _storage: value)
    monkeypatch.setattr(views, "upload_adapter_registry", registry)
    return value


@pytest.fixture
def pending_upload(
    owner: object,
    adapter: RecordingProxyAdapter,
) -> Callable[..., PendingUpload]:
    def create(**overrides: object) -> PendingUpload:
        body = overrides.pop("body", b"abc")
        assert isinstance(body, bytes)
        digest = hashlib.sha256(body).hexdigest()
        values = {
            "user": owner,
            "token_digest": hashlib.sha256(b"consumption-token").hexdigest(),
            "manager_name": ProxyUploadManager.__name__,
            "field_name": "avatar",
            "operation": "create",
            "adapter_id": adapter.adapter_id,
            "adapter_version": str(adapter.adapter_version),
            "storage_fingerprint": adapter.storage_fingerprint(),
            "staging_key": f"gm-staging/{uuid4().hex}/{uuid4().hex}",
            "original_filename": "avatar.png",
            "declared_size": len(body),
            "declared_content_type": "image/png",
            "declared_checksum_sha256": digest,
            "expires_at": timezone.now() + timedelta(minutes=5),
        }
        values.update(overrides)
        intent = UploadIntent.objects.create(**values)
        credential = issue_upload_transfer_credential(
            intent_id=intent.id,
            owner_pk=owner.pk,
            adapter_id=intent.adapter_id,
        )
        return PendingUpload(
            intent=intent,
            authorization=f"GMUpload {credential}",
            consumption_token="consumption-token",  # noqa: S106
        )

    return create


def _request(
    *,
    user: object,
    body: bytes = b"abc",
    authorization: str | None,
    content_type: str = "image/png",
    method: str = "PUT",
) -> object:
    factory = RequestFactory()
    request_method = getattr(factory, method.lower())
    extra = {"HTTP_AUTHORIZATION": authorization} if authorization is not None else {}
    request = request_method(
        "/gm/uploads/opaque",
        data=body,
        content_type=content_type,
        **extra,
    )
    request.user = user
    return request


def _payload(response: HttpResponse) -> dict[str, object]:
    return json.loads(response.content)


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_proxy_put_streams_and_records_exact_verified_metadata(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    adapter: RecordingProxyAdapter,
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload()
    request = _request(user=owner, authorization=upload.authorization)

    response = proxy_upload_view(request, upload.intent.id)

    upload.intent.refresh_from_db()
    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["Cache-Control"] == "no-store"
    assert upload.intent.state == UploadIntentState.UPLOADED
    assert upload.intent.transfer_lease_expires_at is None
    assert upload.intent.transfer_attempt_count == 1
    assert upload.intent.staging_key.endswith(".proxy-attempt-1")
    assert upload.intent.verified_size == 3
    assert upload.intent.verified_content_type == "image/png"
    assert upload.intent.verified_checksum_sha256 == hashlib.sha256(b"abc").hexdigest()
    assert upload.intent.object_version == {
        "version_id": None,
        "etag": None,
        "checksum_sha256": hashlib.sha256(b"abc").hexdigest(),
        "size": 3,
        "content_type": "image/png",
    }
    assert b"".join(adapter.chunks) == b"abc"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
def test_proxy_endpoint_accepts_only_put(method: str) -> None:
    from general_manager.uploads.views import proxy_upload_view

    request = _request(
        user=AnonymousUser(),
        authorization=None,
        method=method,
        body=b"",
    )

    response = proxy_upload_view(request, uuid4())

    assert response.status_code == 405
    assert response.headers["Allow"] == "PUT"
    assert response.headers["Cache-Control"] == "no-store"
    assert _payload(response)["error"]["code"] == "METHOD_NOT_ALLOWED"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_proxy_endpoint_requires_session_authentication(
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload()
    response = proxy_upload_view(
        _request(user=AnonymousUser(), authorization=upload.authorization),
        upload.intent.id,
    )

    assert response.status_code == 401
    assert _payload(response)["error"]["code"] == "UNAUTHENTICATED"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_wrong_owner_and_missing_intent_share_redacted_not_found(
    other_user: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload()
    wrong_owner = proxy_upload_view(
        _request(user=other_user, authorization=upload.authorization),
        upload.intent.id,
    )
    missing = proxy_upload_view(
        _request(user=other_user, authorization=upload.authorization),
        uuid4(),
    )

    assert wrong_owner.status_code == missing.status_code == 404
    assert _payload(wrong_owner) == _payload(missing)
    assert _payload(wrong_owner)["error"]["code"] == "UPLOAD_NOT_FOUND"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
@pytest.mark.parametrize(
    "authorization",
    [None, "", "Bearer credential", "GMUpload", "GMUpload consumption-token"],
)
def test_transfer_rejects_missing_wrong_scheme_and_graphql_consumption_token(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    authorization: str | None,
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload()
    response = proxy_upload_view(
        _request(user=owner, authorization=authorization),
        upload.intent.id,
    )

    assert response.status_code == 401
    assert _payload(response)["error"]["code"] == "UPLOAD_CREDENTIAL_INVALID"
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.PENDING


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_transfer_rejects_ambiguous_combined_authorization_values(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload()
    response = proxy_upload_view(
        _request(
            user=owner,
            authorization=f"{upload.authorization}, {upload.authorization}",
        ),
        upload.intent.id,
    )

    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert _payload(response)["error"]["code"] == "UPLOAD_CREDENTIAL_INVALID"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_transfer_credential_is_bound_to_intent_owner_and_adapter(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload()
    other = pending_upload()

    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        other.intent.id,
    )

    assert response.status_code == 401
    assert _payload(response)["error"]["code"] == "UPLOAD_CREDENTIAL_INVALID"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_expired_intent_becomes_terminal_without_touching_storage(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    adapter: RecordingProxyAdapter,
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload(expires_at=timezone.now() - timedelta(seconds=1))
    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    upload.intent.refresh_from_db()
    assert response.status_code == 410
    assert _payload(response)["error"]["code"] == "UPLOAD_EXPIRED"
    assert upload.intent.state == UploadIntentState.EXPIRED
    assert adapter.chunks == []


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_active_transfer_and_completed_replay_return_conflict(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    active = pending_upload(
        state=UploadIntentState.TRANSFERRING,
        transfer_lease_expires_at=timezone.now() + timedelta(seconds=20),
    )
    completed = pending_upload(state=UploadIntentState.UPLOADED)

    active_response = proxy_upload_view(
        _request(user=owner, authorization=active.authorization),
        active.intent.id,
    )
    completed_response = proxy_upload_view(
        _request(user=owner, authorization=completed.authorization),
        completed.intent.id,
    )

    assert active_response.status_code == completed_response.status_code == 409
    assert _payload(active_response)["error"]["code"] == "UPLOAD_TRANSFER_CONFLICT"
    assert _payload(completed_response)["error"]["code"] == "UPLOAD_TRANSFER_CONFLICT"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_active_and_replayed_transfers_conflict_before_request_metadata_checks(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    active = pending_upload(
        state=UploadIntentState.TRANSFERRING,
        transfer_lease_expires_at=timezone.now() + timedelta(seconds=20),
        storage_fingerprint="sha256:changed-storage",
    )
    replay = pending_upload(state=UploadIntentState.UPLOADED)

    active_response = proxy_upload_view(
        _request(
            user=owner,
            authorization=active.authorization,
            content_type="text/plain",
        ),
        active.intent.id,
    )
    replay_response = proxy_upload_view(
        _request(
            user=owner,
            body=b"abcd",
            authorization=replay.authorization,
        ),
        replay.intent.id,
    )

    assert active_response.status_code == replay_response.status_code == 409
    assert _payload(active_response)["error"]["code"] == "UPLOAD_TRANSFER_CONFLICT"
    assert _payload(replay_response)["error"]["code"] == "UPLOAD_TRANSFER_CONFLICT"


@override_settings(
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "MAX_TRANSFER_ATTEMPTS_PER_USER": 1,
            "MAX_TRANSFER_ATTEMPTS_GLOBAL": 10,
        }
    }
)
def test_conflicts_and_replays_are_counted_by_transfer_rate_limit(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    cache.clear()
    upload = pending_upload(
        state=UploadIntentState.TRANSFERRING,
        transfer_lease_expires_at=timezone.now() + timedelta(seconds=20),
    )
    first = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )
    second = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    assert first.status_code == 409
    assert _payload(first)["error"]["code"] == "UPLOAD_TRANSFER_CONFLICT"
    assert second.status_code == 429
    assert _payload(second)["error"]["code"] == "UPLOAD_RATE_LIMITED"


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
@pytest.mark.parametrize("attempt", range(5))
def test_concurrent_claim_has_one_winner_and_one_stable_conflict(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    attempt: int,
) -> None:
    from general_manager.uploads import views

    del attempt
    upload = pending_upload()
    barrier = Barrier(2)

    def claim() -> str:
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            try:
                views._claim_transfer(upload.intent.id, owner.pk)
            except views._TransferFailure as failure:
                return failure.error.code
            return "claimed"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _index: claim(), range(2)))

    assert sorted(outcomes) == ["UPLOAD_TRANSFER_CONFLICT", "claimed"]


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_expired_transfer_lease_can_be_reclaimed(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload(
        state=UploadIntentState.TRANSFERRING,
        transfer_lease_expires_at=timezone.now() - timedelta(seconds=1),
    )

    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    upload.intent.refresh_from_db()
    assert response.status_code == 204
    assert upload.intent.state == UploadIntentState.UPLOADED


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_oversized_stream_stops_before_forwarding_the_oversized_chunk(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    adapter: RecordingProxyAdapter,
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload(body=b"abc")
    request = _request(
        user=owner,
        body=b"abcd",
        authorization=upload.authorization,
    )

    response = proxy_upload_view(request, upload.intent.id)

    assert response.status_code == 413
    assert _payload(response)["error"]["code"] == "UPLOAD_SIZE_MISMATCH"
    assert adapter.chunks == []
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.PENDING
    assert upload.intent.transfer_lease_expires_at is None


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_oversized_chunked_stream_is_bounded_without_content_length(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    adapter: RecordingProxyAdapter,
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload(body=b"abc")
    request = _request(
        user=owner,
        body=b"abcd",
        authorization=upload.authorization,
    )
    request.META.pop("CONTENT_LENGTH", None)

    response = proxy_upload_view(request, upload.intent.id)

    assert response.status_code == 413
    assert _payload(response)["error"]["code"] == "UPLOAD_SIZE_MISMATCH"
    assert adapter.chunks == []
    assert adapter.deleted == [f"{upload.intent.staging_key}.proxy-attempt-1"]
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.PENDING


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_short_body_is_retryable_size_mismatch(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload(body=b"abcd")
    response = proxy_upload_view(
        _request(user=owner, body=b"abc", authorization=upload.authorization),
        upload.intent.id,
    )

    assert response.status_code == 422
    assert _payload(response)["error"]["code"] == "UPLOAD_SIZE_MISMATCH"
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.PENDING


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_wrong_content_type_is_rejected_before_storage(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    adapter: RecordingProxyAdapter,
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload()
    response = proxy_upload_view(
        _request(
            user=owner,
            authorization=upload.authorization,
            content_type="text/plain",
        ),
        upload.intent.id,
    )

    assert response.status_code == 415
    assert _payload(response)["error"]["code"] == "INVALID_FILE_TYPE"
    assert adapter.chunks == []


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_wrong_checksum_is_retryable_and_cleans_owned_stage(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    adapter: RecordingProxyAdapter,
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload(declared_checksum_sha256="0" * 64)
    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    assert response.status_code == 422
    assert _payload(response)["error"]["code"] == "UPLOAD_CHECKSUM_MISMATCH"
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.PENDING
    assert adapter.deleted == [f"{upload.intent.staging_key}.proxy-attempt-1"]


@pytest.mark.django_db(transaction=True)
@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_cleanup_and_reclamation_use_attempt_isolated_stage_keys(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_manager.uploads import views

    blocking = BlockingDeleteAdapter()
    registry = UploadAdapterRegistry()
    registry.register(FileSystemStorage, lambda _storage: blocking)
    monkeypatch.setattr(views, "upload_adapter_registry", registry)
    upload = pending_upload(
        adapter_id=blocking.adapter_id,
        adapter_version=str(blocking.adapter_version),
        storage_fingerprint=blocking.storage_fingerprint(),
    )
    claim = views._claim_transfer(upload.intent.id, owner.pk)
    original_stage_key = claim.stage_key

    with ThreadPoolExecutor(max_workers=1) as executor:
        cleanup = executor.submit(
            views._reset_failed_transfer,
            claim,
            adapter=blocking,
            intent=upload.intent,
        )
        assert blocking.delete_started.wait(timeout=5)
        try:
            UploadIntent.objects.filter(pk=upload.intent.id).update(
                transfer_lease_expires_at=timezone.now() - timedelta(seconds=1)
            )
            current = views._claim_transfer(upload.intent.id, owner.pk)
            upload.intent.refresh_from_db()
            assert upload.intent.state == UploadIntentState.TRANSFERRING
            assert current.stage_key != original_stage_key
            assert upload.intent.staging_key == current.base_stage_key
            assert upload.intent.transfer_attempt_count == 2
        finally:
            blocking.allow_delete.set()
        cleanup.result(timeout=5)

    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.TRANSFERRING
    assert upload.intent.staging_key == current.base_stage_key
    assert upload.intent.transfer_attempt_count == 2
    assert blocking.deleted == [original_stage_key]


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_stale_lease_holder_cannot_complete_or_delete_current_after_reclamation(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    adapter: RecordingProxyAdapter,
) -> None:
    from general_manager.uploads import views

    upload = pending_upload()
    stale = views._claim_transfer(upload.intent.id, owner.pk)
    UploadIntent.objects.filter(pk=upload.intent.id).update(
        transfer_lease_expires_at=timezone.now() - timedelta(seconds=1)
    )
    current = views._claim_transfer(upload.intent.id, owner.pk)
    version = ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256=upload.intent.declared_checksum_sha256,
        size=upload.intent.declared_size,
        content_type=upload.intent.declared_content_type,
    )

    with pytest.raises(views._TransferFailure) as conflict:
        views._complete_transfer(stale, version=version, uploaded_at=timezone.now())
    views._reset_failed_transfer(stale, adapter=adapter, intent=upload.intent)
    views._complete_transfer(current, version=version, uploaded_at=timezone.now())

    assert conflict.value.error.code == "UPLOAD_TRANSFER_CONFLICT"
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.UPLOADED
    assert upload.intent.staging_key == current.stage_key
    assert upload.intent.transfer_attempt_count == 2
    assert adapter.deleted == [stale.stage_key]


def test_attempt_keys_remain_durably_enumerable_after_success(
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads import views

    upload = pending_upload()
    base = upload.intent.staging_key
    upload.intent.transfer_attempt_count = 2
    assert tuple(views.iter_proxy_attempt_stage_keys(upload.intent)) == (
        f"{base}.proxy-attempt-1",
        f"{base}.proxy-attempt-2",
    )

    upload.intent.staging_key = f"{base}.proxy-attempt-2"

    assert tuple(views.iter_proxy_attempt_stage_keys(upload.intent)) == (
        f"{base}.proxy-attempt-1",
        f"{base}.proxy-attempt-2",
    )

    upload.intent.staging_key = f"{base}.proxy-attempt-1"
    with pytest.raises(views._TransferFailure) as malformed:
        tuple(views.iter_proxy_attempt_stage_keys(upload.intent))
    assert malformed.value.error.code == "UPLOAD_STORAGE_ERROR"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_storage_and_cleanup_failures_are_sanitized_and_retryable(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_manager.uploads import views
    from general_manager.uploads.views import proxy_upload_view

    failing = FailingProxyAdapter()
    registry = UploadAdapterRegistry()
    registry.register(FileSystemStorage, lambda _storage: failing)
    monkeypatch.setattr(views, "upload_adapter_registry", registry)
    upload = pending_upload(
        adapter_id=failing.adapter_id,
        adapter_version=str(failing.adapter_version),
        storage_fingerprint=failing.storage_fingerprint(),
    )
    original_stage_key = upload.intent.staging_key

    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    body = response.content.decode()
    assert response.status_code == 503
    assert _payload(response)["error"]["code"] == "UPLOAD_STORAGE_ERROR"
    assert "password" not in body
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.PENDING
    assert upload.intent.transfer_lease_expires_at is None
    assert upload.intent.staging_key == original_stage_key
    assert upload.intent.transfer_attempt_count == 1


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_hostile_adapter_metadata_is_rejected_without_persisting_it(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_manager.uploads import views
    from general_manager.uploads.views import proxy_upload_view

    malformed = MalformedVersionAdapter()
    registry = UploadAdapterRegistry()
    registry.register(FileSystemStorage, lambda _storage: malformed)
    monkeypatch.setattr(views, "upload_adapter_registry", registry)
    upload = pending_upload(
        adapter_id=malformed.adapter_id,
        adapter_version=str(malformed.adapter_version),
        storage_fingerprint=malformed.storage_fingerprint(),
    )

    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    assert response.status_code == 503
    assert _payload(response)["error"]["code"] == "UPLOAD_STORAGE_ERROR"
    upload.intent.refresh_from_db()
    assert upload.intent.object_version == {}
    assert upload.intent.state == UploadIntentState.PENDING


def test_forged_boolean_adapter_size_is_rejected_as_storage_error() -> None:
    from general_manager.uploads import views

    forged = object.__new__(ObjectVersion)
    object.__setattr__(forged, "version_id", None)
    object.__setattr__(forged, "etag", None)
    object.__setattr__(forged, "checksum_sha256", hashlib.sha256(b"a").hexdigest())
    object.__setattr__(forged, "size", True)
    object.__setattr__(forged, "content_type", "image/png")

    with pytest.raises(views._TransferFailure) as failure:
        views._validate_object_version(
            forged,
            expected_size=1,
            expected_content_type="image/png",
            expected_checksum=hashlib.sha256(b"a").hexdigest(),
        )

    assert failure.value.error.code == "UPLOAD_STORAGE_ERROR"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_adapter_identity_or_storage_change_fails_closed(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload(storage_fingerprint="sha256:old-storage")
    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    assert response.status_code == 503
    assert _payload(response)["error"]["code"] == "UPLOAD_STORAGE_CHANGED"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_transfer_credential_uses_configured_finite_ttl(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    with patch("django.core.signing.time.time", return_value=100):
        upload = pending_upload()
    with patch("django.core.signing.time.time", return_value=221):
        response = proxy_upload_view(
            _request(user=owner, authorization=upload.authorization),
            upload.intent.id,
        )

    assert response.status_code == 401
    assert _payload(response)["error"]["code"] == "UPLOAD_CREDENTIAL_INVALID"


@override_settings(
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "MAX_TRANSFER_ATTEMPTS_PER_USER": 1,
            "MAX_TRANSFER_ATTEMPTS_GLOBAL": 10,
        }
    }
)
def test_transfer_rate_limit_is_finite_and_stable(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    cache.clear()
    first = pending_upload()
    second = pending_upload()
    assert (
        proxy_upload_view(
            _request(user=owner, authorization=first.authorization),
            first.intent.id,
        ).status_code
        == 204
    )

    response = proxy_upload_view(
        _request(user=owner, authorization=second.authorization),
        second.intent.id,
    )

    assert response.status_code == 429
    assert _payload(response)["error"]["code"] == "UPLOAD_RATE_LIMITED"
    second.intent.refresh_from_db()
    assert second.intent.state == UploadIntentState.PENDING


@override_settings(
    GENERAL_MANAGER={
        "FILE_UPLOADS": {
            "ENABLED": True,
            "MAX_TRANSFER_ATTEMPTS_PER_INTENT": 1,
        }
    }
)
def test_per_intent_attempt_cap_bounds_abandoned_objects(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    cache.clear()
    upload = pending_upload(transfer_attempt_count=1)
    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    assert response.status_code == 429
    assert _payload(response)["error"]["code"] == "UPLOAD_RATE_LIMITED"
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.PENDING
    assert upload.intent.transfer_attempt_count == 1


@override_settings(
    GENERAL_MANAGER=_ENABLED_UPLOADS,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": _RATE_CACHE_LOCATION,
        }
    },
)
def test_transfer_rate_limit_fails_closed_for_non_atomic_cache_backend(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload()
    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    assert response.status_code == 503
    assert _payload(response)["error"]["code"] == "UPLOAD_STORAGE_ERROR"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_concurrent_locmem_rate_limit_admits_exact_configured_count() -> None:
    from general_manager.uploads import views

    cache.clear()
    settings = FileUploadSettings(
        enabled=True,
        max_transfer_attempts_per_user=5,
        max_transfer_attempts_global=100,
    )

    def attempt() -> str:
        try:
            views._enforce_transfer_rate_limit(42, settings)
        except views._TransferFailure as failure:
            return failure.error.code
        return "allowed"

    with ThreadPoolExecutor(max_workers=10) as executor:
        outcomes = list(executor.map(lambda _index: attempt(), range(10)))

    assert outcomes.count("allowed") == 5
    assert outcomes.count("UPLOAD_RATE_LIMITED") == 5


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_user_rate_rejections_do_not_consume_global_transfer_budget() -> None:
    from general_manager.uploads import views

    cache.clear()
    settings = FileUploadSettings(
        enabled=True,
        max_transfer_attempts_per_user=1,
        max_transfer_attempts_global=3,
    )
    views._enforce_transfer_rate_limit(1, settings)
    for _attempt in range(5):
        with pytest.raises(views._TransferFailure) as limited:
            views._enforce_transfer_rate_limit(1, settings)
        assert limited.value.error.code == "UPLOAD_RATE_LIMITED"

    views._enforce_transfer_rate_limit(2, settings)
    views._enforce_transfer_rate_limit(3, settings)


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_lease_is_bounded_by_intent_expiry_and_renewed_with_compare_and_swap(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads import views

    bounded = pending_upload(expires_at=timezone.now() + timedelta(seconds=10))
    bounded_claim = views._claim_transfer(bounded.intent.id, owner.pk)
    assert bounded_claim.lease_expires_at <= bounded.intent.expires_at

    renewable = pending_upload(expires_at=timezone.now() + timedelta(minutes=2))
    claimed = views._claim_transfer(renewable.intent.id, owner.pk)
    one_second_later = timezone.now() + timedelta(seconds=1)
    with patch("general_manager.uploads.views.timezone.now") as now:
        now.return_value = one_second_later
        renewed = views._renew_transfer_lease(claimed)
    assert renewed is not None
    assert renewed.lease_expires_at <= renewable.intent.expires_at
    assert views._renew_transfer_lease(claimed) is None


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_rate_counter_recovers_from_eviction_and_sanitizes_cache_failures() -> None:
    from general_manager.uploads import views

    with (
        patch.object(views.cache, "add", side_effect=[False, True]),
        patch.object(views.cache, "incr", side_effect=ValueError("evicted")),
    ):
        assert views._increment_rate_counter("key", window_seconds=60) == 1

    with patch.object(views.cache, "add", side_effect=RuntimeError("cache-password")):
        with pytest.raises(views._TransferFailure) as failure:
            views._increment_rate_counter("key", window_seconds=60)

    assert failure.value.error.code == "UPLOAD_STORAGE_ERROR"
    assert "cache-password" not in failure.value.error.message


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_rate_limit_sanitizes_backend_lookup_and_enforces_global_budget() -> None:
    from general_manager.uploads import views

    class BrokenCaches:
        def __getitem__(self, _alias: str) -> object:
            raise RuntimeError("backend-password")

    with patch.object(views, "caches", BrokenCaches()):
        with pytest.raises(views._TransferFailure) as unavailable:
            views._enforce_transfer_rate_limit(1, FileUploadSettings(enabled=True))
    assert unavailable.value.error.code == "UPLOAD_STORAGE_ERROR"
    assert "backend-password" not in unavailable.value.error.message

    settings = FileUploadSettings(
        enabled=True,
        max_transfer_attempts_per_user=10,
        max_transfer_attempts_global=1,
    )
    with patch.object(views, "_increment_rate_counter", side_effect=[1, 2]):
        with pytest.raises(views._TransferFailure) as limited:
            views._enforce_transfer_rate_limit(1, settings)
    assert limited.value.error.code == "UPLOAD_RATE_LIMITED"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_intent_query_failures_map_to_stable_storage_errors(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads import views

    upload = pending_upload()
    with patch.object(
        views.UploadIntent.objects, "using", side_effect=RuntimeError("db-password")
    ):
        with pytest.raises(views._TransferFailure) as lookup:
            views._owned_intent(upload.intent.id, owner.pk)
    with patch.object(
        views.UploadIntent.objects,
        "using",
        side_effect=RuntimeError("expiry-password"),
    ):
        with pytest.raises(views._TransferFailure) as expiry:
            views._mark_expired(upload.intent, owner.pk, at=timezone.now())

    assert lookup.value.error.code == "UPLOAD_STORAGE_ERROR"
    assert expiry.value.error.code == "UPLOAD_STORAGE_ERROR"
    assert "db-password" not in lookup.value.error.message
    assert "expiry-password" not in expiry.value.error.message


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_transfer_lifecycle_database_failures_are_fail_closed_or_bounded(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads import views

    upload = pending_upload()
    claim = views.TransferClaim(
        intent_id=upload.intent.id,
        owner_pk=owner.pk,
        lease_expires_at=timezone.now(),
        intent_expires_at=timezone.now() + timedelta(minutes=1),
        base_stage_key=upload.intent.staging_key,
        stage_key=f"{upload.intent.staging_key}.proxy-attempt-1",
        attempt_number=1,
    )
    version = ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256=hashlib.sha256(b"abc").hexdigest(),
        size=3,
        content_type="image/png",
    )

    with patch.object(
        views.UploadIntent.objects, "using", side_effect=RuntimeError("db-password")
    ):
        with pytest.raises(views._TransferFailure) as renewal:
            views._renew_transfer_lease(claim)
        with pytest.raises(views._TransferFailure) as completion:
            views._complete_transfer(claim, version=version, uploaded_at=timezone.now())
        views._release_unstarted_transfer(claim)

    assert renewal.value.error.code == "UPLOAD_STORAGE_ERROR"
    assert completion.value.error.code == "UPLOAD_STORAGE_ERROR"
    assert "db-password" not in renewal.value.error.message
    assert "db-password" not in completion.value.error.message


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_claim_transfer_retries_sqlite_busy_with_bounded_backoff() -> None:
    from general_manager.uploads import views

    recovered = object()
    with (
        patch.object(
            views,
            "_claim_transfer_once",
            side_effect=[OperationalError("database is locked"), recovered],
        ),
        patch.object(views, "_is_sqlite_busy_error", return_value=True),
        patch.object(views.time, "monotonic", side_effect=[10.0, 10.1]),
        patch.object(views.secrets, "randbelow", return_value=0),
        patch.object(views.time, "sleep") as sleep,
    ):
        result = views._claim_transfer(uuid4(), 1)

    assert result is recovered
    sleep.assert_called_once()
    delay = sleep.call_args.args[0]
    assert 0 < delay <= views._SQLITE_BUSY_MAX_DELAY_SECONDS


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_claim_transfer_stops_on_non_busy_or_expired_retry_budget() -> None:
    from general_manager.uploads import views

    error = OperationalError("disk I/O failure")
    with (
        patch.object(views, "_claim_transfer_once", side_effect=error),
        patch.object(views, "_is_sqlite_busy_error", return_value=False),
    ):
        with pytest.raises(views._TransferFailure) as non_busy:
            views._claim_transfer(uuid4(), 1)
    assert non_busy.value.error.code == "UPLOAD_STORAGE_ERROR"

    with (
        patch.object(views, "_claim_transfer_once", side_effect=error),
        patch.object(views, "_is_sqlite_busy_error", return_value=True),
        patch.object(views.time, "monotonic", side_effect=[10.0, 11.0]),
    ):
        with pytest.raises(views._TransferFailure) as exhausted:
            views._claim_transfer(uuid4(), 1)
    assert exhausted.value.error.code == "UPLOAD_STORAGE_ERROR"


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_claim_once_redacts_missing_and_database_failure(owner: object) -> None:
    from general_manager.uploads import views

    settings = FileUploadSettings(enabled=True)
    with pytest.raises(views._TransferFailure) as missing:
        views._claim_transfer_once(uuid4(), owner.pk, settings=settings)
    assert missing.value.error.code == "UPLOAD_NOT_FOUND"

    with patch.object(
        views.UploadIntent.objects, "using", side_effect=RuntimeError("db-password")
    ):
        with pytest.raises(views._TransferFailure) as unavailable:
            views._claim_transfer_once(uuid4(), owner.pk, settings=settings)
    assert unavailable.value.error.code == "UPLOAD_STORAGE_ERROR"
    assert "db-password" not in unavailable.value.error.message


def _claim_for_stream() -> object:
    from general_manager.uploads import views

    now = timezone.now()
    return views.TransferClaim(
        intent_id=uuid4(),
        owner_pk=1,
        lease_expires_at=now + timedelta(seconds=10),
        intent_expires_at=now + timedelta(minutes=1),
        base_stage_key="gm-staging/base",
        stage_key="gm-staging/base.proxy-attempt-1",
        attempt_number=1,
    )


@pytest.mark.parametrize("attempt_count", [True, -1, "1"])
def test_attempt_key_iteration_rejects_invalid_persisted_counts(
    attempt_count: object,
) -> None:
    from general_manager.uploads import views

    intent = SimpleNamespace(
        transfer_attempt_count=attempt_count,
        staging_key="gm-staging/base",
    )
    with pytest.raises(views._TransferFailure) as failure:
        tuple(views.iter_proxy_attempt_stage_keys(intent))
    assert failure.value.error.code == "UPLOAD_STORAGE_ERROR"


def test_attempt_stage_key_rejects_empty_or_oversized_names() -> None:
    from general_manager.uploads import views

    for base in ("", "x" * 1024):
        with pytest.raises(views._TransferFailure) as failure:
            views._attempt_staging_key(base, 1)
        assert failure.value.error.code == "UPLOAD_STORAGE_ERROR"


@pytest.mark.parametrize("raw", [True, object(), "not-a-number", "-1"])
def test_content_length_parser_rejects_ambiguous_or_invalid_values(raw: object) -> None:
    from general_manager.uploads import views

    request = SimpleNamespace(META={"CONTENT_LENGTH": raw})
    with pytest.raises(views._TransferFailure) as failure:
        views._parse_content_length(request)
    assert failure.value.error.code == "UPLOAD_SIZE_MISMATCH"


def test_bounded_stream_sanitizes_read_type_and_lease_failures() -> None:
    from general_manager.uploads import views

    class BrokenRead:
        def read(self, _size: int) -> bytes:
            raise RuntimeError("request-secret")

    class TextRead:
        def read(self, _size: int) -> object:
            return "not-bytes"

    for request in (BrokenRead(), TextRead()):
        chunks = views._BoundedRequestChunks(
            request,
            _claim_for_stream(),
            expected_size=1,
            maximum_size=1,
        )
        with pytest.raises(views._TransferFailure) as failure:
            tuple(chunks)
        assert failure.value.error.code == "UPLOAD_STORAGE_ERROR"

    request = SimpleNamespace(read=lambda _size: b"a")
    chunks = views._BoundedRequestChunks(
        request,
        _claim_for_stream(),
        expected_size=1,
        maximum_size=1,
    )
    with patch.object(views, "_renew_transfer_lease", return_value=None):
        with pytest.raises(views._TransferFailure) as conflict:
            next(iter(chunks))
    assert conflict.value.error.code == "UPLOAD_TRANSFER_CONFLICT"


def test_object_version_validation_maps_each_untrusted_mismatch() -> None:
    from general_manager.uploads import views

    checksum = hashlib.sha256(b"a").hexdigest()
    cases = (
        (object(), "UPLOAD_STORAGE_ERROR", checksum),
        (
            ObjectVersion(None, None, checksum, 2, "image/png"),
            "UPLOAD_SIZE_MISMATCH",
            checksum,
        ),
        (
            ObjectVersion(None, None, "b" * 64, 1, "image/png"),
            "UPLOAD_CHECKSUM_MISMATCH",
            checksum,
        ),
        (
            ObjectVersion(None, None, checksum, 1, "text/plain"),
            "INVALID_FILE_TYPE",
            checksum,
        ),
        (
            ObjectVersion("", None, checksum, 1, "image/png"),
            "UPLOAD_STORAGE_ERROR",
            checksum,
        ),
        (
            ObjectVersion(None, None, "invalid", 1, "image/png"),
            "UPLOAD_STORAGE_ERROR",
            "invalid",
        ),
    )
    for value, code, expected_checksum in cases:
        with pytest.raises(views._TransferFailure) as failure:
            views._validate_object_version(
                value,
                expected_size=1,
                expected_content_type="image/png",
                expected_checksum=expected_checksum,
            )
        assert failure.value.error.code == code


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_adapter_resolution_rejects_malformed_identity_and_fingerprint_failure(
    pending_upload: Callable[..., PendingUpload],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_manager.uploads import views

    upload = pending_upload()
    upload.intent.adapter_version = 1  # type: ignore[assignment]
    with pytest.raises(views._TransferFailure) as malformed:
        views._resolve_adapter(upload.intent)
    assert malformed.value.error.code == "UPLOAD_STORAGE_CHANGED"

    class BrokenFingerprintAdapter(RecordingProxyAdapter):
        def storage_fingerprint(self) -> str:
            raise RuntimeError("storage-password")

    upload.intent.adapter_version = "1"
    monkeypatch.setattr(
        views.upload_adapter_registry,
        "resolve_by_id",
        lambda *_args, **_kwargs: BrokenFingerprintAdapter(),
    )
    with pytest.raises(views._TransferFailure) as fingerprint:
        views._resolve_adapter(upload.intent)
    assert fingerprint.value.error.code == "UPLOAD_STORAGE_CHANGED"
    assert "storage-password" not in fingerprint.value.error.message


@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": False}})
def test_transfer_and_download_endpoints_are_hidden_when_disabled() -> None:
    from general_manager.uploads.views import private_download_view, proxy_upload_view

    upload = proxy_upload_view(
        _request(user=AnonymousUser(), authorization=None),
        uuid4(),
    )
    download = private_download_view(RequestFactory().get("/private/file"), "invalid")

    assert upload.status_code == 404
    assert _payload(upload)["error"]["code"] == "UPLOAD_NOT_FOUND"
    assert download.status_code == 404
    assert _payload(download)["error"]["code"] == "FILE_NOT_FOUND"


def test_private_download_endpoint_rejects_unsupported_methods() -> None:
    from general_manager.uploads.views import private_download_view

    response = private_download_view(RequestFactory().post("/private/file"), "invalid")

    assert response.status_code == 405
    assert response.headers["Allow"] == "GET, HEAD"
    assert _payload(response)["error"]["code"] == "METHOD_NOT_ALLOWED"


@override_settings(GENERAL_MANAGER={"FILE_UPLOADS": {"ENABLED": True, "MAX_BYTES": 2}})
def test_transfer_rejects_declared_size_above_global_limit(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads.views import proxy_upload_view

    upload = pending_upload(body=b"abc")
    response = proxy_upload_view(
        _request(user=owner, authorization=upload.authorization),
        upload.intent.id,
    )

    assert response.status_code == 413
    assert _payload(response)["error"]["code"] == "UPLOAD_SIZE_MISMATCH"
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.PENDING


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_adapter_resolution_exception_releases_claim_and_is_sanitized(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads import views

    upload = pending_upload()
    with patch.object(views, "_resolve_adapter", side_effect=RuntimeError("secret")):
        response = views.proxy_upload_view(
            _request(user=owner, authorization=upload.authorization),
            upload.intent.id,
        )

    assert response.status_code == 503
    assert _payload(response)["error"]["code"] == "UPLOAD_STORAGE_ERROR"
    assert "secret" not in response.content.decode()
    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.PENDING


def test_proxy_boundary_sanitizes_unexpected_failures() -> None:
    from general_manager.uploads import views

    with patch.object(views, "_transfer", side_effect=RuntimeError("secret")):
        response = views.proxy_upload_view(
            _request(user=AnonymousUser(), authorization=None),
            uuid4(),
        )

    assert response.status_code == 503
    assert _payload(response)["error"]["code"] == "UPLOAD_STORAGE_ERROR"
    assert "secret" not in response.content.decode()


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_observability_failure_does_not_undo_completed_transfer(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads import views

    upload = pending_upload()
    claim = views._claim_transfer(upload.intent.id, owner.pk)
    version = ObjectVersion(
        version_id=None,
        etag=None,
        checksum_sha256=upload.intent.declared_checksum_sha256,
        size=upload.intent.declared_size,
        content_type=upload.intent.declared_content_type,
    )
    with patch.object(
        views, "record_upload_transition", side_effect=RuntimeError("metrics-secret")
    ):
        views._complete_transfer(claim, version=version, uploaded_at=timezone.now())

    upload.intent.refresh_from_db()
    assert upload.intent.state == UploadIntentState.UPLOADED


@override_settings(GENERAL_MANAGER=_ENABLED_UPLOADS)
def test_optional_download_metadata_and_hostile_basename_fallbacks(
    owner: object,
    pending_upload: Callable[..., PendingUpload],
) -> None:
    from general_manager.uploads import views

    with patch.object(
        views.UploadIntent.objects, "using", side_effect=RuntimeError("db-secret")
    ):
        assert views._download_metadata(
            manager_name="Manager",
            object_id="1",
            field_name="avatar",
            current_key="avatars/a.png",
            database_alias="default",
        ) == (None, None)

    upload = pending_upload(
        final_target_pk=str(owner.pk),
        final_key="avatars/a.png",
        original_filename="friendly.png",
        verified_content_type="image/png",
    )
    assert views._download_metadata(
        manager_name=upload.intent.manager_name,
        object_id=str(owner.pk),
        field_name=upload.intent.field_name,
        current_key="avatars/a.png",
        database_alias="default",
    ) == ("friendly.png", "image/png")
    assert views._download_basename("folder/bad\x00name.png") == "download"
