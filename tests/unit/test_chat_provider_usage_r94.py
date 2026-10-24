"""Rejected SDK responses retain accounting without admitting completion."""

import asyncio
from types import SimpleNamespace as NS

import pytest

from general_manager.chat.planned.provider_calls import (
    ProviderRoundTimeoutError,
    ProviderStreamError,
    complete_provider_round,
)
from general_manager.chat.providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
)
from general_manager.chat.providers._shared import completed_events
from general_manager.chat.providers.base import DoneEvent, TokenUsage, ToolCallEvent


_PROVIDERS = [OpenAIProvider, AnthropicProvider, GeminiProvider, OllamaProvider]


def _chunks(provider_type, *, rejected=False, known=True):
    if provider_type is OpenAIProvider:
        chunks = [
            NS(
                choices=[
                    NS(
                        index=0,
                        finish_reason="length" if rejected else "stop",
                        delta=NS(content="answer", tool_calls=[]),
                    )
                ],
                usage=None,
            )
        ]
        if known:
            chunks.append(
                NS(choices=[], usage=NS(prompt_tokens=2, completion_tokens=3))
            )
        return chunks
    if provider_type is AnthropicProvider:
        return [
            NS(type="content_block_delta", delta=NS(type="text_delta", text="answer")),
            NS(
                type="message_start",
                message=NS(usage=NS(input_tokens=2)) if known else NS(),
            ),
            NS(
                type="message_delta",
                delta=NS(stop_reason="max_tokens" if rejected else "end_turn"),
                usage=NS(output_tokens=3) if known else None,
            ),
            NS(type="message_stop"),
        ]
    if provider_type is GeminiProvider:
        return [
            NS(
                text="answer",
                candidates=[NS(finish_reason="MAX_TOKENS" if rejected else "STOP")],
                usage_metadata=NS(prompt_token_count=2, candidates_token_count=3)
                if known
                else None,
            )
        ]
    return [
        {
            "message": {"content": "answer"},
            "done": True,
            "done_reason": "length" if rejected else "stop",
            **({"prompt_eval_count": 2, "eval_count": 3} if known else {}),
        }
    ]


class _Transport:
    """Replace only SDK transport; adapters and accounting remain real."""

    def __init__(
        self,
        chunks,
        *,
        late_failure=False,
        wait_at_eof=False,
        cleanup_failure=False,
        stream_cleanup_failure=False,
    ):
        self.chunks = iter(chunks)
        self.late_failure = late_failure
        self.wait_at_eof = wait_at_eof
        self.cleanup_failure = cleanup_failure
        self.stream_cleanup_failure = stream_cleanup_failure
        self.waiting = asyncio.Event()
        self.closed = []
        self.requests = []
        self.chat = NS(completions=self)
        self.messages = self
        self.aio = NS(models=self)
        self._client = NS(aclose=self.close_client)

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return self

    generate_content_stream = create

    async def _request(self, *args, **kwargs):
        return await self.create(**kwargs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.chunks)
        except StopIteration:
            if self.wait_at_eof:
                self.waiting.set()
                await asyncio.Event().wait()
            if self.late_failure:
                detail = "controlled late transport failure"
                raise OSError(detail) from None
            raise StopAsyncIteration from None

    async def aclose(self):
        self.closed.append("stream")
        if self.stream_cleanup_failure:
            detail = "controlled stream cleanup failure"
            raise OSError(detail)

    async def close_client(self):
        self.closed.append("client")
        if self.cleanup_failure:
            detail = "controlled client cleanup failure"
            raise OSError(detail)


def _provider(monkeypatch, provider_type, chunks, **transport_options):
    transport = _Transport(chunks, **transport_options)
    # OpenAI selects aclose before close; keep its client separate from stream.
    client = NS(
        chat=transport.chat,
        messages=transport.messages,
        aio=transport.aio,
        _request=transport._request,
        _client=transport._client,
        close=transport.close_client,
    )
    monkeypatch.setattr(
        provider_type, "_build_async_client", classmethod(lambda _cls, _config: client)
    )
    return provider_type.from_config({"model": "offline"}), transport


def _assert_closed(provider_type, transport):
    assert transport.closed == (
        ["stream", "client"]
        if provider_type in {OpenAIProvider, OllamaProvider}
        else ["stream"]
    )
    assert len(transport.requests) == 1


@pytest.mark.parametrize("provider_type", _PROVIDERS)
def test_rejected_terminal_books_known_usage_once(monkeypatch, provider_type):
    provider, transport = _provider(
        monkeypatch, provider_type, _chunks(provider_type, rejected=True)
    )
    observed = []

    async def record(usage):
        observed.append(usage)

    with pytest.raises(ProviderStreamError) as caught:
        asyncio.run(
            complete_provider_round(
                provider, [], [], 1, classify_provider_errors=True, on_usage=record
            )
        )
    assert observed == [TokenUsage(2, 3)]
    assert caught.value.usage_observed is True
    assert caught.value.usage == TokenUsage(2, 3)
    _assert_closed(provider_type, transport)


