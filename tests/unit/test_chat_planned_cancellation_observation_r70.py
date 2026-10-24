"""Cancellation attribution records actual owner calls, never guessed timer origins."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from general_manager.chat.planned.scheduler import iter_planned_read_events
from general_manager.chat.providers.base import (
    DoneEvent,
    TokenUsage,
    ToolCallEvent,
)
from tests.unit.test_chat_planned_action_feedback import _runner
from tests.unit.test_chat_planned_unlimited_rounds import _CancellableProvider


def _install(monkeypatch, provider):
    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.build_profile_provider",
        lambda _: provider,
    )


def test_actual_scheduler_deadline_cancel_is_named_before_provider_observation(
    monkeypatch,
):
    provider = _CancellableProvider()
    _install(monkeypatch, provider)
    runner = _runner()
    now = [0.0]
    runner.clock = lambda: now[0]
    runner.deadline = 100.0

    async def expire():
        original_wait = asyncio.wait

        async def observed_wait(*args, **kwargs):
            await provider.entered.wait()
            now[0] = 100.0
            # Wake the outer scheduler exactly at its own deadline while the
            # real per-round asyncio timer still has almost 100 seconds left.
            return set(), set(args[0])

        monkeypatch.setattr(asyncio, "wait", observed_wait)
        try:
            await runner.run()
        finally:
            monkeypatch.setattr(asyncio, "wait", original_wait)

    asyncio.run(expire())
    records = runner.prepared.cancellation_observations
    assert [(item.event, item.location, item.task_id) for item in records] == [
        ("requested", "evidence_deadline", "root"),
        ("observed", "executor_provider_round", "root"),
    ]
    assert all(item.evidence_deadline == item.observed_at == 100.0 for item in records)
    assert provider.cancelled and len(provider.seen) == 1
    assert runner.runtimes["root"].reason == "deadline_exceeded"


def test_external_cancellation_does_not_invent_scheduler_or_provider_timer_origin(
    monkeypatch,
):
    provider = _CancellableProvider()
    _install(monkeypatch, provider)
    runner = _runner()
    runner.deadline = 100.0

    async def cancel():
        execution = asyncio.create_task(runner.run())
        await provider.entered.wait()
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(cancel())
    records = runner.prepared.cancellation_observations
    assert [(item.event, item.location) for item in records] == [
        ("requested", "runner_cleanup"),
        ("observed", "executor_provider_round"),
    ]
    assert all(
        item.observed_at == 0.0 and item.evidence_deadline == 100.0 for item in records
    )
    assert runner.runtimes["root"].reason is None
    assert provider.cancelled and len(provider.seen) == 1


def test_event_stream_cancellation_retains_partial_evidence_without_completion_or_retry(
    monkeypatch,
):
    class QueryThenWait(_CancellableProvider):
        async def complete(self, messages, tools):
            if not self.seen:
                self.seen.append(messages)
                yield ToolCallEvent(
                    "read", "query", {"manager": "PartManager", "fields": ["id"]}
                )
                yield DoneEvent(TokenUsage(2, 1))
                return
            async for event in super().complete(messages, tools):
                yield event

    provider = QueryThenWait()
    _install(monkeypatch, provider)
    runner = _runner()
    prepared = runner.prepared
    callbacks = replace(runner.callbacks, execute_tool=lambda *_: {"data": [{"id": 3}]})
    events = []

    async def consume():
        async for event in iter_planned_read_events(
            prepared,
            scope={},
            conversation=None,
            messages=[],
            callbacks=callbacks,
            clock=lambda: 0.0,
        ):
            events.append(event)

    async def cancel():
        execution = asyncio.create_task(consume())
        await provider.entered.wait()
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(cancel())
    assert prepared.result is None
    assert prepared.interrupted_result is not None
    assert prepared.interrupted_result.statuses == {"root": "running"}
    assert prepared.interrupted_result.selected_evidence_ids == {}
    assert prepared.interrupted_result.usage == TokenUsage(2, 1)
    assert [
        record.payload() for record in prepared.interrupted_result.evidence.records
    ] == [{"data": [{"id": 3}]}]
    assert [
        (item.event, item.location) for item in prepared.cancellation_observations
    ] == [
        ("requested", "event_stream_cleanup"),
        ("requested", "runner_cleanup"),
        ("observed", "executor_provider_round"),
    ]
    assert provider.cancelled and len(provider.seen) == 2
    assert not any(event["type"] in {"done", "error", "text_chunk"} for event in events)


def test_unidentified_provider_cancellation_has_no_invented_cancel_request(monkeypatch):
    class SelfCancelled(_CancellableProvider):
        async def complete(self, messages, tools):
            self.seen.append(messages)
            raise asyncio.CancelledError
            yield  # pragma: no cover

    provider = SelfCancelled()
    _install(monkeypatch, provider)
    runner = _runner()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runner.run_task(runner.runtimes["root"]))
    assert [
        (item.event, item.location)
        for item in runner.prepared.cancellation_observations
    ] == [("observed", "executor_provider_round")]
    assert len(provider.seen) == 1
