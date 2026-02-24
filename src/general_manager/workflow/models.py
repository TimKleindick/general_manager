"""Persistent models for workflow event routing and execution."""

from __future__ import annotations

from typing import Any

from django.db import models


class WorkflowEventRecord(models.Model):
    """Stored workflow event payload."""

    event_id: Any = models.CharField(max_length=128, unique=True)
    event_type: Any = models.CharField(max_length=255)
    event_name: Any = models.CharField(max_length=255, null=True, blank=True)
    source: Any = models.CharField(max_length=255, null=True, blank=True)
    occurred_at: Any = models.DateTimeField(null=True, blank=True)
    payload: Any = models.JSONField(default=dict)
    metadata: Any = models.JSONField(default=dict)
    created_at: Any = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = (
            models.Index(fields=["event_type"]),
            models.Index(fields=["event_name"]),
            models.Index(fields=["created_at"]),
        )


class WorkflowOutbox(models.Model):
    """Outbox entry used to route events asynchronously."""

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

    event: Any = models.ForeignKey(
        WorkflowEventRecord,
        on_delete=models.CASCADE,
        related_name="outbox_entries",
    )
    status: Any = models.CharField(
        max_length=24, choices=STATUSES, default=STATUS_PENDING
    )
    available_at: Any = models.DateTimeField(auto_now_add=True)
    claimed_at: Any = models.DateTimeField(null=True, blank=True)
    claim_token: Any = models.CharField(max_length=64, null=True, blank=True)
    attempts: Any = models.PositiveIntegerField(default=0)
    last_error: Any = models.TextField(null=True, blank=True)
    created_at: Any = models.DateTimeField(auto_now_add=True)
    updated_at: Any = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = (
            models.Index(fields=["status", "available_at"]),
            models.Index(fields=["claim_token"]),
            models.Index(fields=["created_at"]),
        )


class WorkflowExecutionRecord(models.Model):
    """Durable workflow execution record."""

    execution_id: Any = models.CharField(max_length=128, unique=True)
    workflow_id: Any = models.CharField(max_length=255)
    state: Any = models.CharField(max_length=32)
    input_data: Any = models.JSONField(default=dict)
    output_data: Any = models.JSONField(null=True, blank=True)
    correlation_id: Any = models.CharField(max_length=128, null=True, blank=True)
    started_at: Any = models.DateTimeField(null=True, blank=True)
    ended_at: Any = models.DateTimeField(null=True, blank=True)
    error: Any = models.TextField(null=True, blank=True)
    metadata: Any = models.JSONField(default=dict)
    created_at: Any = models.DateTimeField(auto_now_add=True)
    updated_at: Any = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = (
            models.Index(fields=["workflow_id", "state"]),
            models.Index(fields=["correlation_id", "workflow_id"]),
            models.Index(fields=["created_at"]),
        )


class WorkflowDeliveryAttempt(models.Model):
    """Per-handler delivery audit with idempotency key."""

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

    event: Any = models.ForeignKey(
        WorkflowEventRecord,
        on_delete=models.CASCADE,
        related_name="delivery_attempts",
    )
    handler_registration_id: Any = models.CharField(max_length=255)
    idempotency_key: Any = models.CharField(max_length=255, unique=True)
    status: Any = models.CharField(
        max_length=24, choices=STATUSES, default=STATUS_PENDING
    )
    attempts: Any = models.PositiveIntegerField(default=0)
    last_error: Any = models.TextField(null=True, blank=True)
    last_traceback: Any = models.TextField(null=True, blank=True)
    created_at: Any = models.DateTimeField(auto_now_add=True)
    updated_at: Any = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("event", "handler_registration_id"),)
        indexes = (
            models.Index(fields=["status", "updated_at"]),
            models.Index(fields=["handler_registration_id"]),
        )
