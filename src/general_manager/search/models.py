"""Persistent search reconciliation state."""

from __future__ import annotations

from datetime import datetime

from django.db import models
from django.utils import timezone

SEARCH_INDEX_DIRTY_REASON_INITIALIZATION = "initialization"
SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED = "schema_changed"
SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED = "data_changed"
SEARCH_INDEX_DIRTY_REASON_FORCED = "forced"

SEARCH_INDEX_DIRTY_REASONS = (
    (SEARCH_INDEX_DIRTY_REASON_INITIALIZATION, "Initialization"),
    (SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED, "Schema changed"),
    (SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED, "Data changed"),
    (SEARCH_INDEX_DIRTY_REASON_FORCED, "Forced"),
)


class SearchIndexState(models.Model):
    """
    Durable reconciliation state for one searchable manager/index pair.

    `manager_path` stores the dotted import path for the manager class,
    `index_name` stores the logical search index, and `schema_fingerprint`
    records the last known schema hash for that pair. The reconciliation
    planner creates and updates fingerprints; this model only stores the value.
    Dirty fields drive reconciliation scheduling; claim fields are used by
    worker helpers to avoid processing the same dirty state concurrently.
    """

    manager_path: models.CharField[str] = models.CharField(max_length=512)
    index_name: models.CharField[str] = models.CharField(max_length=255)
    schema_fingerprint: models.CharField[str] = models.CharField(max_length=64)
    initialized_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )
    last_reconciled_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )
    dirty_since: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )
    dirty_reason: models.CharField[str] = models.CharField(
        max_length=32,
        choices=SEARCH_INDEX_DIRTY_REASONS,
        blank=True,
        default="",
    )
    claim_token: models.CharField[str] = models.CharField(
        max_length=64, blank=True, default=""
    )
    claimed_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )
    claim_expires_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )
    last_error: models.TextField[str] = models.TextField(blank=True, default="")
    created_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now_add=True)
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("manager_path", "index_name"),
                name="general_manager_search_state_manager_index_uniq",
            ),
        )
        indexes = (
            models.Index(
                fields=["dirty_since", "index_name"],
                name="general_man_dirty_s_71fc00_idx",
            ),
            models.Index(
                fields=["claim_token"],
                name="general_man_claim_t_3aaacc_idx",
            ),
            models.Index(
                fields=["claim_expires_at"],
                name="general_man_claim_e_1fa228_idx",
            ),
            models.Index(
                fields=["last_reconciled_at"],
                name="general_man_last_re_81038c_idx",
            ),
        )

    def mark_dirty(self, reason: str) -> None:
        """
        Mark this state as needing reconciliation.

        The first dirty timestamp is recorded with `timezone.now()` and
        preserved so repeated marks keep the
        original age of the pending work. `dirty_reason` is always overwritten
        with the provided string; callers should pass one of
        `SEARCH_INDEX_DIRTY_REASON_INITIALIZATION`,
        `SEARCH_INDEX_DIRTY_REASON_SCHEMA_CHANGED`,
        `SEARCH_INDEX_DIRTY_REASON_DATA_CHANGED`, or
        `SEARCH_INDEX_DIRTY_REASON_FORCED` because this method does not
        validate choices before saving.

        Parameters:
            reason: Stored dirty reason string.

        Raises:
            django.db.Error: Database save errors propagate unchanged.
            ValueError: Propagated from Django field assignment/save validation
                if a configured backend validates the value.
        """
        if self.dirty_since is None:
            self.dirty_since = timezone.now()
        self.dirty_reason = reason
        self.save(update_fields=["dirty_since", "dirty_reason", "updated_at"])

    def clear_dirty(self) -> None:
        """
        Record successful reconciliation and release any active claim.

        Timestamps use `timezone.now()`. On first success this also initializes
        `initialized_at`. Every call updates `last_reconciled_at`, clears dirty
        state, claim state, and `last_error`, then persists only the changed
        reconciliation fields.

        Raises:
            django.db.Error: Database save errors propagate unchanged.
        """
        now = timezone.now()
        if self.initialized_at is None:
            self.initialized_at = now
        self.last_reconciled_at = now
        self.dirty_since = None
        self.dirty_reason = ""
        self.claim_token = ""
        self.claimed_at = None
        self.claim_expires_at = None
        self.last_error = ""
        self.save(
            update_fields=[
                "initialized_at",
                "last_reconciled_at",
                "dirty_since",
                "dirty_reason",
                "claim_token",
                "claimed_at",
                "claim_expires_at",
                "last_error",
                "updated_at",
            ]
        )
