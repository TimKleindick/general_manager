"""Batch data-change notifications across synchronous bulk operations."""

from __future__ import annotations

import threading
from _thread import LockType
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from asgiref.sync import async_to_sync

from general_manager.logging import get_logger

type NotificationKey = tuple[str, ...]
type NotificationMessage = Mapping[str, object]
type GroupSend = Callable[[str, dict[str, object]], Awaitable[None]]

logger = get_logger("api.notification_batching")
_COMBINED_FAILURE_MESSAGE = "bulk data change and notification flush failed"


@dataclass(slots=True)
class _PendingNotification:
    key: NotificationKey
    group_send: GroupSend = field(repr=False)
    group: str
    message: dict[str, object]


@dataclass(slots=True)
class _BatchState:
    targets: dict[NotificationKey, _PendingNotification] = field(default_factory=dict)
    accepting: bool = True
    lock: LockType = field(default_factory=threading.Lock, repr=False)


_active_batch: ContextVar[_BatchState | None] = ContextVar(
    "general_manager_notification_batch",
    default=None,
)


def _queue_notification(
    *,
    key: NotificationKey,
    group_send: GroupSend,
    group: str,
    message: NotificationMessage,
) -> bool:
    """Queue a target when batching is active."""
    state = _active_batch.get()
    if state is None:
        return False
    with state.lock:
        if not state.accepting:
            return False
        if key in state.targets:
            return True
        state.targets[key] = _PendingNotification(
            key,
            group_send,
            group,
            dict(message),
        )
        return True


def _close_batch(state: _BatchState) -> None:
    with state.lock:
        state.accepting = False


async def _flush_notifications(state: _BatchState) -> None:
    for key in sorted(state.targets):
        target = state.targets[key]
        try:
            await target.group_send(target.group, target.message)
        except MemoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "failed to dispatch batched notification",
                context={"key": key, "group": target.group},
                exc_info=exc,
            )


def _flush_sync(state: _BatchState) -> None:
    if state.targets:
        async_to_sync(_flush_notifications)(state)


@contextmanager
def bulk_data_change_notifications() -> Iterator[None]:
    """Collect and flush data-change notifications for a bulk operation."""
    current = _active_batch.get()
    if current is not None:
        yield
        return

    state = _BatchState()
    token = _active_batch.set(state)
    try:
        yield
    except BaseException as body_error:
        _close_batch(state)
        _active_batch.reset(token)
        try:
            _flush_sync(state)
        except BaseException as flush_error:  # noqa: BLE001
            raise BaseExceptionGroup(
                _COMBINED_FAILURE_MESSAGE,
                [body_error, flush_error],
            ) from None
        raise
    else:
        _close_batch(state)
        _active_batch.reset(token)
        _flush_sync(state)
