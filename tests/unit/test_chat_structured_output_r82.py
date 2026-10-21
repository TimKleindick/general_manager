"""Optional provider schema guidance preserves planned validation and accounting."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.planner import plan_request
from general_manager.chat.planned.provider_calls import (
    ProviderStreamError,
    complete_provider_round,
)
from general_manager.chat.planned.synthesis import synthesize_answer
from general_manager.chat.providers.base import DoneEvent, TextChunkEvent, TokenUsage
from general_manager.chat.providers.openai import OpenAIProvider
from tests.unit.test_chat_planned_planner import _plan, _settings as _planner_settings
from tests.unit.test_chat_planned_synthesis import (
    _settings as _synthesis_settings,
    _store,
)


_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
_USAGE = TokenUsage(2, 3)


class _LegacyProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def complete(self, messages, tools):
        self.calls.append((messages, tools))
        yield TextChunkEvent(next(self.responses))
        yield DoneEvent(_USAGE)


class _StructuredProvider(_LegacyProvider):
    def __init__(self, responses):
        super().__init__(responses)
        self.schemas = []

    async def complete_structured(self, messages, tools, *, response_schema):
        self.schemas.append(deepcopy(response_schema))
        async for event in self.complete(messages, tools):
            yield event


def _reference_schema(messages):
    for message in messages:
        content = message.logical_content or message.content
        for prefix in ("REFERENCE_DATA=", "RESOLVED_REFERENCE_DATA="):
            if content.startswith(prefix):
                return json.loads(content.removeprefix(prefix))["required_json_schema"]
    pytest.fail("planned request did not contain its schema")


def test_optional_schema_keeps_legacy_custom_provider_signature():
    provider = _LegacyProvider(['{"answer":"legacy"}'])
    result = asyncio.run(
        complete_provider_round(provider, [], [], 1.0, response_schema=_SCHEMA)
    )
    assert result.text == '{"answer":"legacy"}'
    assert result.usage == _USAGE
    assert len(provider.calls) == 1


def test_round_uses_optional_schema_capability_and_accounts_once():
    provider = _StructuredProvider(['{"answer":"guided"}'])
    observed = []

    async def record_usage(usage):
        observed.append(usage)

    result = asyncio.run(
        complete_provider_round(
            provider, [], [], 1.0, response_schema=_SCHEMA, on_usage=record_usage
        )
    )
    assert provider.schemas == [_SCHEMA]
    assert result.text == '{"answer":"guided"}'
    assert result.usage == _USAGE
    assert observed == [_USAGE]


def test_round_without_schema_uses_existing_completion_path():
    provider = _StructuredProvider(["ordinary response"])
    result = asyncio.run(complete_provider_round(provider, [], [], 1.0))
    assert result.text == "ordinary response"
    assert provider.schemas == []


def test_openai_opt_out_preserves_subclass_with_legacy_complete_signature():
    class LegacySubclass(OpenAIProvider):
        async def complete(self, messages, tools):
            yield TextChunkEvent("custom response")
            yield DoneEvent(_USAGE)

    result = asyncio.run(
        complete_provider_round(
            LegacySubclass({}), [], [], 1.0, response_schema=_SCHEMA
        )
    )
    assert result.text == "custom response"
    assert result.usage == _USAGE


def _chunk(*, text=None, refusal=None, finish=None, usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                finish_reason=finish,
                delta=SimpleNamespace(content=text, refusal=refusal, tool_calls=[]),
            )
        ],
        usage=usage,
    )


def _usage_chunk():
    return SimpleNamespace(
        choices=[], usage=SimpleNamespace(prompt_tokens=2, completion_tokens=3)
    )


def _tool_chunk():
    chunk = _chunk(finish="tool_calls")
    chunk.choices[0].delta.tool_calls = [
        SimpleNamespace(
            index=0,
            id="blocked-call",
            function=SimpleNamespace(name="query", arguments="{}"),
        )
    ]
    return chunk


class _Stream:
    def __init__(self, chunks, closed):
        self.chunks = iter(chunks)
        self.closed = closed

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.chunks)
        except StopIteration:
            raise StopAsyncIteration from None

    async def close(self):
        self.closed.append("stream")


class _Client:
    def __init__(self, chunks, *, failure=None):
        self.calls = []
        self.closed = []
        self.stream = _Stream(chunks, self.closed)
        self.failure = failure
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        return self.stream

    async def close(self):
        self.closed.append("client")


@pytest.mark.parametrize("enabled", [True, False, None, "true"])
def test_openai_schema_guidance_requires_explicit_profile_opt_in(enabled):
    config = {"model": "gpt-4.1-mini"}
    if enabled is not None:
        config["structured_outputs"] = enabled
    client = _Client([_chunk(text='{"answer":"ok"}', finish="stop"), _usage_chunk()])
    schema = deepcopy(_SCHEMA)
    with patch.object(OpenAIProvider, "_build_async_client", return_value=client):
        result = asyncio.run(
            complete_provider_round(
                OpenAIProvider(config), [], [], 1.0, response_schema=schema
            )
        )
    request = client.calls[0]
    if enabled is True:
        assert request["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "planned_response",
                "schema": _SCHEMA,
                "strict": False,
            },
        }
        request["response_format"]["json_schema"]["schema"]["required"].clear()
        assert schema == _SCHEMA
    else:
        assert "response_format" not in request
    assert result.text == '{"answer":"ok"}'
    assert result.usage == _USAGE
    assert len(client.calls) == 1
    assert client.closed == ["stream", "client"]


@pytest.mark.parametrize(
    "chunks",
    [
        [_chunk(refusal="private refusal", finish="stop"), _usage_chunk()],
        [
            _chunk(text='{"answer":"apparently valid"}'),
            _chunk(refusal="private refusal", finish="stop"),
            _usage_chunk(),
        ],
        [
            _tool_chunk(),
            _chunk(refusal="private refusal", finish="stop"),
            _usage_chunk(),
        ],
        [_chunk(text='{"answer":"truncated"}', finish="length"), _usage_chunk()],
        [_chunk(text='{"answer":"no terminal"}'), _usage_chunk()],
    ],
    ids=["refusal", "mixed-refusal", "tool-refusal", "incomplete", "missing-stop"],
)
def test_openai_rejects_refusal_or_incomplete_guidance_with_observed_usage(chunks):
    client = _Client(chunks)
    observed = []

    async def record_usage(usage):
        observed.append(usage)

    with (
        patch.object(OpenAIProvider, "_build_async_client", return_value=client),
        pytest.raises(ProviderStreamError) as caught,
    ):
        asyncio.run(
            complete_provider_round(
                OpenAIProvider({"structured_outputs": True}),
                [],
                [],
                1.0,
                response_schema=_SCHEMA,
                classify_provider_errors=True,
                on_usage=record_usage,
            )
        )
    assert caught.value.usage == _USAGE
    assert observed == [_USAGE]
    assert str(caught.value) == "provider_failed"
    assert len(client.calls) == 1
    assert client.closed == ["stream", "client"]


def test_openai_schema_rejection_does_not_retry_without_schema():
    client = _Client([], failure=RuntimeError("private unsupported schema"))
    with (
        patch.object(OpenAIProvider, "_build_async_client", return_value=client),
        pytest.raises(ProviderStreamError),
    ):
        asyncio.run(
            complete_provider_round(
                OpenAIProvider({"structured_outputs": True}),
                [],
                [],
                1.0,
                response_schema=_SCHEMA,
                classify_provider_errors=True,
            )
        )
    assert len(client.calls) == 1
    assert client.calls[0]["response_format"]["type"] == "json_schema"
    assert client.closed == ["client"]


@pytest.mark.parametrize("configured_context", ["", "Parts are inventory records."])
def test_planner_guides_with_actual_schema_and_keeps_semantic_validation(
    monkeypatch, configured_context
):
    valid = _plan()
    invalid = deepcopy(valid)
    invalid["tasks"][0]["completion_criteria"] = ["nonexistent-requirement"]
    if configured_context:
        valid = {
            "plan": valid,
            "choices": [],
            "clarification_requests": [],
            "definition_bindings": [],
        }
        invalid = {**valid, "plan": invalid}
    provider = _StructuredProvider([json.dumps(invalid), json.dumps(valid)])
    monkeypatch.setattr(
        "general_manager.chat.planned.planner.build_profile_provider",
        lambda _profile: provider,
    )
    result = asyncio.run(
        plan_request(
            "show parts",
            [],
            _planner_settings(),
            RoundBudget(()),
            {},
            configured_context=configured_context,
        )
    )
    assert result.plan.tasks[0].completion_criteria == ("query_1",)
    assert result.usage == TokenUsage(4, 6)
    assert len(provider.schemas) == 2
    assert provider.schemas == [
        _reference_schema(messages) for messages, _tools in provider.calls
    ]
    if configured_context:
        assert "definition_bindings" in provider.schemas[0]["required"]


@pytest.mark.parametrize(
    "invalid", ["not JSON", '{"answer":"bad","evidence_ids":["foreign"]}']
)
def test_synthesis_guides_with_eligible_schema_and_keeps_grounding(
    monkeypatch, invalid
):
    provider = _StructuredProvider(
        [invalid, '{"answer":"No parts found.","evidence_ids":["ev-query-1"]}']
    )
    monkeypatch.setattr(
        "general_manager.chat.planned.synthesis.build_profile_provider",
        lambda _profile: provider,
    )
    result = asyncio.run(
        synthesize_answer(
            "show parts", _store(), {}, _synthesis_settings(), RoundBudget(())
        )
    )
    assert result.answer == "No parts found."
    assert result.evidence_ids == ("ev-query-1",)
    assert result.usage == TokenUsage(4, 6)
    assert len(provider.schemas) == 2
    assert provider.schemas == [
        _reference_schema(messages) for messages, _tools in provider.calls
    ]
    assert provider.schemas[0]["oneOf"][0]["properties"]["evidence_ids"]["items"][
        "enum"
    ] == ["ev-query-1"]
