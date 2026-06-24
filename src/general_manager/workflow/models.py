"""Persistent models for workflow event routing and execution."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from django.db import models

from general_manager.workflow.engine import (
    ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES,
)

WorkflowJSONPayload = Mapping[str, object]


class WorkflowEventRecord(models.Model):
    """Durable workflow event payload routed through the event registry."""

    event_id: models.CharField[str] = models.CharField(max_length=128, unique=True)
    event_type: models.CharField[str] = models.CharField(max_length=255)
    event_name: models.CharField[str | None] = models.CharField(
        max_length=255, null=True, blank=True
    )
    source: models.CharField[str | None] = models.CharField(
        max_length=255, null=True, blank=True
    )
    occurred_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )
    payload: models.JSONField[WorkflowJSONPayload] = models.JSONField(default=dict)
    metadata: models.JSONField[WorkflowJSONPayload] = models.JSONField(default=dict)
    created_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        indexes = (
            models.Index(
                fields=["event_type"],
                name="general_man_event_t_55e4f2_idx",
            ),
            models.Index(
                fields=["event_name"],
                name="general_man_event_n_33eb24_idx",
            ),
            models.Index(
                fields=["created_at"],
                name="general_man_created_5b1ca2_idx",
            ),
        )


class WorkflowOutbox(models.Model):
    """Outbox entry used to claim, retry, and route workflow events."""

    STATUS_PENDING = "pending"
    STATUS_CLAIMED = "claimed"
    STATUS_PROCESSED = "processed"
    STATUS_FAILED = "failed"
    STATUS_DEAD_LETTER = "dead_letter"
    STATUSES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_CLAIMED, "Claimed"),
        (STATUS_PROCESSED, "Processed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_DEAD_LETTER, "Dead Letter"),
    )

    event: models.ForeignKey[WorkflowEventRecord] = models.ForeignKey(
        WorkflowEventRecord,
        on_delete=models.CASCADE,
        related_name="outbox_entries",
    )
    status: models.CharField[str] = models.CharField(
        max_length=24, choices=STATUSES, default=STATUS_PENDING
    )
    available_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now_add=True
    )
    claimed_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )
    claim_token: models.CharField[str | None] = models.CharField(
        max_length=64, null=True, blank=True
    )
    attempts: models.PositiveIntegerField[int] = models.PositiveIntegerField(default=0)
    last_error: models.TextField[str | None] = models.TextField(
        null=True, blank=True
    )
    created_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = (
            models.Index(
                fields=["status", "available_at"],
                name="general_man_status_180bed_idx",
            ),
            models.Index(
                fields=["status", "claimed_at"],
                name="workflow_ou_status__8b7f7b_idx",
            ),
            models.Index(
                fields=["status", "available_at", "id"],
                name="workflow_ou_status__a5f7dc_idx",
            ),
            models.Index(
                fields=["claim_token"],
                name="general_man_claim_t_78fd22_idx",
            ),
            models.Index(
                fields=["created_at"],
                name="general_man_created_073f4b_idx",
            ),
        )


class WorkflowExecutionRecord(models.Model):
    """
    Durable workflow execution state used by production workflow engines.

    The `state` column is stored as a raw database string. Workflow engines
    narrow it to the public `WorkflowState` vocabulary when returning
    `WorkflowExecution` DTOs or raising state-transition errors.
    """

    execution_id: models.CharField[str] = models.CharField(
        max_length=128, unique=True
    )
    workflow_id: models.CharField[str] = models.CharField(max_length=255)
    state: models.CharField[str] = models.CharField(max_length=32)
    input_data: models.JSONField[WorkflowJSONPayload] = models.JSONField(default=dict)
    output_data: models.JSONField[WorkflowJSONPayload | None] = models.JSONField(
        null=True, blank=True
    )
    correlation_id: models.CharField[str | None] = models.CharField(
        max_length=128, null=True, blank=True
    )
    started_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )
    ended_at: models.DateTimeField[datetime | None] = models.DateTimeField(
        null=True, blank=True
    )
    error: models.TextField[str | None] = models.TextField(null=True, blank=True)
    metadata: models.JSONField[WorkflowJSONPayload] = models.JSONField(default=dict)
    created_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("workflow_id", "correlation_id"),
                condition=models.Q(
                    correlation_id__isnull=False,
                )
                & ~models.Q(correlation_id="")
                & models.Q(state__in=ACTIVE_PLUS_COMPLETED_WORKFLOW_STATES),
                name="general_manager_workflow_exec_active_corr_uniq",
            ),
        )
        indexes = (
            models.Index(
                fields=["workflow_id", "state"],
                name="general_man_workflo_a2876f_idx",
            ),
            models.Index(
                fields=["correlation_id", "workflow_id"],
                name="general_man_correla_b2be1f_idx",
            ),
            models.Index(
                fields=["created_at"],
                name="general_man_created_4dbaac_idx",
            ),
        )


class WorkflowDeliveryAttempt(models.Model):
    """Per-handler delivery audit row keyed for idempotent event handling."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_DEAD_LETTER = "dead_letter"
    STATUSES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_DEAD_LETTER, "Dead Letter"),
    )

    event: models.ForeignKey[WorkflowEventRecord] = models.ForeignKey(
        WorkflowEventRecord,
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
    )
    handler_registration_id: models.CharField[str] = models.CharField(max_length=255)
    idempotency_key: models.CharField[str] = models.CharField(
        max_length=255, unique=True
    )
    status: models.CharField[str] = models.CharField(
        max_length=24, choices=STATUSES, default=STATUS_PENDING
    )
    attempts: models.PositiveIntegerField[int] = models.PositiveIntegerField(default=0)
    last_error: models.TextField[str | None] = models.TextField(
        null=True, blank=True
    )
    last_traceback: models.TextField[str | None] = models.TextField(
        null=True, blank=True
    )
    created_at: models.DateTimeField[datetime] = models.DateTimeField(
        auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime] = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("event", "handler_registration_id"),)
        indexes = (
            models.Index(
                fields=["status", "updated_at"],
                name="general_man_status_5f4aa1_idx",
            ),
            models.Index(
                fields=["handler_registration_id"],
                name="general_man_handler_f8368f_idx",
            ),
        )
