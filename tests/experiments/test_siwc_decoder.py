"""Finalized Responses text is recovered only after successful completion."""

import asyncio
from copy import deepcopy
import json

import pytest

from experiments.siwc_eval.errors import EvalError
from experiments.siwc_eval.provider import Provider
from general_manager.chat.planned.provider_calls import (
    InvalidProviderRoundError,
    complete_provider_round,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
    ToolDefinition,
)


TEXT = "A finalized answer: Grüße."
TOOLS = [ToolDefinition("query", "synthetic declaration only", {"type": "object"})]
MESSAGES = [Message("user", "Synthetic request")]


class Replay:
    def __init__(self, events):
        self.events = events
        self.bodies = []

    async def stream(self, body):
        self.bodies.append(deepcopy(body))
        for event in self.events:
            yield deepcopy(event)


def message(text=TEXT, *, item_id="msg_0", parts=None):
    return {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "status": "completed",
        "content": parts
        if parts is not None
        else [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}],
    }


def text_event(
    text=TEXT, *, kind="done", output_index=0, content_index=0, item_id="msg_0"
):
    return {
        "type": "response.output_text." + kind,
        "output_index": output_index,
        "content_index": content_index,
        "item_id": item_id,
        "text" if kind == "done" else "delta": text,
    }


def item_done(item, index=0):
    return {"type": "response.output_item.done", "output_index": index, "item": item}


def completed(output=(), *, status="completed"):
    return {
        "type": "response.completed",
        "response": {
            "status": status,
            "output": list(output),
            "usage": {"input_tokens": 7, "output_tokens": 2},
        },
    }


def call(*, status="completed"):
    return {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call-1",
        "name": "query",
        "namespace": "gm",
        "arguments": '{"value":2}',
        "status": status,
    }


def final_events(source):
    if source == "text_done":
        return [text_event(), completed()]
    if source == "item_done":
        return [item_done(message()), completed()]
    return [completed([message()])]


def collect(events, *, tools=()):
    async def run():
        return [
            event
            async for event in Provider("offline", Replay(events)).complete(
                MESSAGES, list(tools)
            )
        ]

    return asyncio.run(run())


@pytest.mark.parametrize("source", ["text_done", "item_done", "completed"])
def test_final_text_without_deltas_reaches_provider_and_strict_round(source):
    events = final_events(source)
    assert collect(events) == [TextChunkEvent(TEXT), DoneEvent(TokenUsage(7, 2))]
    result = asyncio.run(
        complete_provider_round(Provider("offline", Replay(events)), MESSAGES, [], 1.0)
    )
    assert result.text == TEXT and result.tool_call is None


@pytest.mark.parametrize("delta", [TEXT, TEXT[:8], "Grüße."])
def test_final_text_replaces_complete_or_partial_deltas_once(delta):
    assert collect(
        [
            text_event(delta, kind="delta"),
            text_event(),
            text_event(),
            item_done(message()),
            completed([message()]),
        ]
    ) == [TextChunkEvent(TEXT), DoneEvent(TokenUsage(7, 2))]


def test_text_is_ordered_by_output_and_content_indices_not_arrival():
    events = [
        text_event("b1", output_index=1, content_index=1, item_id="msg_1"),
        text_event("a1", content_index=1),
        text_event("b0", output_index=1, item_id="msg_1"),
        text_event("a0"),
        completed(),
    ]
    assert collect(events)[0] == TextChunkEvent("a0a1b0b1")


def test_distinct_items_with_equal_text_are_not_deduplicated():
    assert collect(
        [
            text_event("same", output_index=1, item_id="msg_1"),
            text_event("same"),
            completed(),
        ]
    )[0] == TextChunkEvent("samesame")


@pytest.mark.parametrize(
    "events",
    [
        [text_event("first"), text_event("different")],
        [text_event("first"), item_done(message("different"))],
        [item_done(message("first")), completed([message("different")])],
        [text_event(item_id="msg_wrong"), item_done(message())],
        [text_event(), text_event(output_index=1)],
        [text_event(content_index=2), item_done(message())],
    ],
)
def test_conflicting_final_text_or_identity_is_rejected_before_release(events):
    emitted = []
    if events[-1]["type"] != "response.completed":
        events = [*events, completed()]

    async def run():
        async for event in Provider("offline", Replay(events)).complete(MESSAGES, []):
            emitted.append(event)

    with pytest.raises(EvalError, match="conflicting_text_output"):
        asyncio.run(run())
    assert emitted == []


@pytest.mark.parametrize("index", [True, -1, "0"])
def test_final_text_rejects_invalid_indices(index):
    with pytest.raises(EvalError, match="invalid_text_event"):
        collect([text_event(output_index=index), completed()])


@pytest.mark.parametrize("source", ["text_done", "item_done", "completed"])
@pytest.mark.parametrize(
    "terminal", ["response.failed", "response.incomplete", "error", "eof"]
)
def test_final_text_and_buffered_calls_never_escape_unsuccessful_stream(
    source, terminal
):
    events = final_events(source)[:-1]
    if source == "completed":
        # A final item is available but the response as a whole never completes.
        events = [item_done(message())]
    events.append(item_done(call(), 1))
    if terminal != "eof":
        events.append({"type": terminal, "response": {"output": [message(), call()]}})
    emitted = []
    provider = Provider("offline", Replay(events))

    async def run():
        async for event in provider.complete(MESSAGES, TOOLS):
            emitted.append(event)

    with pytest.raises(
        EvalError, match="stream_without_completion" if terminal == "eof" else terminal
    ):
        asyncio.run(run())
    assert emitted == [] and provider.stopped