@pytest.mark.parametrize("provider_type", _PROVIDERS)
def test_normal_terminal_books_known_usage_once(monkeypatch, provider_type):
    provider, transport = _provider(monkeypatch, provider_type, _chunks(provider_type))
    observed = []

    async def record(usage):
        observed.append(usage)

    result = asyncio.run(complete_provider_round(provider, [], [], 1, on_usage=record))
    assert result.text == "answer"
    assert result.usage == TokenUsage(2, 3)
    assert observed == [TokenUsage(2, 3)]
    _assert_closed(provider_type, transport)


@pytest.mark.parametrize("provider_type", _PROVIDERS)
def test_rejected_terminal_without_usage_stays_unknown(monkeypatch, provider_type):
    provider, transport = _provider(
        monkeypatch, provider_type, _chunks(provider_type, rejected=True, known=False)
    )
    observed = []

    async def record(usage):
        observed.append(usage)

    with pytest.raises(ProviderStreamError) as caught:
        asyncio.run(
            complete_provider_round(
                provider, [], [], 1, classify_provider_errors=True, on_usage=record
            )
        )
    assert caught.value.usage_observed is False
    assert observed == []
    _assert_closed(provider_type, transport)


@pytest.mark.parametrize("provider_type", _PROVIDERS)
@pytest.mark.parametrize("rejected", [False, True])
def test_known_terminal_usage_survives_cleanup_failure(
    monkeypatch, provider_type, rejected
):
    provider, transport = _provider(
        monkeypatch,
        provider_type,
        _chunks(provider_type, rejected=rejected),
        cleanup_failure=provider_type is OllamaProvider,
        stream_cleanup_failure=provider_type is not OllamaProvider,
    )
    observed = []

    async def record(usage):
        observed.append(usage)

    with pytest.raises(ProviderStreamError) as caught:
        asyncio.run(
            complete_provider_round(
                provider, [], [], 1, classify_provider_errors=True, on_usage=record
            )
        )
    assert caught.value.usage_observed is True
    assert caught.value.usage == TokenUsage(2, 3)
    assert observed == [TokenUsage(2, 3)]
    _assert_closed(provider_type, transport)


