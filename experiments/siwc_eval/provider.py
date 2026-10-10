"""Opt-in Responses adapter, deliberately outside the distributable package."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import aclosing
from copy import deepcopy
import json
from types import MappingProxyType
from typing import Any, Protocol

from general_manager.chat.providers.base import (
    ChatEvent,
    DoneEvent,
    Message,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
    ToolDefinition,
    TOOL_RESULT_MISSING,
)


from .errors import EvalError, fail, diagnostic


class Transport(Protocol):
    def stream(self, body: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]: ...


def encode(messages: list[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            if not message.tool_call_id:
                fail("missing_tool_call_id")
            output = (
                message.content
                if message.tool_result is TOOL_RESULT_MISSING
                else json.dumps(message.tool_result)
            )
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": output,
                }
            )
            continue
        if message.role not in {"system", "developer", "user", "assistant"}:
            fail("unsupported_role")
        if message.content:
            items.append(
                {
                    "role": "developer" if message.role == "system" else message.role,
                    "content": message.content,
                }
            )
        for call in message.tool_calls:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "namespace": "gm",
                    "arguments": json.dumps(call.args),
                }
            )
    return items


def payload(
    model: str, messages: list[Message], tools: list[ToolDefinition]
) -> dict[str, Any]:
    return {
        "model": model,
        "input": encode(messages),
        "store": False,
        "stream": True,
        "include": ["reasoning.encrypted_content"],
        "tools": [
            {
                "type": "namespace",
                "name": "gm",
                "description": "GeneralManager synthetic evaluation tools",
                "tools": [
                    {
                        "type": "function",
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                        "strict": False,
                    }
                    for tool in tools
                ],
            }
        ]
        if tools
        else [],
    }


def _recover_text(
    events: list[dict[str, Any]],
    completed_items: dict[int, dict[str, Any]],
    terminal_output: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Reconcile observed text after completion without duplicating snapshots."""
    deltas: dict[tuple[int, int], list[str]] = {}
    finalized: dict[tuple[int, int], str] = {}
    item_ids: dict[int, str] = {}
    item_indices: dict[str, int] = {}
    messages: dict[int, dict[str, Any]] = {}
    part_types: dict[int, tuple[str, ...]] = {}
    legacy_deltas: list[str] = []

    def bind_item(index: int, item_id: object) -> None:
        if item_id is None:
            return
        if not isinstance(item_id, str) or not item_id:
            fail("invalid_text_event")
        if (
            item_ids.get(index, item_id) != item_id
            or item_indices.get(item_id, index) != index
        ):
            fail("conflicting_text_output")
        item_ids[index] = item_id
        item_indices[item_id] = index

    def set_final(key: tuple[int, int], text: object) -> None:
        if not isinstance(text, str):
            fail("invalid_text_event")
        if key in finalized and finalized[key] != text:
            fail("conflicting_text_output")
        finalized[key] = text

    for event in events:
        is_delta = event["type"] == "response.output_text.delta"
        text = event["delta" if is_delta else "text"]
        if not isinstance(text, str):
            fail("invalid_text_event")
        if is_delta and not any(
            field in event for field in ("output_index", "content_index", "item_id")
        ):
            # Older local replay fixtures provide a single unindexed text stream.
            legacy_deltas.append(text)
            continue
        index, part = event.get("output_index"), event.get("content_index")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or isinstance(part, bool)
            or not isinstance(part, int)
            or part < 0
            or not isinstance(event.get("item_id"), str)
            or not event["item_id"]
        ):
            fail("invalid_text_event")
        bind_item(index, event["item_id"])
        key = (index, part)
        if is_delta:
            deltas.setdefault(key, []).append(text)
        else:
            set_final(key, text)

    if legacy_deltas and deltas:
        # Missing coordinates cannot be guessed or assigned to another item.
        fail("invalid_text_event")

    # Terminal messages have the richest replay metadata; final text must still
    # agree with any earlier finalized representation of the same identified part.
    for index, item in [*sorted(completed_items.items()), *enumerate(terminal_output)]:
        if item.get("type") != "message":
            continue
        if (
            isinstance(index, bool)
            or item.get("status", "completed") != "completed"
            or item.get("role", "assistant") != "assistant"
            or not isinstance(item.get("content"), list)
        ):
            fail("invalid_text_event")
        bind_item(index, item.get("id"))
        kinds: list[str] = []
        for part, content in enumerate(item["content"]):
            if not isinstance(content, dict) or not isinstance(
                content.get("type"), str
            ):
                fail("invalid_text_event")
            kinds.append(content["type"])
            if content["type"] == "output_text":
                set_final((index, part), content.get("text"))
        if index in part_types and part_types[index] != tuple(kinds):
            fail("conflicting_text_output")
        part_types[index] = tuple(kinds)
        messages[index] = item

    output = (
        dict(enumerate(terminal_output)) if terminal_output else dict(completed_items)
    )
    text_by_item: dict[int, list[str]] = {}
    for key in sorted(deltas.keys() | finalized.keys()):
        index, part = key
        if index in part_types and (
            part >= len(part_types[index]) or part_types[index][part] != "output_text"
        ):
            fail("conflicting_text_output")
        if index in output and output[index].get("type") != "message":
            fail("conflicting_text_output")
        # Final values replace all fragments, including an observed partial stream.
        text_by_item.setdefault(index, []).append(
            finalized[key] if key in finalized else "".join(deltas[key])
        )
    content = "".join("".join(parts) for parts in text_by_item.values())
    for index, parts in text_by_item.items():
        if index not in output:
            # Preserve real finalized items verbatim when present. A text-only
            # event has no full item to replay: use known text, never invented IDs.
            output[index] = messages.get(index) or {
                "role": "assistant",
                "content": "".join(parts),
            }
    replay = [output[index] for index in sorted(output)]
    if legacy_deltas and not text_by_item and not messages:
        content = "".join(legacy_deltas)
        if content:
            replay.append({"role": "assistant", "content": content})
    return content, replay


