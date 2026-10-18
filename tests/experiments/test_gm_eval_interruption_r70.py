"""A stopped native run retains sanitized partial observations on its original error."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from importlib import import_module
import json
from types import SimpleNamespace

import graphene
import pytest

from general_manager.api.graphql import GraphQL


@pytest.fixture
def no_graphql_schema(monkeypatch):
    monkeypatch.setattr(GraphQL, "_schema", None)
    assert GraphQL.get_schema() is None


def test_cancelled_case_retains_native_provider_and_partial_scheduler_trace(
    monkeypatch, no_graphql_schema
):
    from experiments.gm_eval import harness
    from experiments.gm_eval.profiles import make_profile
    from general_manager.chat.consumer import ChatConsumer
    from general_manager.chat.planned import scheduler
    from general_manager.chat.providers.base import DoneEvent, TokenUsage, ToolCallEvent
    from tests.unit.test_chat_planned_action_feedback import _runner

    class Query(graphene.ObjectType):
        ping = graphene.String()

    # Startup validation needs a real schema, independent of prior test state.
    monkeypatch.setattr(GraphQL, "_schema", graphene.Schema(query=Query))

    context = import_module("general_manager.chat.context")
    models = import_module("general_manager.chat.models")
    rate_limits = import_module("general_manager.chat.rate_limits")

    entered = asyncio.Event()
    provider_calls = []

    class QueryThenWait:
        async def complete(self, messages, tools):
            provider_calls.append(messages)
            if len(provider_calls) == 1:
                yield ToolCallEvent(
                    "query", "query", {"manager": "PartManager", "fields": ["id"]}
                )
                yield DoneEvent(TokenUsage(2, 1))
                return
            entered.set()
            await asyncio.Event().wait()

    runner = _runner()
    callbacks = replace(
        runner.callbacks,
        execute_tool=lambda *_: {"data": [{"id": 3, "secret": "not-for-diagnostics"}]},
    )
    prepared = runner.prepared

    async def prepare(text, messages, settings, catalog, **kwargs):
        prepared.settings = settings
        return prepared

    async def messages(*args, **kwargs):
        return []

    monkeypatch.setattr(scheduler, "prepare_planned_turn", prepare)
    monkeypatch.setattr(scheduler, "SchedulerCallbacks", lambda: callbacks)
    monkeypatch.setattr(context, "prepare_conversation_messages", messages)
    monkeypatch.setattr(rate_limits, "enforce_chat_rate_limit", lambda *_: None)
    monkeypatch.setattr(models, "append_chat_message", lambda *_, **__: None)
    monkeypatch.setattr(ChatConsumer, "_planned_catalog_summary", lambda *_: {})
    monkeypatch.setattr(harness, "_new_conversation", lambda *_: None)
    runtime = SimpleNamespace(
        scope={}, catalog={}, glossary="", verify=lambda: {"gaps": []}, managers={}
    )
    case = {"id": "generic-interruption", "turns": ["Show the requested records"]}
    profile = make_profile("strong-only", strong_model="offline")

    async def cancel():
        execution = asyncio.create_task(
            harness.run_case(runtime, case, profile, lambda _: QueryThenWait())
        )
        readiness = asyncio.create_task(entered.wait())
        try:
            done, _ = await asyncio.wait(
                {execution, readiness}, timeout=1.0, return_when=asyncio.FIRST_COMPLETED
            )
            if execution in done:
                # Expose a real setup/runner error instead of hiding its outcome
                # behind an unrelated readiness timeout.
                outcome = await execution
                pytest.fail(
                    "case finished before the waiting provider was reached: "
                    f"{harness.sanitize(outcome)!r}"
                )
            assert readiness in done, "case did not reach its waiting provider"
            execution.cancel("private caller detail")
            with pytest.raises(asyncio.CancelledError) as caught:
                await execution
            return caught.value
        finally:
            for task in (execution, readiness):
                if not task.done():
                    task.cancel()
            await asyncio.gather(execution, readiness, return_exceptions=True)

    original = asyncio.run(cancel())
    record = original.gm_eval_interruption
    assert type(original) is asyncio.CancelledError
    assert original.args == ("private caller detail",)
    assert record["version"] == 1 and record["status"] == "interrupted"
    assert record["exception_type"] == "CancelledError"
    assert record["case_id"] == case["id"]
    assert record["task_quality_status"] == "unscored"
    assert record["observation_complete"] is False
    assert record["remote_outcome"] == "unknown"
    assert record["completed_turns"] == []
    partial = record["interrupted_turn"]
    assert partial["turn"] == 1 and partial["user"] == case["turns"][0]
    assert partial["terminal"] is None
    assert partial["task_quality_status"] == "unscored"
    assert any(event["type"] == "tool_result" for event in partial["events"])
    assert not any(event["type"] in {"error", "done"} for event in partial["events"])
    calls = record["trace"]["provider_calls"]
    assert len(calls) == len(provider_calls) == 2
    assert calls[0]["completed"] is True
    assert calls[1]["completed"] is False and calls[1]["error_type"] == "CancelledError"
    snapshot = record["scheduler"]["interrupted_result"]
    assert snapshot["statuses"] == {"root": "running"}
    assert snapshot["evidence"][0]["payload"] == {"data": [{"id": 3}]}
    assert snapshot["selected_evidence_ids"] == {}
    observations = record["scheduler"]["cancellation_observations"]
    assert [(item["event"], item["location"]) for item in observations] == [
        ("requested", "event_stream_cleanup"),
        ("requested", "runner_cleanup"),
        ("observed", "executor_provider_round"),
    ]
    rendered = json.dumps(record)
    assert "not-for-diagnostics" not in rendered
    assert "private caller detail" not in rendered
    assert "api_key" not in rendered
    assert prepared.result is None