@pytest.mark.parametrize(
    "output",
    [
        [],
        [
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [],
                "encrypted_content": "synthetic",
            }
        ],
        [message(parts=[{"type": "refusal", "refusal": "synthetic refusal"}])],
    ],
)
def test_empty_reasoning_and_refusal_are_not_invented_executor_text(output):
    events = [completed(output)]
    assert collect(events) == [DoneEvent(TokenUsage(7, 2))]
    with pytest.raises(
        InvalidProviderRoundError, match="must produce text or tool calls"
    ):
        asyncio.run(
            complete_provider_round(
                Provider("offline", Replay(events)), MESSAGES, [], 1.0
            )
        )


@pytest.mark.parametrize("source", ["text_done", "item_done", "completed"])
def test_recovered_text_survives_continuation_with_raw_reasoning(source):
    reasoning = {
        "type": "reasoning",
        "id": "rs_0",
        "summary": [],
        "encrypted_content": "synthetic",
    }
    output = message(item_id="msg_1")
    if source == "text_done":
        events = [text_event(output_index=1, item_id="msg_1"), completed([reasoning])]
        expected_output = [reasoning, {"role": "assistant", "content": TEXT}]
    elif source == "item_done":
        events = [item_done(output, 1), item_done(reasoning), completed()]
        expected_output = [reasoning, output]
    else:
        events = [completed([reasoning, output])]
        expected_output = [reasoning, output]
    transport = Replay(events)
    provider = Provider("offline", transport)

    async def run():
        first = [event async for event in provider.complete(MESSAGES, [])]
        assert first[0] == TextChunkEvent(TEXT)
        transport.events = [completed()]
        continued = [*MESSAGES, Message("assistant", TEXT), Message("user", "Continue")]
        _ = [event async for event in provider.complete(continued, [])]

    asyncio.run(run())
    assert transport.bodies[1]["input"] == [
        {"role": "user", "content": "Synthetic request"},
        *expected_output,
        {"role": "user", "content": "Continue"},
    ]


def test_recovered_text_plus_function_preserves_strict_round_rejection_and_call_id():
    events = [completed([message(), call()])]
    assert collect(events, tools=TOOLS) == [
        TextChunkEvent(TEXT),
        ToolCallEvent("call-1", "query", {"value": 2}),
        DoneEvent(TokenUsage(7, 2)),
    ]
    with pytest.raises(InvalidProviderRoundError, match="text and a tool call"):
        asyncio.run(
            complete_provider_round(
                Provider("offline", Replay(events)), MESSAGES, TOOLS, 1.0
            )
        )


@pytest.mark.parametrize("status", ["in_progress", "incomplete", "failed"])
def test_explicit_incomplete_function_item_is_never_released(status):
    with pytest.raises(EvalError, match="invalid_tool_call"):
        collect([completed([call(status=status)])], tools=TOOLS)


@pytest.mark.parametrize("status", ["in_progress", "incomplete", "failed"])
def test_completed_event_cannot_override_unsuccessful_response_status(status):
    with pytest.raises(EvalError, match="invalid_response_status"):
        collect([completed([message()], status=status)])


@pytest.mark.parametrize("status", ["in_progress", "incomplete", "failed"])
def test_explicit_incomplete_message_item_rejects_all_buffered_output(status):
    item = message()
    item["status"] = status
    with pytest.raises(EvalError, match="invalid_text_event"):
        collect([completed([item, call()])], tools=TOOLS)


def test_missing_legacy_status_and_unindexed_delta_remain_compatible():
    item = call()
    item.pop("status")
    event = completed([item])
    event["response"].pop("status")
    assert collect([event], tools=TOOLS)[0] == ToolCallEvent(
        "call-1", "query", {"value": 2}
    )
    assert collect(
        [{"type": "response.output_text.delta", "delta": TEXT}, completed()]
    )[0] == TextChunkEvent(TEXT)
    item = message()
    item.pop("status")
    event = completed([item])
    event["response"].pop("status")
    assert collect([event])[0] == TextChunkEvent(TEXT)


@pytest.mark.parametrize("source", ["text_done", "item_done", "completed"])
@pytest.mark.parametrize("legacy_text", [TEXT, TEXT[:8]])
def test_final_text_replaces_legacy_unindexed_deltas_without_duplication(
    source, legacy_text
):
    events = [
        {"type": "response.output_text.delta", "delta": legacy_text},
        *final_events(source),
    ]
    assert collect(events) == [TextChunkEvent(TEXT), DoneEvent(TokenUsage(7, 2))]


def test_mixed_indexed_and_unindexed_deltas_are_rejected_without_guessing_order():
    with pytest.raises(EvalError, match="invalid_text_event"):
        collect(
            [
                {"type": "response.output_text.delta", "delta": "unindexed"},
                text_event("indexed", kind="delta"),
                completed(),
            ]
        )


def test_recovered_message_rejects_boolean_output_index():
    with pytest.raises(EvalError, match="invalid_text_event"):
        collect([item_done(message(), True), completed()])


def test_real_sse_parser_delivers_finalized_text_to_strict_round(monkeypatch):
    import httpx
    from experiments.siwc_eval.http import LiveTransport

    wire = "".join(
        "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        for event in final_events("text_done")
    ).encode()
    factory = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: factory(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, headers={"Content-Type": "text/event-stream"}, content=wire
                )
            ),
            **kwargs,
        ),
    )
    result = asyncio.run(
        complete_provider_round(
            Provider("offline", LiveTransport("synthetic")), MESSAGES, [], 1.0
        )
    )
    assert result.text == TEXT