@pytest.mark.parametrize("provider_type", _PROVIDERS)
@pytest.mark.parametrize("rejected", [False, True])
def test_cancellation_after_observed_terminal_usage_propagates_and_cleans_up(
    monkeypatch, provider_type, rejected
):
    async def run():
        provider, transport = _provider(
            monkeypatch, provider_type, _chunks(provider_type, rejected=rejected)
        )
        observed = []
        recorded = asyncio.Event()

        async def record(usage):
            observed.append(usage)
            recorded.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            complete_provider_round(
                provider, [], [], 1, classify_provider_errors=True, on_usage=record
            )
        )
        try:
            await asyncio.wait_for(recorded.wait(), timeout=0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert observed == [TokenUsage(2, 3)]
            _assert_closed(provider_type, transport)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_openai_rejected_terminal_usage_tail_is_bounded_by_existing_deadline(
    monkeypatch,
):
    provider, transport = _provider(
        monkeypatch,
        OpenAIProvider,
        _chunks(OpenAIProvider, rejected=True),
        wait_at_eof=True,
    )
    observed = []

    async def record(usage):
        observed.append(usage)

    with pytest.raises(ProviderRoundTimeoutError) as caught:
        asyncio.run(complete_provider_round(provider, [], [], 0.01, on_usage=record))
    assert caught.value.usage_observed is True
    assert caught.value.usage == TokenUsage(2, 3)
    assert observed == [TokenUsage(2, 3)]
    _assert_closed(OpenAIProvider, transport)


@pytest.mark.parametrize("provider_type", [OpenAIProvider, AnthropicProvider])
def test_cancellation_waiting_for_eof_retains_known_terminal_usage(
    monkeypatch, provider_type
):
    async def run():
        provider, transport = _provider(
            monkeypatch,
            provider_type,
            _chunks(provider_type, rejected=True),
            wait_at_eof=True,
        )
        observed = []

        async def record(usage):
            observed.append(usage)

        task = asyncio.create_task(
            complete_provider_round(provider, [], [], 1, on_usage=record)
        )
        try:
            await asyncio.wait_for(transport.waiting.wait(), timeout=0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert observed == [TokenUsage(2, 3)]
            _assert_closed(provider_type, transport)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_gemini_prior_usage_is_retained_when_terminal_chunk_rejects(monkeypatch):
    terminal = _chunks(GeminiProvider, rejected=True, known=False)
    prior_usage = NS(
        text=None,
        candidates=[],
        usage_metadata=NS(prompt_token_count=2, candidates_token_count=3),
    )
    provider, transport = _provider(
        monkeypatch, GeminiProvider, [prior_usage, *terminal]
    )
    observed = []

    async def record(usage):
        observed.append(usage)

    with pytest.raises(ProviderStreamError) as caught:
        asyncio.run(
            complete_provider_round(
                provider, [], [], 1, classify_provider_errors=True, on_usage=record
            )
        )
    assert caught.value.usage_observed is True
    assert caught.value.usage == TokenUsage(2, 3)
    assert observed == [TokenUsage(2, 3)]
    _assert_closed(GeminiProvider, transport)


def test_anthropic_message_stop_without_success_reason_retains_known_usage(monkeypatch):
    chunks = _chunks(AnthropicProvider, rejected=True)
    chunks[-2].delta.stop_reason = None
    provider, transport = _provider(monkeypatch, AnthropicProvider, chunks)
    observed = []

    async def record(usage):
        observed.append(usage)

    with pytest.raises(ProviderStreamError) as caught:
        asyncio.run(
            complete_provider_round(
                provider, [], [], 1, classify_provider_errors=True, on_usage=record
            )
        )
    assert caught.value.usage_observed is True
    assert caught.value.usage == TokenUsage(2, 3)
    assert observed == [TokenUsage(2, 3)]
    _assert_closed(AnthropicProvider, transport)


@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize(
    "provider_type", [OpenAIProvider, AnthropicProvider, GeminiProvider]
)
def test_trailing_usage_updates_replace_provisional_counters(
    monkeypatch, provider_type, rejected
):
    chunks = _chunks(provider_type, rejected=rejected)
    if provider_type is OpenAIProvider:
        chunks.insert(0, NS(choices=[], usage=NS(prompt_tokens=2, completion_tokens=1)))
    elif provider_type is AnthropicProvider:
        chunks[-2].usage = None
        chunks.insert(
            -1, NS(type="message_delta", delta=NS(), usage=NS(output_tokens=3))
        )
    else:
        chunks[0].usage_metadata = None
        chunks.insert(
            0,
            NS(
                text=None,
                candidates=[],
                usage_metadata=NS(prompt_token_count=2, candidates_token_count=1),
            ),
        )
        chunks.append(
            NS(
                text=None,
                candidates=[],
                usage_metadata=NS(prompt_token_count=2, candidates_token_count=3),
            )
        )
    provider, transport = _provider(monkeypatch, provider_type, chunks)
    observed = []

    async def record(usage):
        observed.append(usage)

    async def run():
        return await complete_provider_round(
            provider, [], [], 1, classify_provider_errors=True, on_usage=record
        )

    if rejected:
        with pytest.raises(ProviderStreamError) as caught:
            asyncio.run(run())
        assert caught.value.usage == TokenUsage(2, 3)
    else:
        assert asyncio.run(run()).usage == TokenUsage(2, 3)
    assert observed == [TokenUsage(2, 3)]
    _assert_closed(provider_type, transport)


@pytest.mark.parametrize(
    "provider_type", [OpenAIProvider, AnthropicProvider, GeminiProvider]
)
def test_later_success_marker_cannot_reverse_a_rejected_terminal(
    monkeypatch, provider_type
):
    chunks = _chunks(provider_type, rejected=True)
    if provider_type is AnthropicProvider:
        chunks.insert(-1, NS(type="message_delta", delta=NS(stop_reason="end_turn")))
    elif provider_type is OpenAIProvider:
        chunks.append(
            NS(
                choices=[NS(index=0, finish_reason="stop", delta=NS(content=None))],
                usage=None,
            )
        )
    else:
        chunks.append(NS(text=None, candidates=[NS(finish_reason="STOP")]))
    provider, transport = _provider(monkeypatch, provider_type, chunks)
    observed = []

    async def record(usage):
        observed.append(usage)

    with pytest.raises(ProviderStreamError):
        asyncio.run(
            complete_provider_round(
                provider, [], [], 1, classify_provider_errors=True, on_usage=record
            )
        )
    assert observed == [TokenUsage(2, 3)]
    _assert_closed(provider_type, transport)


@pytest.mark.parametrize("provider_type", _PROVIDERS)
def test_completed_events_never_admits_rejected_done_or_tool(
    monkeypatch, provider_type
):
    chunks = _chunks(provider_type, rejected=True)
    if provider_type is OpenAIProvider:
        chunks[0].choices[0].delta.tool_calls = [
            NS(index=0, id="blocked", function=NS(name="query", arguments="{}"))
        ]
    elif provider_type is AnthropicProvider:
        chunks.insert(
            -1,
            NS(
                type="content_block_start",
                index=0,
                content_block=NS(type="tool_use", id="blocked", name="query", input={}),
            ),
        )
        chunks.insert(-1, NS(type="content_block_stop", index=0))
    elif provider_type is GeminiProvider:
        chunks[0].function_calls = [NS(id="blocked", name="query", args={})]
    else:
        chunks[0]["message"]["tool_calls"] = [
            {"id": "blocked", "function": {"name": "query", "arguments": {}}}
        ]
    provider, transport = _provider(monkeypatch, provider_type, chunks)

    async def run():
        from general_manager.chat.providers._shared import ProviderCompletionError

        admitted = []
        with pytest.raises(ProviderCompletionError):
            async for event in completed_events(provider.complete([], [])):
                admitted.append(event)
        assert not any(
            isinstance(event, (DoneEvent, ToolCallEvent)) for event in admitted
        )

    asyncio.run(run())
    _assert_closed(provider_type, transport)
