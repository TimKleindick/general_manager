"""Bounded, capability-protected HTTP transfer views for file uploads."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
import re
import secrets
import time
from typing import Any, Iterator, Never, cast
from uuid import UUID

from django.core.cache import cache, caches
from django.core.cache.backends.base import BaseCache
from django.db import OperationalError, models
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from general_manager.uploads.adapters import ProxyUploadSink
from general_manager.uploads.config import FileUploadSettings, get_file_upload_settings
from general_manager.uploads.models import UploadIntent
from general_manager.uploads.services import (
    _resolve_file_field,
    _resolve_manager,
    _is_sqlite_busy_error,
    upload_adapter_registry,
    verify_upload_transfer_credential,
)
from general_manager.uploads.types import ObjectVersion, UploadIntentState


_TRANSFER_SCHEME = "GMUpload "
_STREAM_CHUNK_BYTES = 64 * 1024
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROXY_ATTEMPT_KEY = re.compile(
    r"^(?P<base>.+)\.proxy-attempt-(?P<attempt>[1-9][0-9]*)$"
)
_SQLITE_BUSY_RETRY_ATTEMPTS = 6
_SQLITE_BUSY_RETRY_DEADLINE_SECONDS = 0.25
_SQLITE_BUSY_BASE_DELAY_SECONDS = 0.002
_SQLITE_BUSY_MAX_DELAY_SECONDS = 0.02


@dataclass(frozen=True, slots=True)
class _TransferError:
    code: str
    status: int
    message: str


class _TransferFailure(Exception):
    """Internal control flow carrying one stable public response value."""

    def __init__(self, error: _TransferError) -> None:
        self.error = error
        super().__init__(error.code)


def _fail(error: _TransferError) -> Never:
    raise _TransferFailure(error) from None


@dataclass(frozen=True, slots=True)
class TransferClaim:
    """Opaque compare-and-swap handle for one active transfer lease."""

    intent_id: UUID
    owner_pk: object
    lease_expires_at: datetime
    intent_expires_at: datetime
    base_stage_key: str
    stage_key: str
    attempt_number: int


_METHOD_ERROR = _TransferError(
    "METHOD_NOT_ALLOWED", 405, "Only PUT is supported for file transfers."
)
_AUTH_ERROR = _TransferError(
    "UNAUTHENTICATED", 401, "Authentication is required to upload a file."
)
_NOT_FOUND_ERROR = _TransferError(
    "UPLOAD_NOT_FOUND", 404, "The requested upload is not available."
)
_CREDENTIAL_ERROR = _TransferError(
    "UPLOAD_CREDENTIAL_INVALID", 401, "The upload credential is invalid."
)
_EXPIRED_ERROR = _TransferError("UPLOAD_EXPIRED", 410, "The upload intent has expired.")
_CONFLICT_ERROR = _TransferError(
    "UPLOAD_TRANSFER_CONFLICT", 409, "The upload transfer is not available."
)
_SIZE_LARGE_ERROR = _TransferError(
    "UPLOAD_SIZE_MISMATCH", 413, "The uploaded file is larger than declared."
)
_SIZE_MISMATCH_ERROR = _TransferError(
    "UPLOAD_SIZE_MISMATCH", 422, "The uploaded file size did not match."
)
_TYPE_ERROR = _TransferError(
    "INVALID_FILE_TYPE", 415, "The uploaded content type did not match."
)
_CHECKSUM_ERROR = _TransferError(
    "UPLOAD_CHECKSUM_MISMATCH", 422, "The uploaded checksum did not match."
)
_STORAGE_CHANGED_ERROR = _TransferError(
    "UPLOAD_STORAGE_CHANGED", 503, "The upload storage configuration changed."
)
_STORAGE_ERROR = _TransferError(
    "UPLOAD_STORAGE_ERROR", 503, "The upload storage is temporarily unavailable."
)
_RATE_ERROR = _TransferError(
    "UPLOAD_RATE_LIMITED", 429, "Too many upload transfer requests were made."
)


def _json_error(error: _TransferError) -> JsonResponse:
    response = JsonResponse(
        {"error": {"code": error.code, "message": error.message}},
        status=error.status,
    )
    if error.status == 405:
        response["Allow"] = "PUT"
    response["Cache-Control"] = "no-store"
    return response


def _authenticated_owner_pk(request: HttpRequest) -> object:
    user = getattr(request, "user", None)
    owner_pk = getattr(user, "pk", None)
    if getattr(user, "is_authenticated", False) is not True or owner_pk is None:
        _fail(_AUTH_ERROR)
    return owner_pk


def _rate_key(scope: str) -> str:
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()
    return f"general_manager:upload_transfer:{digest}"


def _increment_rate_counter(key: str, *, window_seconds: int) -> int:
    try:
        if cache.add(key, 1, timeout=window_seconds):
            return 1
        try:
            return int(cache.incr(key, 1))
        except ValueError:
            if cache.add(key, 1, timeout=window_seconds):
                return 1
            return int(cache.incr(key, 1))
    except Exception as exc:
        raise _TransferFailure(_STORAGE_ERROR) from exc


def _enforce_transfer_rate_limit(
    owner_pk: object,
    settings: FileUploadSettings,
) -> None:
    try:
        backend = caches["default"]
    except Exception as exc:
        raise _TransferFailure(_STORAGE_ERROR) from exc
    if type(backend).incr is BaseCache.incr:
        _fail(_STORAGE_ERROR)

    owner_total = _increment_rate_counter(
        _rate_key(f"owner:{owner_pk}"),
        window_seconds=settings.transfer_rate_limit_window_seconds,
    )
    if owner_total > settings.max_transfer_attempts_per_user:
        _fail(_RATE_ERROR)
    global_total = _increment_rate_counter(
        _rate_key("global"),
        window_seconds=settings.transfer_rate_limit_window_seconds,
    )
    if global_total > settings.max_transfer_attempts_global:
        _fail(_RATE_ERROR)


def _owned_intent(intent_id: UUID, owner_pk: object) -> UploadIntent:
    settings = get_file_upload_settings()
    try:
        return UploadIntent.objects.using(settings.intent_database).get(
            pk=intent_id,
            user_id=owner_pk,
        )
    except UploadIntent.DoesNotExist as exc:
        raise _TransferFailure(_NOT_FOUND_ERROR) from exc
    except Exception as exc:
        raise _TransferFailure(_STORAGE_ERROR) from exc


def _mark_expired(intent: UploadIntent, owner_pk: object, *, at: datetime) -> None:
    settings = get_file_upload_settings()
    try:
        UploadIntent.objects.using(settings.intent_database).filter(
            pk=intent.pk,
            user_id=owner_pk,
            expires_at__lte=at,
            state__in=(
                UploadIntentState.PENDING.value,
                UploadIntentState.TRANSFERRING.value,
            ),
        ).update(
            state=UploadIntentState.EXPIRED.value,
            transfer_lease_expires_at=None,
        )
    except Exception as exc:
        raise _TransferFailure(_STORAGE_ERROR) from exc


def _attempt_staging_key(base_stage_key: str, attempt_number: int) -> str:
    stage_key = f"{base_stage_key}.proxy-attempt-{attempt_number}"
    if not base_stage_key or len(stage_key) > 1024:
        _fail(_STORAGE_ERROR)
    return stage_key


def iter_proxy_attempt_stage_keys(intent: UploadIntent) -> Iterator[str]:
    """Yield every deterministic proxy-attempt key retained by ``intent``."""

    attempt_count = intent.transfer_attempt_count
    if (
        isinstance(attempt_count, bool)
        or not isinstance(attempt_count, int)
        or attempt_count < 0
    ):
        _fail(_STORAGE_ERROR)
    current_key = intent.staging_key
    match = _PROXY_ATTEMPT_KEY.fullmatch(current_key)
    if match is None:
        base_stage_key = current_key
    else:
        if int(match.group("attempt")) != attempt_count:
            _fail(_STORAGE_ERROR)
        base_stage_key = match.group("base")
    for attempt_number in range(1, attempt_count + 1):
        yield _attempt_staging_key(base_stage_key, attempt_number)


def _claim_transfer(intent_id: UUID, owner_pk: object) -> TransferClaim:
    """Atomically claim a pending or abandoned transfer using one finite lease."""

    settings = get_file_upload_settings()
    deadline = time.monotonic() + _SQLITE_BUSY_RETRY_DEADLINE_SECONDS
    for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
        try:
            return _claim_transfer_once(intent_id, owner_pk, settings=settings)
        except OperationalError as exc:
            if not _is_sqlite_busy_error(
                exc,
                database_alias=settings.intent_database,
            ):
                raise _TransferFailure(_STORAGE_ERROR) from exc
            remaining = deadline - time.monotonic()
            if attempt + 1 >= _SQLITE_BUSY_RETRY_ATTEMPTS or remaining <= 0:
                raise _TransferFailure(_STORAGE_ERROR) from exc
            base_delay = min(
                _SQLITE_BUSY_BASE_DELAY_SECONDS * (2**attempt),
                _SQLITE_BUSY_MAX_DELAY_SECONDS,
            )
            jitter = base_delay * secrets.randbelow(1_001) / 1_000
            time.sleep(min(base_delay + jitter, remaining))
    raise AssertionError("unreachable")


def _claim_transfer_once(
    intent_id: UUID,
    owner_pk: object,
    *,
    settings: FileUploadSettings,
) -> TransferClaim:
    now = timezone.now()
    try:
        intent = UploadIntent.objects.using(settings.intent_database).get(
            pk=intent_id,
            user_id=owner_pk,
        )
    except UploadIntent.DoesNotExist as exc:
        raise _TransferFailure(_NOT_FOUND_ERROR) from exc
    except OperationalError:
        raise
    except Exception as exc:
        raise _TransferFailure(_STORAGE_ERROR) from exc

    if intent.expires_at <= now:
        _mark_expired(intent, owner_pk, at=now)
        _fail(_EXPIRED_ERROR)
    if intent.transfer_attempt_count >= settings.max_transfer_attempts_per_intent:
        _fail(_RATE_ERROR)
    attempt_number = intent.transfer_attempt_count + 1
    stage_key = _attempt_staging_key(intent.staging_key, attempt_number)
    lease_expires_at = min(
        intent.expires_at,
        now + timedelta(seconds=settings.transfer_lease_seconds),
    )
    eligible_state = models.Q(state=UploadIntentState.PENDING.value) | models.Q(
        state=UploadIntentState.TRANSFERRING.value,
        transfer_lease_expires_at__lte=now,
    )
    try:
        updated = (
            UploadIntent.objects.using(settings.intent_database)
            .filter(
                pk=intent_id,
                user_id=owner_pk,
                expires_at__gt=now,
                staging_key=intent.staging_key,
                transfer_attempt_count=intent.transfer_attempt_count,
            )
            .filter(eligible_state)
            .update(
                state=UploadIntentState.TRANSFERRING.value,
                transfer_lease_expires_at=lease_expires_at,
                transfer_attempt_count=attempt_number,
            )
        )
    except OperationalError:
        raise
    except Exception as exc:
        raise _TransferFailure(_STORAGE_ERROR) from exc
    if updated != 1:
        _fail(_CONFLICT_ERROR)
    return TransferClaim(
        intent_id=intent_id,
        owner_pk=owner_pk,
        lease_expires_at=lease_expires_at,
        intent_expires_at=intent.expires_at,
        base_stage_key=intent.staging_key,
        stage_key=stage_key,
        attempt_number=attempt_number,
    )


def _renew_transfer_lease(claim: TransferClaim) -> TransferClaim | None:
    """Renew ``claim`` only while its exact compare-and-swap lease is current."""

    settings = get_file_upload_settings()
    now = timezone.now()
    if claim.intent_expires_at <= now:
        return None
    renewed_until = min(
        claim.intent_expires_at,
        now + timedelta(seconds=settings.transfer_lease_seconds),
    )
    if renewed_until <= claim.lease_expires_at:
        return claim
    try:
        updated = (
            UploadIntent.objects.using(settings.intent_database)
            .filter(
                pk=claim.intent_id,
                user_id=claim.owner_pk,
                state=UploadIntentState.TRANSFERRING.value,
                transfer_lease_expires_at=claim.lease_expires_at,
                expires_at__gt=now,
                staging_key=claim.base_stage_key,
                transfer_attempt_count=claim.attempt_number,
            )
            .update(transfer_lease_expires_at=renewed_until)
        )
    except Exception as exc:
        raise _TransferFailure(_STORAGE_ERROR) from exc
    if updated != 1:
        return None
    return replace(claim, lease_expires_at=renewed_until)


class _BoundedRequestChunks:
    """Single-pass request iterator that hashes, counts, and renews its lease."""

    def __init__(
        self,
        request: HttpRequest,
        claim: TransferClaim,
        *,
        expected_size: int,
        maximum_size: int,
    ) -> None:
        self._request = request
        self.claim = claim
        self.expected_size = expected_size
        self.maximum_size = maximum_size
        self.size = 0
        self.checksum_sha256 = ""

    def __iter__(self) -> Iterator[bytes]:
        digest = hashlib.sha256()
        while True:
            try:
                chunk = self._request.read(_STREAM_CHUNK_BYTES)
            except Exception as exc:
                raise _TransferFailure(_STORAGE_ERROR) from exc
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                _fail(_STORAGE_ERROR)
            next_size = self.size + len(chunk)
            if next_size > self.maximum_size or next_size > self.expected_size:
                _fail(_SIZE_LARGE_ERROR)
            renewed = _renew_transfer_lease(self.claim)
            if renewed is None:
                _fail(_CONFLICT_ERROR)
            self.claim = renewed
            self.size = next_size
            digest.update(chunk)
            yield chunk

        self.checksum_sha256 = digest.hexdigest()
        if self.size != self.expected_size:
            _fail(_SIZE_MISMATCH_ERROR)


def _parse_content_length(request: HttpRequest) -> int | None:
    raw = request.META.get("CONTENT_LENGTH")
    if raw in (None, ""):
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        _fail(_SIZE_MISMATCH_ERROR)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise _TransferFailure(_SIZE_MISMATCH_ERROR) from exc
    if value < 0:
        _fail(_SIZE_MISMATCH_ERROR)
    return value


def _resolve_adapter(intent: UploadIntent) -> ProxyUploadSink:
    try:
        manager = _resolve_manager(intent.manager_name)
        _model, model_field = _resolve_file_field(
            cast(Any, manager.Interface),
            intent.field_name,
        )
        if not isinstance(intent.adapter_version, str):
            _fail(_STORAGE_CHANGED_ERROR)
        adapter_version = int(intent.adapter_version)
        adapter = upload_adapter_registry.resolve_by_id(
            intent.adapter_id,
            adapter_version,
            model_field.storage,
        )
    except _TransferFailure:
        raise
    except Exception as exc:
        raise _TransferFailure(_STORAGE_CHANGED_ERROR) from exc
    if adapter is None or not isinstance(adapter, ProxyUploadSink):
        _fail(_STORAGE_CHANGED_ERROR)
    try:
        fingerprint = adapter.storage_fingerprint()
    except Exception as exc:
        raise _TransferFailure(_STORAGE_CHANGED_ERROR) from exc
    if fingerprint != intent.storage_fingerprint:
        _fail(_STORAGE_CHANGED_ERROR)
    return adapter


def _validate_object_version(
    value: object,
    *,
    expected_size: int,
    expected_content_type: str,
    expected_checksum: str,
) -> ObjectVersion:
    if type(value) is not ObjectVersion:
        _fail(_STORAGE_ERROR)
    version = value
    if (
        isinstance(version.size, bool)
        or not isinstance(version.size, int)
        or version.size < 0
    ):
        _fail(_STORAGE_ERROR)
    if version.size != expected_size:
        _fail(_SIZE_MISMATCH_ERROR)
    if version.checksum_sha256 != expected_checksum:
        _fail(_CHECKSUM_ERROR)
    if version.content_type != expected_content_type:
        _fail(_TYPE_ERROR)
    for identity in (version.version_id, version.etag):
        if identity is not None and (
            not isinstance(identity, str)
            or not identity
            or len(identity) > 1024
            or any(
                ord(character) < 32 or ord(character) == 127 for character in identity
            )
        ):
            _fail(_STORAGE_ERROR)
    if _HEX_SHA256.fullmatch(version.checksum_sha256) is None:
        _fail(_STORAGE_ERROR)
    return version


def _reset_failed_transfer(
    claim: TransferClaim,
    *,
    adapter: ProxyUploadSink,
    intent: UploadIntent,
) -> None:
    """Clean only this intent's stage, then make the transfer retryable by CAS."""

    del intent
    with suppress(Exception):
        adapter.delete_stage(claim.stage_key)
    _release_unstarted_transfer(claim)


