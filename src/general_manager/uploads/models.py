"""Durable persistence for file upload intents."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID, uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone

from general_manager.uploads.tokens import verify_upload_token
from general_manager.uploads.types import UploadIntentState, UploadOperation

UPLOAD_OPERATION_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (member.value, member.value.replace("_", " ").title()) for member in UploadOperation
)
UPLOAD_INTENT_STATE_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (member.value, member.value.replace("_", " ").title())
    for member in UploadIntentState
)
TERMINAL_UPLOAD_INTENT_STATES: frozenset[str] = frozenset(
    {
        UploadIntentState.CONSUMED.value,
        UploadIntentState.SUPERSEDED.value,
        UploadIntentState.REJECTED.value,
        UploadIntentState.EXPIRED.value,
    }
)


class UploadQuotaLock(models.Model):
    """The single durable row used to serialize upload quota admission."""

    id: models.PositiveSmallIntegerField[int] = models.PositiveSmallIntegerField(
        primary_key=True,
        default=1,
        editable=False,
    )
    generation: models.PositiveBigIntegerField[int] = models.PositiveBigIntegerField(
        default=0
    )

    class Meta:
        constraints: ClassVar[tuple[models.BaseConstraint, ...]] = (
            models.CheckConstraint(
                condition=models.Q(id=1),
                name="gm_upload_quota_lock_singleton",
            ),
        )


class UploadIntent(models.Model):
    """A field-bound, single-use authorization to stage one uploaded object."""

    id: models.UUIDField[UUID] = models.UUIDField(
        primary_key=True,
        default=uuid4,
        editable=False,
    )
    user: Any = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        db_index=False,
    )
    token_digest: models.CharField[str] = models.CharField(max_length=64)

    manager_name: models.CharField[str] = models.CharField(max_length=255)
    field_name: models.CharField[str] = models.CharField(max_length=255)
    operation: models.CharField[str] = models.CharField(
        max_length=16,
        choices=UPLOAD_OPERATION_CHOICES,
    )
    target_id: models.TextField[str | None] = models.TextField(
        null=True,
        blank=True,
    )
    final_target_pk: models.TextField[str | None] = models.TextField(
        null=True,
        blank=True,
    )

    adapter_id: models.CharField[str] = models.CharField(max_length=128)
    adapter_version: models.CharField[str] = models.CharField(max_length=64)
    storage_fingerprint: models.CharField[str] = models.CharField(max_length=255)
    staging_key: models.CharField[str] = models.CharField(max_length=1024)
    transfer_attempt_count: models.PositiveIntegerField[int] = (
        models.PositiveIntegerField(default=0)
    )
    final_key: models.CharField[str | None] = models.CharField(
        max_length=1024,
        null=True,
        blank=True,
    )
    old_key: models.CharField[str | None] = models.CharField(
        max_length=1024,
        null=True,
        blank=True,
    )

    original_filename: models.CharField[str] = models.CharField(max_length=255)
    declared_size: models.BigIntegerField[int] = models.BigIntegerField()
    declared_content_type: models.CharField[str] = models.CharField(max_length=255)
    declared_checksum_sha256: models.CharField[str] = models.CharField(max_length=64)
    verified_size: models.BigIntegerField[int | None] = models.BigIntegerField(
        null=True,
        blank=True,
    )
    verified_content_type: models.CharField[str | None] = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    verified_checksum_sha256: models.CharField[str | None] = models.CharField(
        max_length=64,
        null=True,
        blank=True,
    )
    verified_width: models.PositiveBigIntegerField[int | None] = (
        models.PositiveBigIntegerField(null=True, blank=True)
    )
    verified_height: models.PositiveBigIntegerField[int | None] = (
        models.PositiveBigIntegerField(null=True, blank=True)
    )
    object_version: models.JSONField[dict[str, Any]] = models.JSONField(default=dict)
    final_object_version: models.JSONField[dict[str, Any]] = models.JSONField(
        default=dict
    )
    old_object_version: models.JSONField[dict[str, Any]] = models.JSONField(
        default=dict
    )
    old_cleanup_key: models.CharField[str | None] = models.CharField(
        max_length=1024,
        null=True,
        blank=True,
    )
    old_cleanup_version: models.JSONField[dict[str, Any]] = models.JSONField(
        default=dict
    )
    old_cleanup_completed_at: models.DateTimeField[datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    cleanup_completed_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True,
        blank=True,
    )
    cleanup_error_code: models.CharField[str] = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    cleanup_lease_expires_at: models.DateTimeField[datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    cleanup_lease_token: models.CharField[str] = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )

    state: models.CharField[str] = models.CharField(
        max_length=16,
        choices=UPLOAD_INTENT_STATE_CHOICES,
        default=UploadIntentState.PENDING.value,
    )
    transfer_lease_expires_at: models.DateTimeField[datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )
    expires_at: models.DateTimeField[datetime] = models.DateTimeField()
    uploaded_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True,
        blank=True,
    )
    consumed_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True,
        blank=True,
    )
    finalization_error_code: models.CharField[str] = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    finalization_attempt_count: models.IntegerField[int] = models.IntegerField(
        default=0
    )
    created_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True)

    class Meta:
        indexes: ClassVar[tuple[models.Index, ...]] = (
            models.Index(
                fields=["state", "expires_at"],
                name="gm_upload_state_exp_idx",
            ),
            models.Index(
                fields=["user", "state"],
                name="gm_upload_user_state_idx",
            ),
        )
        constraints: ClassVar[tuple[models.BaseConstraint, ...]] = (
            models.CheckConstraint(
                condition=models.Q(declared_size__gte=0),
                name="gm_upload_declared_size_gte_0",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(verified_size__isnull=True)
                    | models.Q(verified_size__gte=0)
                ),
                name="gm_upload_verified_size_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(finalization_attempt_count__gte=0),
                name="gm_upload_attempt_count_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(transfer_attempt_count__gte=0),
                name="gm_upload_transfer_attempt_gte_0",
            ),
        )

    def matches_token(self, token: object) -> bool:
        """Return whether ``token`` matches this intent's stored digest."""
        return verify_upload_token(token, self.token_digest)

    def is_expired(self, at: datetime | None = None) -> bool:
        """Return whether this intent has reached its expiry boundary."""
        return self.expires_at <= (at if at is not None else timezone.now())

    @property
    def is_terminal(self) -> bool:
        """Return whether no further state transition should consume the intent."""
        return self.state in TERMINAL_UPLOAD_INTENT_STATES

    def __str__(self) -> str:
        """Return a log-safe intent identity and state."""
        return f"UploadIntent {self.pk} [{self.state}]"

    def __repr__(self) -> str:
        """Return a debug representation without object or authorization metadata."""
        return f"<UploadIntent id={self.pk!s} state={self.state!r}>"
