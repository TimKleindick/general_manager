"""Tests for batching data-change notifications."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync

from general_manager.api import bulk_data_change_notifications
from general_manager.api.notification_batching import _queue_notification


def test_queue_notification_returns_false_outside_batch() -> None:
    async def group_send(_group: str, _message: dict[str, object]) -> None:
        raise AssertionError

    assert not _queue_notification(
        key=("graphql", "Project"),
        group_send=group_send,
        group="project-refresh",
        message={"action": "refresh"},
    )


def test_empty_batch_does_not_create_async_bridge() -> None:
    with patch("general_manager.api.notification_batching.async_to_sync") as bridge:
        with bulk_data_change_notifications():
            pass

    bridge.assert_not_called()


def test_nested_batch_deduplicates_first_target_and_flushes_once() -> None:
    sent: list[tuple[str, dict[str, object]]] = []

    async def group_send(group: str, message: dict[str, object]) -> None:
        sent.append((group, message))

    message: dict[str, object] = {
        "type": "gm.subscription.event",
        "action": "refresh",
    }
    with patch(
        "general_manager.api.notification_batching.async_to_sync",
        side_effect=async_to_sync,
    ) as bridge:
        with bulk_data_change_notifications():
            assert _queue_notification(
                key=("graphql", "Project"),
                group_send=group_send,
                group="project-refresh",
                message=message,
            )
            message["action"] = "changed-after-queue"
            with bulk_data_change_notifications():
                assert _queue_notification(
                    key=("graphql", "Project"),
                    group_send=group_send,
                    group="ignored-duplicate",
                    message={"action": "ignored"},
                )

    bridge.assert_called_once()
    assert sent == [
        (
            "project-refresh",
            {"type": "gm.subscription.event", "action": "refresh"},
        )
    ]


def test_batch_dispatches_sequentially_in_sorted_key_order() -> None:
    sent: list[str] = []
    active_sends = 0

    async def group_send(group: str, _message: dict[str, object]) -> None:
        nonlocal active_sends
        assert active_sends == 0
        active_sends += 1
        await asyncio.sleep(0)
        sent.append(group)
        active_sends -= 1

    with bulk_data_change_notifications():
        for key, group in [
            (("remote", "zeta"), "zeta-refresh"),
            (("graphql", "Alpha"), "alpha-refresh"),
            (("remote", "middle"), "middle-refresh"),
        ]:
            assert _queue_notification(
                key=key,
                group_send=group_send,
                group=group,
                message={"action": "refresh"},
            )

    assert sent == ["alpha-refresh", "middle-refresh", "zeta-refresh"]


def test_batch_is_inactive_while_notifications_are_flushed() -> None:
    reentrant_queue_results: list[bool] = []

    async def group_send(_group: str, _message: dict[str, object]) -> None:
        reentrant_queue_results.append(
            _queue_notification(
                key=("remote", "reentrant"),
                group_send=group_send,
                group="reentrant-refresh",
                message={"action": "refresh"},
            )
        )

    with bulk_data_change_notifications():
        assert _queue_notification(
            key=("graphql", "Project"),
            group_send=group_send,
            group="project-refresh",
            message={"action": "refresh"},
        )

    assert reentrant_queue_results == [False]


def test_ordinary_dispatch_failure_is_logged_and_remaining_targets_continue(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sent: list[str] = []

    async def group_send(group: str, _message: dict[str, object]) -> None:
        if group == "alpha-refresh":
            raise RuntimeError
        sent.append(group)

    with caplog.at_level(
        logging.WARNING,
        logger="general_manager.api.notification_batching",
    ):
        with bulk_data_change_notifications():
            for key, group in [
                (("graphql", "Alpha"), "alpha-refresh"),
                (("graphql", "Beta"), "beta-refresh"),
            ]:
                assert _queue_notification(
                    key=key,
                    group_send=group_send,
                    group=group,
                    message={"action": "refresh"},
                )

    assert sent == ["beta-refresh"]
    failure_record = next(
        record
        for record in caplog.records
        if record.message == "failed to dispatch batched notification"
    )
    assert failure_record.__dict__["context"] == {
        "key": ("graphql", "Alpha"),
        "group": "alpha-refresh",
    }


def test_memory_error_propagates_without_dispatching_remaining_targets() -> None:
    attempted: list[str] = []
    memory_error = MemoryError("thread startup")

    async def group_send(group: str, _message: dict[str, object]) -> None:
        attempted.append(group)
        if group == "alpha-refresh":
            raise memory_error

    with pytest.raises(MemoryError, match="thread startup"):
        with bulk_data_change_notifications():
            for key, group in [
                (("graphql", "Alpha"), "alpha-refresh"),
                (("graphql", "Beta"), "beta-refresh"),
            ]:
                assert _queue_notification(
                    key=key,
                    group_send=group_send,
                    group=group,
                    message={"action": "refresh"},
                )

    assert attempted == ["alpha-refresh"]


def test_batch_flushes_when_body_raises() -> None:
    sent: list[str] = []
    body_error = ValueError("row failed")

    async def group_send(group: str, _message: dict[str, object]) -> None:
        sent.append(group)

    with pytest.raises(ValueError, match="row failed"):
        with bulk_data_change_notifications():
            assert _queue_notification(
                key=("remote", "projects"),
                group_send=group_send,
                group="projects-refresh",
                message={"action": "refresh"},
            )
            raise body_error

    assert sent == ["projects-refresh"]


def test_body_and_memory_failure_are_preserved() -> None:
    async def exhausted(_group: str, _message: dict[str, object]) -> None:
        raise MemoryError

    with pytest.raises(ExceptionGroup) as caught:
        with bulk_data_change_notifications():
            assert _queue_notification(
                key=("graphql", "Project"),
                group_send=exhausted,
                group="project-refresh",
                message={"action": "refresh"},
            )
            raise ValueError

    assert [type(exc) for exc in caught.value.exceptions] == [
        ValueError,
        MemoryError,
    ]
