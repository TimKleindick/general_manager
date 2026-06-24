"""Event registry interfaces for triggering workflow executions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha1
from time import perf_counter
from collections import deque
from collections.abc import Callable, Mapping
from threading import Lock
from traceback import format_exc
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable
from uuid import uuid4

from django.db import IntegrityError, models, transaction
from django.db.models import Min
from django.utils.module_loading import import_string

from general_manager.logging import get_logger
from general_manager.workflow.config import (
    workflow_async_enabled,
    workflow_delivery_running_timeout_seconds,
    workflow_dead_letter_enabled,
    workflow_mode,
    workflow_outbox_batch_size,
    workflow_outbox_claim_ttl_seconds,
    workflow_max_retries,
    workflow_retry_backoff_seconds,
)
from general_manager.workflow.telemetry import (
    increment_delivery_attempt,
    increment_duplicate_suppression,
    increment_outbox_status,
    observe_outbox_claim_batch,
    observe_outbox_process_duration,
)

if TYPE_CHECKING:
    from general_manager.workflow.models import WorkflowEventRecord


type WorkflowEventPayload = Mapping[str, object]


@dataclass(frozen=True)
class WorkflowEvent:
    """
    Canonical workflow trigger event routed by workflow event registries.

    Attributes:
        event_id: Stable idempotency key for duplicate suppression.
        event_type: Canonical dotted type such as
            `"general_manager.manager.updated"`.
        payload: Event-specific payload stored in memory or serialized to the
            database-backed event record.
        event_name: Optional readable routing name such as
            `"project_status_changed"`.
        source: Optional origin label for observability and auditing.
        occurred_at: Optional timestamp for when the domain event occurred.
        metadata: Additional event metadata stored alongside the payload.

    The dataclass is frozen but does not validate field values at runtime.
    Empty strings and caller-supplied mutable mappings are accepted by the
    constructor. In-memory registries pass mappings through to handlers as-is;
    database registries snapshot them with `dict(...)` and rely on Django model
    fields for JSON/date serialization validation.
    """

    event_id: str
    event_type: str
    payload: WorkflowEventPayload
    event_name: str | None = None
    source: str | None = None
    occurred_at: datetime | None = None
    metadata: WorkflowEventPayload = field(default_factory=dict)


WorkflowEventHandler = Callable[[WorkflowEvent], None]
EventValidator = Callable[[WorkflowEvent], None]
EventPredicate = Callable[[WorkflowEvent], bool]
RetryPredicate = Callable[[Exception], bool]
DeadLetterHandler = Callable[[WorkflowEvent, Exception], None]


logger = get_logger("workflow.event_registry")
_SETTINGS_KEY = "GENERAL_MANAGER"
_EVENT_REGISTRY_KEY = "WORKFLOW_EVENT_REGISTRY"


class InvalidWorkflowEventRegistryOptionsError(TypeError):
    """Raised when WORKFLOW_EVENT_REGISTRY mapping options are not a mapping."""

    def __init__(self) -> None:
        super().__init__("WORKFLOW_EVENT_REGISTRY options must be a mapping.")


class InvalidWorkflowEventRegistryError(TypeError):
    """Raised when a workflow event registry setting cannot resolve to a registry."""

    def __init__(self, registry_setting: object) -> None:
        super().__init__(
            "Workflow event registry setting did not resolve to an EventRegistry: "
            f"{registry_setting!r}"
        )


class _DeliveryInProgressError(RuntimeError):
    """Raised when another worker owns an in-flight delivery attempt."""

    def __init__(self, attempt_id: int) -> None:
        self.attempt_id = attempt_id
        super().__init__(f"Delivery attempt {attempt_id} is already running.")


@dataclass(frozen=True)
class _RoutingOutcome:
    handled: bool
    had_applicable_handlers: bool


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
    if not isinstance(module, str):
        module = ""
    qualname = getattr(value, "__qualname__", "")
    if not isinstance(qualname, str):
        qualname = ""
    if module and qualname:
        return f"{module}.{qualname}"
    return repr(value)


def _registration_id(
    event: str,
    handler: WorkflowEventHandler,
    *,
    validator: EventValidator | None = None,
    when: EventPredicate | None = None,
    retries: int = 0,
    retry_on: RetryPredicate | None = None,
    dead_letter_handler: DeadLetterHandler | None = None,
) -> str:
    raw = (
        f"{event}:{_callable_path(handler)}:{_callable_path(validator)}:"
        f"{_callable_path(when)}:{retries}:{_callable_path(retry_on)}:"
        f"{_callable_path(dead_letter_handler)}"
    )
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
        """
        Register a handler for an event type or event name.

        `event` values containing a dot route by `WorkflowEvent.event_type`;
        values without a dot route by `WorkflowEvent.event_name`. `validator`
        runs before `when`; validator errors and final handler failures are sent
        to the registration's dead-letter handler when configured. `retries`
        counts additional attempts after the first and is clamped to zero when
        negative.

        The registry trusts the annotated input types. It does not validate
        non-callable handlers, validators, predicates, retry predicates, or
        dead-letter handlers at registration time; invalid callables fail when
        the route is evaluated. Non-string event keys or non-integer retry values
        may raise normal Python errors during registration.

        Route keys are matched by exact string equality; they are not stripped,
        normalized, wildcarded, or prefix-matched. Empty strings are valid keys
        and match events whose `event_name` is `None` or `""`. A readable
        `event_name` containing a dot is treated as a type route key, so prefer
        dot-free event names.

        Identical registrations are ignored rather than appended. Registration
        identity is derived from the event key, handler, validator, predicate,
        retry count, retry predicate, and dead-letter handler. Different routing
        options produce separate registrations and may invoke the same handler
        more than once. Callable identity uses each callable's
        `__module__ + "." + __qualname__` when available and `repr(...)`
        otherwise; retry count identity uses the clamped retry count.

        Matching handlers run in registration order within each route bucket.
        Type-route registrations run before name-route registrations when an
        event matches both. A registration becomes applicable only after its
        validator succeeds and its `when` predicate is absent or returns `True`;
        `when=False` registrations are skipped and do not affect `publish()`
        return values.

        `retry_on=None` retries every handler exception until the attempt budget
        is exhausted. Validator failures are not retried. `when` predicate
        exceptions and dead-letter handler exceptions are not wrapped by the
        in-memory registry; database outbox processing records them as outbox
        failures. If a `retry_on` predicate raises, that exception propagates
        the same way and is not converted into a retry decision.
        """

    def publish(self, event: WorkflowEvent) -> bool:
        """
        Publish an event and return whether at least one handler completed.

        Implementations may suppress duplicate `event.event_id` values and may
        persist work before handlers run. A duplicate publish returns `False`.
        If at least one applicable handler completes and another fails,
        synchronous registries return `True`.
        """


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
        retry_count = max(0, retries)
        registration = _EventHandlerRegistration(
            event_key=event,
            registration_id=_registration_id(
                event,
                handler,
                validator=validator,
                when=when,
                retries=retry_count,
                retry_on=retry_on,
                dead_letter_handler=dead_letter_handler,
            ),
            handler=handler,
            validator=validator,
            when=when,
            retries=retry_count,
            retry_on=retry_on,
            dead_letter_handler=dead_letter_handler,
        )
        with self._lock:
            if "." in event:
                bucket = self._handlers_by_type.setdefault(event, [])
            else:
                bucket = self._handlers_by_name.setdefault(event, [])
            if any(
                existing.registration_id == registration.registration_id
                for existing in bucket
            ):
                return
            bucket.append(registration)

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
            except _DeliveryInProgressError:
                raise
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

    def _route_event(self, event: WorkflowEvent) -> _RoutingOutcome:
        handled = False
        had_applicable_handlers = False
        for entry in self._get_entries(event):
            if entry.validator is not None:
                try:
                    entry.validator(event)
                except Exception as exc:  # noqa: BLE001
                    self._send_to_dead_letter(event, exc, entry)
                    continue
            if entry.when is not None and not entry.when(event):
                continue
            had_applicable_handlers = True
            if self._run_handler_with_retry(
                event, entry, attempt_handler=self._attempt_handler
            ):
                handled = True
        return _RoutingOutcome(
            handled=handled,
            had_applicable_handlers=had_applicable_handlers,
        )

    def _attempt_handler(
        self,
        event: WorkflowEvent,
        entry: _EventHandlerRegistration,
        attempt: int,
    ) -> bool:
        raise NotImplementedError

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
    """
    Thread-safe in-memory event registry suitable for local development.

    The registry deduplicates published events by `event_id` using a bounded
    process-local cache. It routes matching handlers synchronously and isolates
    handler failures so one failing route does not stop later routes.

    `max_seen_event_ids` is clamped to at least `1`; when the cache is full, the
    oldest id is evicted. The id is marked seen before routing, so a handler
    failure does not make the event id publishable again. The dataclass is
    frozen, but `payload` and `metadata` may still reference mutable mappings.

    Registration-level dead-letter handlers take precedence over the
    registry-level `dead_letter_handler`; the registry-level handler is used only
    when a failed registration does not define one.

    The internal lock protects registration mutation, handler snapshot creation,
    and seen-id cache updates. Handlers run outside the lock. Concurrent
    `publish()` calls use the handler snapshot available when routing starts;
    concurrent `register()` calls may affect later publishes but not a snapshot
    already being routed.
    """

    def __init__(
        self,
        *,
        dead_letter_handler: DeadLetterHandler | None = None,
        max_seen_event_ids: int = 100_000,
    ) -> None:
        super().__init__()
        self._seen_event_ids: set[str] = set()
        self._seen_event_order: deque[str] = deque()
        self._max_seen_event_ids = max(1, max_seen_event_ids)
        self._dead_letter_handler = dead_letter_handler

    def publish(self, event: WorkflowEvent) -> bool:
        with self._lock:
            if event.event_id in self._seen_event_ids:
                return False
            if len(self._seen_event_order) >= self._max_seen_event_ids:
                evicted = self._seen_event_order.popleft()
                self._seen_event_ids.discard(evicted)
            self._seen_event_ids.add(event.event_id)
            self._seen_event_order.append(event.event_id)

        outcome = self._route_event(event)
        return outcome.handled

    def _attempt_handler(
        self,
        event: WorkflowEvent,
        entry: _EventHandlerRegistration,
        attempt: int,
    ) -> bool:
        del attempt
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
    """
    DB-backed event registry for production event durability.

    `publish()` persists a `WorkflowEventRecord` and `WorkflowOutbox` row. In
    async mode it schedules outbox processing after commit and returns `False`
    because handlers have not run yet; otherwise it processes the outbox row
    synchronously. Duplicate `event_id` values are suppressed by the event
    record uniqueness constraint; duplicate `publish()` calls return `False`
    without creating or routing a new outbox row. Database registries do not have a
    registry-level dead-letter handler; use registration-level handlers for
    custom dead-letter handling. The class accepts no constructor options.

    Payload and metadata values are handed to Django's `JSONField`; they must be
    serializable by the configured Django/database JSON handling. `occurred_at`
    is handed to Django's `DateTimeField` without conversion in this layer, so
    timezone behavior follows the project's Django settings and database backend.
    """

    def publish_sync(self, event: WorkflowEvent) -> bool:
        """
        Persist `event` if possible and route matching handlers inline.

        A duplicate `event_id` is not inserted again, but inline routing still
        runs against the provided event object. Non-duplicate persistence errors
        propagate and prevent routing. Successful persistence commits before
        inline routing begins. Returns `True` when at least one handler
        completes.
        """
        self._save_event(event)
        return self._route_event(event).handled

    def publish(self, event: WorkflowEvent) -> bool:
        from general_manager.workflow.models import WorkflowEventRecord, WorkflowOutbox

        try:
            with transaction.atomic():
                event_record = WorkflowEventRecord.objects.create(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    event_name=event.event_name,
                    source=event.source,
                    occurred_at=event.occurred_at,
                    payload=dict(event.payload),
                    metadata=dict(event.metadata),
                )
                outbox = WorkflowOutbox.objects.create(event=event_record)
        except IntegrityError:
            return False
        if workflow_async_enabled():
            transaction.on_commit(self._enqueue_publish_task)
            return False
        return self.process_outbox_entry(int(outbox.pk))

    def process_outbox_entry(
        self, outbox_id: int, *, claim_token: str | None = None
    ) -> bool:
        """
        Route one outbox row and finalize its delivery state.

        Returns `False` for missing or already processed rows, stale or missing
        ownership claims, duplicate in-progress delivery attempts, handler
        failures, and finalize failures. Rows with only filtered-out handlers are
        marked processed and return `False`. Successful handler completion marks
        the row processed and returns `True`.

        Handler failure increments the outbox attempt count. While attempts are
        below `WORKFLOW_MAX_RETRIES`, the row remains failed and becomes due
        again at `now + WORKFLOW_RETRY_BACKOFF_SECONDS * attempts`. When attempts
        reach `WORKFLOW_MAX_RETRIES` and dead letters are enabled, the row moves
        to `dead_letter`. Handler-level `register(..., retries=...)` controls
        delivery attempts inside one outbox processing call; outbox attempts
        control later processing calls. If at least one applicable handler
        succeeds and another handler fails during the same processing call, the
        failed handler still receives dead-letter handling but the outbox row is
        marked processed because the event had a successful route.

        Dead letters are controlled by `WORKFLOW_DEAD_LETTER_ENABLED`, which
        defaults to `True` and is interpreted with `bool(...)`. When disabled,
        rows that reach `WORKFLOW_MAX_RETRIES` remain failed and are scheduled
        again with backoff instead of moving to `dead_letter`.
        `WORKFLOW_MAX_RETRIES` is parsed with `int(...)`, clamped to at least
        `0`, defaults to `3` on missing or invalid values, and counts total
        failed outbox processing calls before dead-letter transition.
        `WORKFLOW_RETRY_BACKOFF_SECONDS` is parsed with `int(...)`, clamped to
        at least `1`, and defaults to `5` on missing or invalid values.
        """
        from general_manager.workflow.models import WorkflowOutbox

        started = perf_counter()
        with transaction.atomic():
            outbox = (
                WorkflowOutbox.objects.select_for_update()
                .select_related("event")
                .filter(id=outbox_id)
                .first()
            )
            if outbox is None:
                return False
            if outbox.status == WorkflowOutbox.STATUS_PROCESSED:
                return False
            if claim_token is not None:
                if (
                    outbox.status != WorkflowOutbox.STATUS_CLAIMED
                    or outbox.claim_token != claim_token
                ):
                    logger.info(
                        "workflow outbox ownership check failed",
                        context={
                            "outbox_id": outbox_id,
                            "claim_token": claim_token,
                            "actual_claim_token": outbox.claim_token,
                            "status": outbox.status,
                            "event_id": outbox.event.event_id,
                        },
                    )
                    return False
            elif outbox.status == WorkflowOutbox.STATUS_CLAIMED:
                logger.info(
                    "workflow outbox claimed by another worker",
                    context={
                        "outbox_id": outbox_id,
                        "actual_claim_token": outbox.claim_token,
                        "status": outbox.status,
                        "event_id": outbox.event.event_id,
                    },
                )
                return False
            was_claimed = outbox.status == WorkflowOutbox.STATUS_CLAIMED
            expected_claim_token = outbox.claim_token
            event = WorkflowEvent(
                event_id=outbox.event.event_id,
                event_type=outbox.event.event_type,
                event_name=outbox.event.event_name,
                payload=outbox.event.payload,
                source=outbox.event.source,
                occurred_at=outbox.event.occurred_at,
                metadata=outbox.event.metadata,
            )
        try:
            outcome = self._route_event(event)
        except _DeliveryInProgressError as exc:
            logger.info(
                "workflow delivery suppressed due to in-progress attempt",
                context={
                    "event_id": event.event_id,
                    "outbox_id": outbox_id,
                    "claim_token": expected_claim_token,
                    "attempt_id": exc.attempt_id,
                },
            )
            observe_outbox_process_duration(
                status=WorkflowOutbox.STATUS_CLAIMED,
                duration_seconds=perf_counter() - started,
            )
            return False
        except Exception as exc:
            logger.exception(
                "workflow outbox processing failed",
                context={
                    "event_id": event.event_id,
                    "outbox_id": outbox_id,
                    "claim_token": expected_claim_token,
                },
            )
            status = self._finalize_outbox_failure(
                outbox_id=outbox_id,
                claim_token=expected_claim_token,
                was_claimed=was_claimed,
                error=str(exc),
            )
            observe_outbox_process_duration(
                status=status,
                duration_seconds=perf_counter() - started,
            )
            return False
        if outcome.had_applicable_handlers and not outcome.handled:
            logger.warning(
                "workflow handlers did not complete successfully",
                context={
                    "event_id": event.event_id,
                    "outbox_id": outbox_id,
                    "claim_token": expected_claim_token,
                },
            )
            status = self._finalize_outbox_failure(
                outbox_id=outbox_id,
                claim_token=expected_claim_token,
                was_claimed=was_claimed,
                error="Workflow event handler did not complete successfully.",
            )
            observe_outbox_process_duration(
                status=status,
                duration_seconds=perf_counter() - started,
            )
            return False
        finalized = self._finalize_outbox_processed(
            outbox_id=outbox_id,
            claim_token=expected_claim_token,
            was_claimed=was_claimed,
        )
        if not finalized:
            observe_outbox_process_duration(
                status=WorkflowOutbox.STATUS_FAILED,
                duration_seconds=perf_counter() - started,
            )
            return False
        observe_outbox_process_duration(
            status=WorkflowOutbox.STATUS_PROCESSED,
            duration_seconds=perf_counter() - started,
        )
        return outcome.handled

    def claim_outbox_batch(
        self, *, batch_size: int | None = None
    ) -> list[tuple[int, str]]:
        """
        Claim available outbox rows for async processing.

        The method claims pending/failed rows whose `available_at` is due and
        stale claimed rows whose claim TTL has expired, using row locks and one
        fresh claim token for the returned batch. `batch_size=None` uses the
        configured outbox batch size. Returns `(outbox_id, claim_token)` pairs.
        """
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
            )
            claims = [(outbox_id, claim_token) for outbox_id in ids]
            observe_outbox_claim_batch(len(claims))
            return claims

    def _save_event(self, event: WorkflowEvent) -> WorkflowEventRecord | None:
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

    def _attempt_handler(
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
        with transaction.atomic():
            attempt_record, _created = WorkflowDeliveryAttempt.objects.get_or_create(
                idempotency_key=idempotency_key,
                defaults={
                    "event": event_record,
                    "handler_registration_id": entry.registration_id,
                    "status": WorkflowDeliveryAttempt.STATUS_PENDING,
                },
            )
            attempt_record = WorkflowDeliveryAttempt.objects.select_for_update().get(
                pk=attempt_record.pk
            )
            if attempt_record.status == WorkflowDeliveryAttempt.STATUS_COMPLETED:
                return True
            running_ttl = timedelta(seconds=workflow_delivery_running_timeout_seconds())
            stale_before = datetime.now(UTC) - running_ttl
            if (
                attempt_record.status == WorkflowDeliveryAttempt.STATUS_RUNNING
                and attempt_record.updated_at > stale_before
            ):
                increment_duplicate_suppression()
                logger.info(
                    "workflow delivery attempt already running",
                    context={
                        "event_id": event.event_id,
                        "registration_id": entry.registration_id,
                        "idempotency_key": idempotency_key,
                        "attempt_id": attempt_record.pk,
                    },
                )
                raise _DeliveryInProgressError(attempt_record.pk)
            attempt_record.status = WorkflowDeliveryAttempt.STATUS_RUNNING
            attempt_record.attempts = max(attempt_record.attempts, attempt)
            attempt_record.save(update_fields=["status", "attempts", "updated_at"])
            increment_delivery_attempt(status=WorkflowDeliveryAttempt.STATUS_RUNNING)
        try:
            entry.handler(event)
        except Exception as exc:
            status = (
                WorkflowDeliveryAttempt.STATUS_DEAD_LETTER
                if attempt > entry.retries and workflow_dead_letter_enabled()
                else WorkflowDeliveryAttempt.STATUS_FAILED
            )
            WorkflowDeliveryAttempt.objects.filter(
                pk=attempt_record.pk,
                status=WorkflowDeliveryAttempt.STATUS_RUNNING,
            ).update(
                status=status,
                last_error=str(exc),
                last_traceback=format_exc(),
            )
            increment_delivery_attempt(status=status)
            raise
        WorkflowDeliveryAttempt.objects.filter(
            pk=attempt_record.pk,
            status=WorkflowDeliveryAttempt.STATUS_RUNNING,
        ).update(
            status=WorkflowDeliveryAttempt.STATUS_COMPLETED,
            last_error=None,
            last_traceback=None,
        )
        increment_delivery_attempt(status=WorkflowDeliveryAttempt.STATUS_COMPLETED)
        return True

    @staticmethod
    def _enqueue_publish_task() -> None:
        from general_manager.workflow.tasks import publish_outbox_batch

        delay = getattr(publish_outbox_batch, "delay", None)
        if callable(delay):
            delay()
            return
        publish_outbox_batch()

    def _finalize_outbox_failure(
        self,
        *,
        outbox_id: int,
        claim_token: str | None,
        was_claimed: bool,
        error: str,
    ) -> str:
        from general_manager.workflow.models import WorkflowOutbox

        with transaction.atomic():
            outbox = (
                WorkflowOutbox.objects.select_for_update().filter(id=outbox_id).first()
            )
            if outbox is None:
                return str(WorkflowOutbox.STATUS_FAILED)
            if was_claimed:
                if (
                    outbox.status != WorkflowOutbox.STATUS_CLAIMED
                    or outbox.claim_token != claim_token
                ):
                    return str(outbox.status)
            elif outbox.status not in (
                WorkflowOutbox.STATUS_PENDING,
                WorkflowOutbox.STATUS_FAILED,
            ):
                return str(outbox.status)
            outbox.status = WorkflowOutbox.STATUS_FAILED
            outbox.attempts += 1
            outbox.last_error = error
            outbox.claim_token = None
            outbox.claimed_at = None
            if (
                outbox.attempts >= workflow_max_retries()
                and workflow_dead_letter_enabled()
            ):
                outbox.status = WorkflowOutbox.STATUS_DEAD_LETTER
            else:
                delay_seconds = workflow_retry_backoff_seconds() * max(
                    outbox.attempts, 1
                )
                outbox.available_at = datetime.now(UTC) + timedelta(
                    seconds=delay_seconds
                )
            outbox.save(
                update_fields=[
                    "status",
                    "attempts",
                    "last_error",
                    "claim_token",
                    "claimed_at",
                    "available_at",
                    "updated_at",
                ]
            )
            status = str(outbox.status)
            increment_outbox_status(status)
            return status

    def _finalize_outbox_processed(
        self,
        *,
        outbox_id: int,
        claim_token: str | None,
        was_claimed: bool,
    ) -> bool:
        from general_manager.workflow.models import WorkflowOutbox

        with transaction.atomic():
            outbox = (
                WorkflowOutbox.objects.select_for_update().filter(id=outbox_id).first()
            )
            if outbox is None:
                return False
            if was_claimed:
                if (
                    outbox.status != WorkflowOutbox.STATUS_CLAIMED
                    or outbox.claim_token != claim_token
                ):
                    return False
            elif outbox.status not in (
                WorkflowOutbox.STATUS_PENDING,
                WorkflowOutbox.STATUS_FAILED,
            ):
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
            increment_outbox_status(outbox.status)
            return True

    def outbox_snapshot(self) -> tuple[int, float]:
        """
        Return `(pending_count, oldest_pending_age_seconds)` for operations UI.

        The age is `0.0` when no pending rows exist or when clock skew would
        otherwise produce a negative age.
        """
        from general_manager.workflow.models import WorkflowOutbox

        now = datetime.now(UTC)
        pending_qs = WorkflowOutbox.objects.filter(status=WorkflowOutbox.STATUS_PENDING)
        pending_count = int(pending_qs.count())
        oldest_pending = pending_qs.aggregate(oldest=Min("created_at"))["oldest"]
        oldest_age = 0.0
        if oldest_pending is not None:
            oldest_age = max(0.0, (now - oldest_pending).total_seconds())
        return pending_count, oldest_age


def _instantiate_registry_reference(
    value: object,
    options: Mapping[str, object] | None = None,
) -> object:
    """Instantiate a registry class or factory while preserving registry instances."""
    if isinstance(value, type):
        factory = cast(Callable[..., object], value)
        return factory(**dict(options or {}))
    if callable(value) and not isinstance(value, EventRegistry):
        factory = cast(Callable[..., object], value)
        return factory(**dict(options or {}))
    return value


def _resolve_registry(value: object) -> EventRegistry | None:
    """Resolve workflow event registry settings values into a registry instance."""
    if value is None:
        return None
    if isinstance(value, str):
        resolved: object = import_string(value)
    elif isinstance(value, Mapping):
        config = cast(Mapping[str, object], value)
        registry_reference = config.get("class")
        options_value = config.get("options", {})
        if registry_reference is None:
            return None
        if options_value is None:
            options: Mapping[str, object] = {}
        elif isinstance(options_value, Mapping):
            options = cast(Mapping[str, object], options_value)
        else:
            raise InvalidWorkflowEventRegistryOptionsError
        resolved_reference = (
            import_string(registry_reference)
            if isinstance(registry_reference, str)
            else registry_reference
        )
        resolved = _instantiate_registry_reference(resolved_reference, options)
    else:
        resolved = value

    resolved = _instantiate_registry_reference(resolved)
    return resolved if isinstance(resolved, EventRegistry) else None


_event_registry: EventRegistry = InMemoryEventRegistry()


def configure_event_registry(registry: EventRegistry) -> None:
    """
    Set the process-local active workflow event registry.

    Parameters:
        registry: Registry instance used by `get_event_registry()` and
            `publish_sync()` until it is replaced. The function trusts the type
            hint and does not perform a runtime `EventRegistry` validation.
    """
    global _event_registry
    _event_registry = registry


def configure_event_registry_from_settings(django_settings: object) -> None:
    """
    Configure the workflow event registry from Django settings.

    `GENERAL_MANAGER["WORKFLOW_EVENT_REGISTRY"]` takes precedence over a
    top-level `WORKFLOW_EVENT_REGISTRY` setting, including explicit `None` to
    use the `WORKFLOW_MODE` default registry. Values may be:

    - `None` or missing to use the mode default registry.
    - An `EventRegistry` instance.
    - A dotted import path to an `EventRegistry` instance, class, or factory.
    - A zero-argument callable returning an `EventRegistry`.
    - A mapping with `{"class": <path-or-callable>, "options": {...}}`; options
      are passed as keyword arguments when constructing/calling the reference.
      Other mapping keys are ignored and options are not merged with any other
      settings.

    `WORKFLOW_MODE` values are normalized by stripping whitespace and
    lowercasing. `"production"` selects `DatabaseEventRegistry`; `"local"` and
    any unrecognized value select `InMemoryEventRegistry`.

    Import, factory, and constructor exceptions propagate.

    Dotted import strings are resolved before classification. Imported classes
    are instantiated, imported registry instances are reused, and imported
    callables that are not already `EventRegistry` instances are called as
    factories.

    Raises:
        TypeError: If mapping `options` is not a mapping, or if a non-`None`
            setting cannot be resolved to an `EventRegistry`.
    """
    config_candidate: object = getattr(django_settings, _SETTINGS_KEY, None)
    setting: object = None
    if isinstance(config_candidate, Mapping):
        config = cast(Mapping[str, object], config_candidate)
        if _EVENT_REGISTRY_KEY in config:
            setting = config[_EVENT_REGISTRY_KEY]
        else:
            setting = getattr(django_settings, _EVENT_REGISTRY_KEY, None)
    else:
        setting = getattr(django_settings, _EVENT_REGISTRY_KEY, None)
    registry = _resolve_registry(setting)
    if setting is not None and registry is None:
        raise InvalidWorkflowEventRegistryError(setting)
    if registry is not None:
        configure_event_registry(registry)
        return
    if workflow_mode(django_settings) == "production":
        configure_event_registry(DatabaseEventRegistry())
        return
    configure_event_registry(InMemoryEventRegistry())


def get_event_registry() -> EventRegistry:
    """
    Return the currently configured process-local workflow event registry.

    Before explicit configuration, this is the import-time `InMemoryEventRegistry`
    instance. Call `configure_event_registry_from_settings()` to replace it with
    the registry selected by Django settings and `WORKFLOW_MODE`.
    """
    return _event_registry


def publish_sync(event: WorkflowEvent) -> bool:
    """
    Publish an event synchronously against the configured registry.

    If the active registry exposes `publish_sync`, that method is used so
    database-backed registries can persist the event and route handlers inline
    even when async delivery is enabled. Otherwise this falls back to
    `registry.publish(event)`.

    Returns:
        bool: `True` when at least one handler completed, `False` otherwise.
    """
    registry = get_event_registry()
    method = getattr(registry, "publish_sync", None)
    if callable(method):
        return bool(method(event))
    return registry.publish(event)