def _release_unstarted_transfer(claim: TransferClaim) -> None:
    """Release an exact lease when no adapter stage operation has started."""

    settings = get_file_upload_settings()
    now = timezone.now()
    next_state = (
        UploadIntentState.EXPIRED.value
        if claim.intent_expires_at <= now
        else UploadIntentState.PENDING.value
    )
    try:
        UploadIntent.objects.using(settings.intent_database).filter(
            pk=claim.intent_id,
            user_id=claim.owner_pk,
            state=UploadIntentState.TRANSFERRING.value,
            transfer_lease_expires_at=claim.lease_expires_at,
            staging_key=claim.base_stage_key,
            transfer_attempt_count=claim.attempt_number,
        ).update(
            state=next_state,
            transfer_lease_expires_at=None,
        )
    except Exception:  # noqa: BLE001 - finite lease remains the safe fallback
        return


def _complete_transfer(
    claim: TransferClaim,
    *,
    version: ObjectVersion,
    uploaded_at: datetime,
) -> None:
    settings = get_file_upload_settings()
    metadata = {
        "version_id": version.version_id,
        "etag": version.etag,
        "checksum_sha256": version.checksum_sha256,
        "size": version.size,
        "content_type": version.content_type,
    }
    try:
        updated = (
            UploadIntent.objects.using(settings.intent_database)
            .filter(
                pk=claim.intent_id,
                user_id=claim.owner_pk,
                state=UploadIntentState.TRANSFERRING.value,
                transfer_lease_expires_at=claim.lease_expires_at,
                expires_at__gt=uploaded_at,
                staging_key=claim.base_stage_key,
                transfer_attempt_count=claim.attempt_number,
            )
            .update(
                state=UploadIntentState.UPLOADED.value,
                transfer_lease_expires_at=None,
                staging_key=claim.stage_key,
                verified_size=version.size,
                verified_content_type=version.content_type,
                verified_checksum_sha256=version.checksum_sha256,
                object_version=metadata,
                uploaded_at=uploaded_at,
            )
        )
    except Exception as exc:
        raise _TransferFailure(_STORAGE_ERROR) from exc
    if updated != 1:
        _fail(_CONFLICT_ERROR)


