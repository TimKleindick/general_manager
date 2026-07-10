"""Security tests for upload metrics and structured logging."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

import pytest
import logging

from general_manager.uploads.metrics import (
    observe_upload_duration,
    record_upload_bytes,
    record_upload_cleanup,
    record_upload_failure,
    record_upload_transition,
    restore_upload_metrics_backend,
    set_upload_metrics_backend,
)
from django.utils import timezone

from general_manager.uploads.models import UploadIntent
from general_manager.uploads.types import ObjectVersion, UploadIntentState
from general_manager.uploads.views import TransferClaim, _complete_transfer
from general_manager.uploads import finalization
from general_manager.uploads import metrics
from general_manager.uploads.finalization import _record_finalization_failure
from general_manager.uploads import services


@dataclass
class RecordingBackend:
    transitions: Counter[tuple[str, str]] = field(default_factory=Counter)
    bytes: list[tuple[int, dict[str, str]]] = field(default_factory=list)
    failures: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    cleanups: Counter[tuple[str, str]] = field(default_factory=Counter)
    durations: list[tuple[float, dict[str, str]]] = field(default_factory=list)

    def increment(self, metric: str, value: int, labels: dict[str, str]) -> None:
        if metric == "upload_transition_total":
            self.transitions[(labels["adapter"], labels["state"])] += value
        elif metric == "upload_failure_total":
            self.failures[
                (labels["adapter"], labels["operation"], labels["error"])
            ] += value
        elif metric == "upload_cleanup_total":
            self.cleanups[(labels["operation"], labels["result"])] += value

    def observe(self, metric: str, value: float, labels: dict[str, str]) -> None:
        if metric == "upload_bytes":
            self.bytes.append((int(value), labels))
        elif metric == "upload_duration_seconds":
            self.durations.append((value, labels))


def test_upload_logs_and_metrics_never_include_secrets(caplog) -> None:
    caplog.set_level(logging.INFO, logger="general_manager.uploads")
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    intent = SimpleNamespace(
        id=uuid4(),
        adapter_id="proxy",
        manager_name="Profile",
        field_name="avatar",
        declared_size=12,
        state="pending",
        token_digest="opaque-token-digest",  # noqa: S106 - hostile fixture
        staging_key="secret/stage/key",
        final_key="secret/final/key",
        original_filename="secret-portrait.png",
        authorization="Bearer secret",
        upload_url="https://example.invalid/u?X-Amz-Signature=secret",
    )
    try:
        record_upload_transition(intent, "uploaded")
        record_upload_bytes(
            adapter="proxy",
            transport="proxy",
            byte_count=12,
            intent=intent,
        )
        record_upload_failure(
            adapter="proxy",
            operation="transfer",
            error="storage_error",
            intent=intent,
        )
        record_upload_cleanup(operation="expired", result="completed", intent=intent)
        observe_upload_duration(
            adapter="proxy",
            operation="cleanup",
            result="completed",
            seconds=0.25,
            intent=intent,
        )
    finally:
        restore_upload_metrics_backend(previous)

    assert backend.transitions[("proxy", "uploaded")] == 1
    assert backend.bytes == [(12, {"adapter": "proxy", "transport": "proxy"})]
    assert backend.failures[("proxy", "transfer", "storage_error")] == 1
    assert backend.cleanups[("expired", "completed")] == 1
    captured = caplog.text
    assert "upload operation" in captured
    assert "upload failure" in captured
    structured = [getattr(record, "upload", {}) for record in caplog.records]
    assert any(
        value.get("intent_id") == str(intent.id)
        and value.get("adapter") == "proxy"
        and value.get("manager") == "Profile"
        and value.get("field") == "avatar"
        and value.get("state") == "pending"
        and value.get("size") == 12
        for value in structured
    )
    assert any(value.get("bytes") == 12 for value in structured)
    required_snapshot = {
        "intent_id": str(intent.id),
        "adapter": "proxy",
        "manager": "Profile",
        "field": "avatar",
        "declared_size": 12,
    }
    relevant = {
        "upload_transition",
        "upload_transfer",
        "upload_failure",
        "upload_operation",
        "upload_duration",
    }
    for value in structured:
        if value.get("event") in relevant:
            assert required_snapshot.items() <= value.items()
            assert value.get("state") in {"pending", "uploaded"}
    assert any(
        value.get("event") == "upload_transition"
        and value.get("to_state") == "uploaded"
        for value in structured
    )
    assert any(
        value.get("event") == "upload_operation" and value.get("result") == "completed"
        for value in structured
    )
    assert any(
        value.get("event") == "upload_duration"
        and value.get("duration_seconds") == 0.25
        for value in structured
    )
    forbidden_keys = {
        "authorization",
        "cause",
        "final_key",
        "original_filename",
        "staging_key",
        "token",
        "token_digest",
        "upload_url",
        "url",
    }
    for value in structured:
        assert forbidden_keys.isdisjoint(value)
    for secret in (
        "opaque-token",
        "Bearer secret",
        "X-Amz-Signature",
        "secret/stage/key",
        "secret/final/key",
        "secret-portrait.png",
    ):
        assert secret not in captured
        assert secret not in repr(structured)


def test_metrics_backend_failure_never_changes_upload_behavior() -> None:
    class BrokenBackend:
        def increment(self, metric: str, value: int, labels: dict[str, str]) -> None:
            raise RuntimeError("backend-secret")

        def observe(self, metric: str, value: float, labels: dict[str, str]) -> None:
            raise RuntimeError("backend-secret")

    previous = set_upload_metrics_backend(BrokenBackend())
    try:
        record_upload_bytes(adapter="proxy", transport="proxy", byte_count=1)
        record_upload_failure(
            adapter="proxy", operation="transfer", error="storage_error"
        )
    finally:
        restore_upload_metrics_backend(previous)


def test_metrics_reject_unbounded_or_sensitive_labels_without_calling_backend() -> None:
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    try:
        record_upload_failure(
            adapter="proxy",
            operation="Profile.avatar",
            error="https://example.invalid/?token=secret",
        )
    finally:
        restore_upload_metrics_backend(previous)

    assert backend.failures == Counter()


def test_metrics_backend_rejects_objects_without_the_backend_protocol() -> None:
    with pytest.raises(TypeError, match=r"increment.*observe"):
        set_upload_metrics_backend(object())  # type: ignore[arg-type]


def test_invalid_metric_values_are_ignored_without_backend_calls() -> None:
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    try:
        record_upload_transition(SimpleNamespace(adapter_id="bad adapter"), "uploaded")
        record_upload_bytes(adapter="proxy", transport="proxy", byte_count=True)
        record_upload_cleanup(operation="unknown", result="completed")
        observe_upload_duration(
            adapter="proxy",
            operation="transfer",
            result="completed",
            seconds=-1,
        )
        record_upload_cleanup(
            operation="expired",
            result="completed",
            intent=SimpleNamespace(adapter_id="bad adapter"),
        )
    finally:
        restore_upload_metrics_backend(previous)

    assert backend.transitions == Counter()
    assert backend.bytes == []
    assert backend.cleanups == Counter({("expired", "completed"): 1})
    assert backend.durations == []


def test_metrics_isolate_hostile_attributes_and_logging_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HostileIntent:
        adapter_id = "proxy"

        @property
        def id(self) -> object:
            raise RuntimeError

    def fail_logging(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError

    monkeypatch.setattr(metrics._logger, "info", fail_logging)
    monkeypatch.setattr(metrics._logger, "warning", fail_logging)

    record_upload_transition(HostileIntent(), "uploaded", from_state="pending")
    record_upload_failure(
        adapter="proxy",
        operation="transfer",
        error="storage_error",
        correlation_id="not-a-uuid",
    )


def _persisted_intent(state: UploadIntentState) -> UploadIntent:
    return UploadIntent.objects.create(
        token_digest="a" * 64,
        manager_name="Profile",
        field_name="avatar",
        operation="create",
        adapter_id="proxy",
        adapter_version="1",
        storage_fingerprint="sha256:" + "b" * 64,
        staging_key="gm-staging/base",
        transfer_attempt_count=1,
        declared_size=1,
        declared_content_type="application/octet-stream",
        declared_checksum_sha256="c" * 64,
        state=state.value,
        expires_at=timezone.now() + timedelta(minutes=5),
    )


def _version() -> ObjectVersion:
    return ObjectVersion(
        version_id="v1",
        etag="etag",
        checksum_sha256="c" * 64,
        size=1,
        content_type="application/octet-stream",
    )


def _claim(intent: UploadIntent) -> TransferClaim:
    lease = timezone.now() + timedelta(minutes=1)
    UploadIntent.objects.filter(pk=intent.pk).update(
        transfer_lease_expires_at=lease,
        user_id=None,
    )
    return TransferClaim(
        intent_id=intent.id,
        owner_pk=None,
        lease_expires_at=lease,
        intent_expires_at=intent.expires_at,
        base_stage_key=intent.staging_key,
        stage_key=f"{intent.staging_key}.proxy-attempt-1",
        attempt_number=1,
    )


@pytest.mark.django_db(transaction=True)
def test_transfer_completion_instruments_transition_and_bytes() -> None:
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    intent = _persisted_intent(UploadIntentState.TRANSFERRING)
    claim = _claim(intent)
    try:
        _complete_transfer(claim, version=_version(), uploaded_at=timezone.now())
    finally:
        restore_upload_metrics_backend(previous)

    assert backend.transitions[("proxy", "uploaded")] == 1
    assert backend.bytes == [(1, {"adapter": "proxy", "transport": "proxy"})]


@pytest.mark.django_db(transaction=True)
def test_finalization_failure_instruments_only_sanitized_error_class() -> None:
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    intent = _persisted_intent(UploadIntentState.FINALIZING)
    try:
        _record_finalization_failure(
            intent.id,
            alias="default",
            error=OSError("https://secret.invalid/?X-Amz-Signature=secret"),
        )
    finally:
        restore_upload_metrics_backend(previous)

    assert backend.failures[("proxy", "finalization", "storage_error")] == 1


@pytest.mark.django_db(transaction=True)
def test_begin_pending_transition_is_recorded_after_commit(
    django_capture_on_commit_callbacks,
) -> None:
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    intent = _persisted_intent(UploadIntentState.PENDING)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            services._record_pending_intent_after_commit(intent, alias="default")
    finally:
        restore_upload_metrics_backend(previous)

    assert backend.transitions[("proxy", "pending")] == 1


@pytest.mark.django_db(transaction=True)
def test_finalization_transitions_are_recorded_after_commit(
    django_capture_on_commit_callbacks,
) -> None:
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    intent = _persisted_intent(UploadIntentState.FINALIZING)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            finalization._record_transition_after_commit(
                intent,
                UploadIntentState.CONSUMED.value,
                alias="default",
            )
    finally:
        restore_upload_metrics_backend(previous)

    assert backend.transitions[("proxy", "consumed")] == 1


@pytest.mark.django_db(transaction=True)
def test_direct_preflight_instruments_uploaded_transition_and_bytes(
    django_capture_on_commit_callbacks,
) -> None:
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    intent = _persisted_intent(UploadIntentState.PENDING)
    plan = services._PreflightPlan(
        field_name="avatar",
        intent=intent,
        token="redacted-in-test",  # noqa: S106 - opaque fixture credential
        version=_version(),
        direct=True,
    )
    try:
        with (
            patch("general_manager.uploads.services._validate_intent_binding"),
            django_capture_on_commit_callbacks(execute=True),
        ):
            services._persist_direct_preflight(
                [plan],
                owner_pk=None,
                manager_name="Profile",
                operation=services.UploadOperation.CREATE,
                target_id=None,
                database_alias="default",
            )
    finally:
        restore_upload_metrics_backend(previous)

    assert backend.transitions[("proxy", "uploaded")] == 1
    assert backend.bytes == [(1, {"adapter": "proxy", "transport": "direct"})]


@pytest.mark.django_db(transaction=True)
def test_rejection_and_proxy_expiry_emit_transitions_and_failures() -> None:
    from general_manager.uploads.errors import InvalidFileTypeError
    from general_manager.uploads.views import _mark_expired

    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    uploaded = _persisted_intent(UploadIntentState.UPLOADED)
    pending = _persisted_intent(UploadIntentState.PENDING)
    pending.expires_at = timezone.now() - timedelta(seconds=1)
    pending.save(update_fields=("expires_at",))
    try:
        finalization._reject_content_intent(
            uploaded,
            InvalidFileTypeError(),
            database_alias="default",
        )
        _mark_expired(pending, None, at=timezone.now())
    finally:
        restore_upload_metrics_backend(previous)

    assert backend.transitions[("proxy", "rejected")] == 1
    assert backend.transitions[("proxy", "expired")] == 1
    assert backend.failures[("proxy", "validation", "file_type")] == 1
    assert any(
        labels["operation"] == "validation" and labels["result"] == "failed"
        for _, labels in backend.durations
    )


@pytest.mark.django_db(transaction=True)
def test_begin_and_finalization_failures_observe_sanitized_duration(
    settings, caplog
) -> None:
    from general_manager.uploads.errors import UploadAuthenticationError

    settings.GENERAL_MANAGER = {"FILE_UPLOADS": {"ENABLED": True}}
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    caplog.set_level(logging.WARNING, logger="general_manager.uploads")
    finalizing = _persisted_intent(UploadIntentState.FINALIZING)
    try:
        with pytest.raises(UploadAuthenticationError):
            services.begin_file_upload(
                SimpleNamespace(is_authenticated=False, pk=None),
                SimpleNamespace(),
            )
        assert finalization.finalize_upload_intent(finalizing.id) == "failed"
    finally:
        restore_upload_metrics_backend(previous)

    assert any(labels["operation"] == "begin" for _, labels in backend.durations)
    assert any(labels["operation"] == "finalization" for _, labels in backend.durations)
    assert backend.failures[("unknown", "begin", "authentication_error")] == 1
    begin_records = [
        record
        for record in caplog.records
        if getattr(record, "upload", {}).get("operation") == "begin"
    ]
    assert len(begin_records) == 1
    assert uuid4().__class__(begin_records[0].upload["intent_id"])


@pytest.mark.django_db(transaction=True)
def test_proxy_transfer_failure_records_sanitized_failure_and_duration(
    settings, caplog
) -> None:
    from django.test import RequestFactory
    from django.contrib.auth.models import AnonymousUser
    from general_manager.uploads.views import proxy_upload_view

    settings.GENERAL_MANAGER = {"FILE_UPLOADS": {"ENABLED": True}}
    backend = RecordingBackend()
    previous = set_upload_metrics_backend(backend)
    caplog.set_level(logging.WARNING, logger="general_manager.uploads")
    intent_id = uuid4()
    request = RequestFactory().put("/gm/uploads/unused", data=b"bytes")
    request.user = AnonymousUser()
    try:
        response = proxy_upload_view(request, intent_id)
    finally:
        restore_upload_metrics_backend(previous)

    assert response.status_code == 401
    assert backend.failures[("unknown", "transfer", "authentication_error")] == 1
    assert any(
        labels["operation"] == "transfer" and labels["result"] == "failed"
        for _, labels in backend.durations
    )
    transfer_records = [
        record
        for record in caplog.records
        if getattr(record, "upload", {}).get("operation") == "transfer"
    ]
    assert transfer_records[0].upload["intent_id"] == str(intent_id)
