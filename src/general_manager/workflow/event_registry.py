"""Event registry interfaces for triggering workflow executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha1
from threading import Lock
from traceback import format_exc
from typing import Any, Callable, Mapping, Protocol, runtime_checkable
from uuid import uuid4

from django.db import IntegrityError, models, transaction
from django.utils.module_loading import import_string

from general_manager.logging import get_logger
from general_manager.workflow.config import (
    workflow_async_enabled,
    workflow_dead_letter_enabled,
    workflow_mode,
    workflow_outbox_batch_size,
    workflow_outbox_claim_ttl_seconds,
    workflow_max_retries,
    workflow_retry_backoff_seconds,
)


@dataclass(frozen=True)
class WorkflowEvent:
    """Canonical workflow trigger event."""

    event_id: str
    event_type: str
    payload: Mapping[str, Any]
    event_name: str | None = None
    source: str | None = None
    occurred_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


WorkflowEventHandler = Callable[[WorkflowEvent], None]
EventValidator = Callable[[WorkflowEvent], None]
EventPredicate = Callable[[WorkflowEvent], bool]
RetryPredicate = Callable[[Exception], bool]
DeadLetterHandler = Callable[[WorkflowEvent, Exception], None]


logger = get_logger("workflow.event_registry")
_SETTINGS_KEY = "GENERAL_MANAGER"
_EVENT_REGISTRY_KEY = "WORKFLOW_EVENT_REGISTRY"


@dataclass(frozen=True)
class _EventHandlerRegistration:
    event_key: str
    registration_id: str
    handler: WorkflowEventHandler
    validator: EventValidator | None = None
    when: EventPredicate | None = None
    retries: int = 0
    retry_on: RetryPredicate | None = None
    dead_letter_handler: DeadLetterHandler | None = None


def _callable_path(value: object) -> str:
    module = getattr(value, "__module__", "")
    qualname = getattr(value, "__qualname__", "")
    if module and qualname:
        return f"{module}.{qualname}"
    return repr(value)


def _registration_id(event: str, handler: WorkflowEventHandler) -> str:
    raw = f"{event}:{_callable_path(handler)}"
    return sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


@runtime_checkable
class EventRegistry(Protocol):
    """Registry that validates, deduplicates, and routes workflow events."""

    def register(
        self,
        event: str,
        *,
        handler: WorkflowEventHandler,
        validator: EventValidator | None = None,
        when: EventPredicate | None = None,
        retries: int = 0,
        retry_on: RetryPredicate | None = None,
        dead_letter_handler: DeadLetterHandler | None = None,
    ) -> None:
        """Register a handler for an event type or event name."""

    def publish(self, event: WorkflowEvent) -> bool:
        """Publish an event and return True when at least one handler ran."""


class _RoutingMixin:
    def __init__(self) -> None:
        self._handlers_by_type: dict[str, list[_EventHandlerRegistration]] = {}
        self._handlers_by_name: dict[str, list[_EventHandlerRegistration]] = {}
        self._lock = Lock()

    def register(
        self,
        event: str,
        *,
        handler: WorkflowEventHandler,
        validator: EventValidator | None = None,
        when: EventPredicate | None = None,
        retries: int = 0,
        retry_on: RetryPredicate | None = None,
        dead_letter_handler: DeadLetterHandler | None = None,
    ) -> None:
        registration = _EventHandlerRegistration(
            event_key=event,
            registration_id=_registration_id(event, handler),
            handler=handler,
            validator=validator,
            when=when,
            retries=max(0, retries),
            retry_on=retry_on,
            dead_letter_handler=dead_letter_handler,
        )
        with self._lock:
            if "." in event:
                self._handlers_by_type.setdefault(event, []).append(registration)
            else:
                self._handlers_by_name.setdefault(event, []).append(registration)

    def _get_entries(
        self, event: WorkflowEvent
    ) -> tuple[_EventHandlerRegistration, ...]:
        with self._lock:
            type_entries = tuple(self._handlers_by_type.get(event.event_type, ()))
            name_entries = tuple(self._handlers_by_name.get(event.event_name or "", ()))
        return (*type_entries, *name_entries)

    def _run_handler_with_retry(
        self,
        event: WorkflowEvent,
        entry: _EventHandlerRegistration,
        *,
        attempt_handler: Callable[
            [WorkflowEvent, _EventHandlerRegistration, int], bool
        ],
    ) -> bool:
        max_attempts = entry.retries + 1
        for attempt in range(1, max_attempts + 1):
            try:
                return attempt_handler(event, entry, attempt)
            except Exception as exc:  # noqa: BLE001
                should_retry = attempt < max_attempts and (
                    entry.retry_on(exc) if entry.retry_on is not None else True
                )
                if should_retry:
                    logger.warning(
                        "workflow event handler failed; retrying",
                        context={
                            "event_id": event.event_id,
                            "event_type": event.event_type,
                            "event_name": event.event_name,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                        },
                    )
                    continue
                self._send_to_dead_letter(event, exc, entry)
                return False
        return False

    def _send_to_dead_letter(
        self,
        event: WorkflowEvent,
        exc: Exception,
        entry: _EventHandlerRegistration,
    ) -> None:
        handler = entry.dead_letter_handler
        if handler is not None:
            handler(event, exc)
            return
        logger.exception(
            "workflow event handler failed",
            context={
                "event_id": event.event_id,
                "event_type": event.event_type,
                "event_name": event.event_name,
            },
        )


class InMemoryEventRegistry(_RoutingMixin):
    """Thread-safe in-memory event registry suitable for local development."""

    def __init__(
        self,
        *,
        dead_letter_handler: DeadLetterHandler | None = None,
    ) -> None:
        super().__init__()
        self._seen_event_ids: set[str] = set()
        self._dead_letter_handler = dead_letter_handler

    def publish(self, event: WorkflowEvent) -> bool:
        with self._lock:
            if event.event_id in self._seen_event_ids:
                return False
            self._seen_event_ids.add(event.event_id)

        handled = False
        for entry in self._get_entries(event):
            if entry.validator is not None:
                try:
                    entry.validator(event)
                except Exception as exc:  # noqa: BLE001
                    self._send_to_dead_letter(event, exc, entry)
                    continue
            if entry.when is not None and not entry.when(event):
                continue
            if self._run_handler_with_retry(
                event, entry, attempt_handler=self._execute_single
            ):
                handled = True
        return handled

    def _execute_single(
        self,
        event: WorkflowEvent,
        entry: _EventHandlerRegistration,
        _attempt: int,
    ) -> bool:
        del _attempt
        entry.handler(event)
        return True

    def _send_to_dead_letter(
        self,
        event: WorkflowEvent,
        exc: Exception,
        entry: _EventHandlerRegistration,
    ) -> None:
        handler = entry.dead_letter_handler or self._dead_letter_handler
        if handler is not None:
            handler(event, exc)
            return
        super()._send_to_dead_letter(event, exc, entry)


class DatabaseEventRegistry(_RoutingMixin):
    """DB-backed event registry for production event durability."""

    def publish(self, event: WorkflowEvent) -> bool:
        event_record = self._save_event(event)
        if event_record is None:
            return False
        from general_manager.workflow.models import WorkflowOutbox

        outbox = WorkflowOutbox.objects.create(event=event_record)
        if workflow_async_enabled():
            transaction.on_commit(self._enqueue_publish_task)
            return False
        return self.process_outbox_entry(int(outbox.pk))

    def publish_sync(self, event: WorkflowEvent) -> bool:
        return self._route_event(event)

    def process_outbox_entry(self, outbox_id: int) -> bool:
        from general_manager.workflow.models import WorkflowOutbox

        now = datetime.now(UTC)
        outbox = (
            WorkflowOutbox.objects.select_related("event").filter(id=outbox_id).first()
        )
        if outbox is None:
            return False
        if outbox.status == WorkflowOutbox.STATUS_PROCESSED:
            return False
        event = WorkflowEvent(
            event_id=outbox.event.event_id,
            event_type=outbox.event.event_type,
            event_name=outbox.event.event_name,
            payload=outbox.event.payload,
            source=outbox.event.source,
            occurred_at=outbox.event.occurred_at,
            metadata=outbox.event.metadata,
        )
        has_handlers = bool(self._get_entries(event))
        try:
            handled = self._route_event(event)
        except Exception as exc:  # noqa: BLE001
            was_claimed = outbox.status == WorkflowOutbox.STATUS_CLAIMED
            outbox.status = WorkflowOutbox.STATUS_FAILED
            if not was_claimed:
                outbox.attempts += 1
            outbox.last_error = str(exc)
            if (
                outbox.attempts >= workflow_max_retries()
                and workflow_dead_letter_enabled()
            ):
                outbox.status = WorkflowOutbox.STATUS_DEAD_LETTER
            delay_seconds = workflow_retry_backoff_seconds() * max(outbox.attempts, 1)
            outbox.available_at = now + timedelta(seconds=delay_seconds)
            outbox.save(
                update_fields=[
                    "status",
                    "attempts",
                    "last_error",
                    "available_at",
                    "updated_at",
                ]
            )
            return False
        if has_handlers and not handled:
            outbox.status = WorkflowOutbox.STATUS_FAILED
            outbox.last_error = "Workflow event handler did not complete successfully."
            if (
                outbox.attempts >= workflow_max_retries()
                and workflow_dead_letter_enabled()
            ):
                outbox.status = WorkflowOutbox.STATUS_DEAD_LETTER
            else:
                delay_seconds = workflow_retry_backoff_seconds() * max(
                    outbox.attempts, 1
                )
                outbox.available_at = now + timedelta(seconds=delay_seconds)
            outbox.save(
                update_fields=[
                    "status",
                    "last_error",
                    "available_at",
                    "updated_at",
                ]
            )
            return False
        outbox.status = WorkflowOutbox.STATUS_PROCESSED
        outbox.last_error = None
        outbox.claim_token = None
        outbox.claimed_at = None
        outbox.save(
            update_fields=[
                "status",
                "last_error",
                "claim_token",
                "claimed_at",
                "updated_at",
            ]
        )
        return handled

    def claim_outbox_batch(self, *, batch_size: int | None = None) -> list[int]:
        from django.db.models import F

        from general_manager.workflow.models import WorkflowOutbox

        size = batch_size or workflow_outbox_batch_size()
        now = datetime.now(UTC)
        stale_claim_before = now - timedelta(
            seconds=workflow_outbox_claim_ttl_seconds()
        )
        claim_token = uuid4().hex
        with transaction.atomic():
            rows = list(
                WorkflowOutbox.objects.select_for_update(skip_locked=True)
                .filter(
                    (
                        models.Q(
                            status__in=(
                                WorkflowOutbox.STATUS_PENDING,
                                WorkflowOutbox.STATUS_FAILED,
                            ),
                            available_at__lte=now,
                        )
                        | models.Q(
                            status=WorkflowOutbox.STATUS_CLAIMED,
                            claimed_at__lte=stale_claim_before,
                        )
                    ),
                )
                .order_by("available_at")[:size]
            )
            if not rows:
                return []
            ids = [int(row.pk) for row in rows]
            WorkflowOutbox.objects.filter(id__in=ids).update(
                status=WorkflowOutbox.STATUS_CLAIMED,
                claimed_at=now,
                claim_token=claim_token,
                attempts=F("attempts") + 1,
            )
            return ids

    def _save_event(self, event: WorkflowEvent):
        from general_manager.workflow.models import WorkflowEventRecord

        try:
            with transaction.atomic():
                return WorkflowEventRecord.objects.create(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    event_name=event.event_name,
                    source=event.source,
                    occurred_at=event.occurred_at,
                    payload=dict(event.payload),
                    metadata=dict(event.metadata),
                )
        except IntegrityError:
            return None

    def _route_event(self, event: WorkflowEvent) -> bool:
        handled = False
        for entry in self._get_entries(event):
            if entry.validator is not None:
                try:
                    entry.validator(event)
                except Exception as exc:  # noqa: BLE001
                    self._send_to_dead_letter(event, exc, entry)
                    continue
            if entry.when is not None and not entry.when(event):
                continue
            if self._run_handler_with_retry(
                event, entry, attempt_handler=self._execute_with_attempt_record
            ):
                handled = True
        return handled

    def _execute_with_attempt_record(
        self,
        event: WorkflowEvent,
        entry: _EventHandlerRegistration,
        attempt: int,
    ) -> bool:
        from general_manager.workflow.models import (
            WorkflowDeliveryAttempt,
            WorkflowEventRecord,
        )

        event_record = WorkflowEventRecord.objects.filter(
            event_id=event.event_id
        ).first()
        if event_record is None:
            return False
        idempotency_key = f"{event.event_id}:{entry.registration_id}"
        attempt_record, created = WorkflowDeliveryAttempt.objects.get_or_create(
            idempotency_key=idempotency_key,
            defaults={
                "event": event_record,
                "handler_registration_id": entry.registration_id,
                "status": WorkflowDeliveryAttempt.STATUS_PENDING,
            },
        )
        if (
            not created
            and attempt_record.status == WorkflowDeliveryAttempt.STATUS_COMPLETED
        ):
            return True
        attempt_record.status = WorkflowDeliveryAttempt.STATUS_RUNNING
        attempt_record.attempts = max(attempt_record.attempts, attempt)
        attempt_record.save(update_fields=["status", "attempts", "updated_at"])
        try:
            entry.handler(event)
        except Exception as exc:
            attempt_record.status = (
                WorkflowDeliveryAttempt.STATUS_DEAD_LETTER
                if attempt > entry.retries and workflow_dead_letter_enabled()
                else WorkflowDeliveryAttempt.STATUS_FAILED
            )
            attempt_record.last_error = str(exc)
            attempt_record.last_traceback = format_exc()
            attempt_record.save(
                update_fields=["status", "last_error", "last_traceback", "updated_at"]
            )
            raise
        attempt_record.status = WorkflowDeliveryAttempt.STATUS_COMPLETED
        attempt_record.last_error = None
        attempt_record.last_traceback = None
        attempt_record.save(
            update_fields=["status", "last_error", "last_traceback", "updated_at"]
        )
        return True

    @staticmethod
    def _enqueue_publish_task() -> None:
        from general_manager.workflow.tasks import publish_outbox_batch

        publish_outbox_batch.delay()


def _resolve_registry(value: Any) -> EventRegistry | None:
    if value is None:
        return None
    if isinstance(value, str):
        resolved = import_string(value)
    elif isinstance(value, Mapping):
        class_path = value.get("class")
        options = value.get("options", {})
        if class_path is None:
            return None
        resolved = (
            import_string(class_path) if isinstance(class_path, str) else class_path
        )
        if isinstance(resolved, type):
            return resolved(**options)
        if callable(resolved):
            return resolved(**options)
        return None
    else:
        resolved = value
    if isinstance(resolved, type):
        return resolved()
    if callable(resolved):
        return resolved()
    return resolved  # type: ignore[return-value]


_event_registry: EventRegistry = InMemoryEventRegistry()


def configure_event_registry(registry: EventRegistry) -> None:
    """Set the active workflow event registry."""
    global _event_registry
    _event_registry = registry


def configure_event_registry_from_settings(django_settings: Any) -> None:
    """Configure event registry from Django settings."""
    config = getattr(django_settings, _SETTINGS_KEY, {})
    setting: Any = None
    if isinstance(config, Mapping):
        setting = config.get(_EVENT_REGISTRY_KEY)
    if setting is None:
        setting = getattr(django_settings, _EVENT_REGISTRY_KEY, None)
    registry = _resolve_registry(setting)
    if registry is not None:
        configure_event_registry(registry)
        return
    if workflow_mode(django_settings) == "production":
        configure_event_registry(DatabaseEventRegistry())
        return
    configure_event_registry(InMemoryEventRegistry())


def get_event_registry() -> EventRegistry:
    """Return the active workflow event registry."""
    return _event_registry


def publish_sync(event: WorkflowEvent) -> bool:
    """Publish an event synchronously against the configured registry."""
    registry = get_event_registry()
    method = getattr(registry, "publish_sync", None)
    if callable(method):
        return bool(method(event))
    return registry.publish(event)