def _transfer(request: HttpRequest, intent_id: UUID) -> HttpResponse:
    settings = get_file_upload_settings()
    if not settings.enabled:
        _fail(_NOT_FOUND_ERROR)
    owner_pk = _authenticated_owner_pk(request)
    _enforce_transfer_rate_limit(owner_pk, settings)
    intent = _owned_intent(intent_id, owner_pk)
    now = timezone.now()
    if intent.expires_at <= now:
        _mark_expired(intent, owner_pk, at=now)
        _fail(_EXPIRED_ERROR)

    authorization = request.headers.get("Authorization")
    if not isinstance(authorization, str) or not authorization.startswith(
        _TRANSFER_SCHEME
    ):
        _fail(_CREDENTIAL_ERROR)
    credential = authorization[len(_TRANSFER_SCHEME) :]
    if not credential or credential.strip() != credential or " " in credential:
        _fail(_CREDENTIAL_ERROR)
    if not verify_upload_transfer_credential(
        credential,
        intent_id=intent.id,
        owner_pk=owner_pk,
        adapter_id=intent.adapter_id,
        max_age=settings.transfer_credential_ttl_seconds,
    ):
        _fail(_CREDENTIAL_ERROR)

    claim = _claim_transfer(intent.id, owner_pk)
    try:
        incoming_type = request.content_type.lower() if request.content_type else ""
        if incoming_type != intent.declared_content_type:
            _fail(_TYPE_ERROR)
        content_length = _parse_content_length(request)
        maximum_size = min(settings.max_bytes, intent.declared_size)
        if intent.declared_size > settings.max_bytes:
            _fail(_SIZE_LARGE_ERROR)
        if content_length is not None:
            if content_length > maximum_size:
                _fail(_SIZE_LARGE_ERROR)
            if content_length != intent.declared_size:
                _fail(_SIZE_MISMATCH_ERROR)

        adapter = _resolve_adapter(intent)
    except _TransferFailure:
        _release_unstarted_transfer(claim)
        raise
    except Exception as exc:
        _release_unstarted_transfer(claim)
        raise _TransferFailure(_STORAGE_ERROR) from exc

    chunks = _BoundedRequestChunks(
        request,
        claim,
        expected_size=intent.declared_size,
        maximum_size=maximum_size,
    )
    try:
        raw_version = adapter.save_stage(
            claim.stage_key,
            chunks,
            content_type=intent.declared_content_type,
            checksum_sha256=intent.declared_checksum_sha256,
            size=intent.declared_size,
        )
        claim = chunks.claim
        if chunks.size != intent.declared_size or not chunks.checksum_sha256:
            # Returning before exhausting the iterator violates the streaming
            # sink contract and leaves the adapter's result untrusted.
            _fail(_STORAGE_ERROR)
        if chunks.checksum_sha256 != intent.declared_checksum_sha256:
            _fail(_CHECKSUM_ERROR)
        version = _validate_object_version(
            raw_version,
            expected_size=chunks.size,
            expected_content_type=intent.declared_content_type,
            expected_checksum=chunks.checksum_sha256,
        )
        _complete_transfer(claim, version=version, uploaded_at=timezone.now())
    except _TransferFailure:
        _reset_failed_transfer(chunks.claim, adapter=adapter, intent=intent)
        raise
    except Exception as exc:
        _reset_failed_transfer(chunks.claim, adapter=adapter, intent=intent)
        raise _TransferFailure(_STORAGE_ERROR) from exc
    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def proxy_upload_view(request: HttpRequest, intent_id: UUID) -> HttpResponse:
    """Accept one bounded proxy transfer without exposing internal metadata."""

    if request.method != "PUT":
        return _json_error(_METHOD_ERROR)
    try:
        return _transfer(request, intent_id)
    except _TransferFailure as failure:
        return _json_error(failure.error)
    except Exception:  # noqa: BLE001 - public HTTP boundary must fail closed
        return _json_error(_STORAGE_ERROR)


@csrf_exempt
def private_download_view(request: HttpRequest, capability: str) -> HttpResponse:
    """Reserve the framework-owned download route until Task 9 wires capabilities."""

    del request, capability
    return _json_error(_NOT_FOUND_ERROR)