class Provider:
    """One sequential synthetic conversation; no registration in product settings.

    Buffers results until response.completed, so a failed stream never executes
    tools. Raw output (including encrypted reasoning) is replayed on continuation.
    Create a fresh provider for each case; concurrent use is rejected.
    """

    def __init__(
        self, model: str, transport: Transport, *, max_requests: int = 3
    ) -> None:
        if (
            not model
            or isinstance(max_requests, bool)
            or not isinstance(max_requests, int)
            or not 1 <= max_requests <= 256
        ):
            fail("invalid_configuration")
        self._config: Mapping[str, Any] = MappingProxyType(
            {"model": model, "max_requests": max_requests}
        )
        self.transport = transport
        self.requests = 0
        self.stopped = False
        self.busy = False
        self._prefix: list[Message] = []
        self._raw: list[dict[str, Any]] = []

    @property
    def provider_config(self) -> Mapping[str, Any]:
        return self._config

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> Provider:
        # Deliberately cannot load personal credentials through Django settings.
        fail("use_explicit_local_runner")

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[ChatEvent]:
        if self.stopped:
            fail("provider_stopped")
        if self.busy:
            fail("concurrent_use_not_supported")
        if self.requests >= self._config["max_requests"]:
            self.stopped = True
            fail("request_budget_exhausted")
        self.busy = True
        try:
            body = payload(str(self._config["model"]), messages, tools)
            if self._prefix and messages[: len(self._prefix)] == self._prefix:
                body["input"] = deepcopy(self._raw) + encode(
                    messages[len(self._prefix) :]
                )
            if len(json.dumps(body)) > 200_000:
                fail("input_budget_exhausted")
            self.requests += 1
            text_events: list[dict[str, Any]] = []
            response = None
            completed_items: dict[int, dict[str, Any]] = {}
            async with (
                asyncio.timeout(90),
                aclosing(self.transport.stream(body)) as events,
            ):
                async for event in events:
                    kind = event.get("type")
                    if kind in {
                        "response.output_text.delta",
                        "response.output_text.done",
                    }:
                        text_events.append(event)
                    elif kind == "response.output_item.done":
                        index = event.get("output_index", len(completed_items))
                        if (
                            not isinstance(index, int)
                            or index < 0
                            or index in completed_items
                        ):
                            fail("invalid_output_index")
                        completed_items[index] = event["item"]
                    elif kind in {"response.failed", "response.incomplete", "error"}:
                        raise EvalError(  # noqa: TRY301 -- terminal protocol failure
                            str(kind), diagnostic(event.get("response", event))
                        )
                    elif kind == "response.completed":
                        response = event["response"]
                        break
            if response is None:
                fail("stream_without_completion")
            if response.get("status", "completed") != "completed":
                fail("invalid_response_status")
            # SIWC may leave terminal output empty; completed item events carry
            # the actual output. Release only after response.completed.
            output = response.get("output") or [
                completed_items[index] for index in sorted(completed_items)
            ]
            calls: list[ToolCallEvent] = []
            available = {tool.name for tool in tools}
            for item in output:
                if item.get("type") == "function_call":
                    args = json.loads(item["arguments"])
                    if (
                        item["name"] not in available
                        or item.get("namespace", "gm") != "gm"
                        or item.get("status", "completed") != "completed"
                        or not isinstance(args, dict)
                        or not item.get("call_id")
                    ):
                        fail("invalid_tool_call")
                    calls.append(ToolCallEvent(item["call_id"], item["name"], args))
            if len({call.id for call in calls}) != len(calls):
                fail("duplicate_tool_call")
            content, replay_output = _recover_text(
                text_events, completed_items, response.get("output") or []
            )
            self._prefix = [
                *messages,
                Message("assistant", "" if calls else content, tool_calls=tuple(calls)),
            ]
            self._raw = deepcopy(body["input"] + replay_output)
            usage = response.get("usage") or {}
            result_usage = TokenUsage(
                int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            )
            if content:
                yield TextChunkEvent(content)
            for call in calls:
                yield call
            yield DoneEvent(result_usage)
        except asyncio.CancelledError:
            self.stopped = True
            raise
        except EvalError:
            self.stopped = True
            raise
        except Exception:  # noqa: BLE001 -- never leak transport exceptions/tokens
            self.stopped = True
            raise EvalError("transport_error") from None
        finally:
            self.busy = False
