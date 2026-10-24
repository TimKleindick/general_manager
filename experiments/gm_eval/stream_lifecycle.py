"""Strict, source-bound Responses lifecycle and opt-in local SIWC metadata policy.

See experiments/gm_eval/README.md#stream-lifecycle-contract for the local contract.
Metadata is consumed and validated; it grants no content, completion or retry
permission. Callers retain their independent HTTP/raw/history/source/cleanup gates.
"""

from collections.abc import Mapping, Sequence
import json
from typing import Any, NoReturn, cast

METADATA_CONTRACT_VERSION = "siwc-response-metadata-1"

# Closed observed flag vocabulary, not a claim about vendor category semantics.
_OMNIMOD_FLAGS = frozenset(
    (
        "A1",
        "A2",
        "A3",
        "C1",
        "C11",
        "C2",
        "CBRN-B1+",
        "E1",
        "E2",
        "E3",
        "H1",
        "H2",
        "H3",
        "IMG-C1",
        "IMG-C11",
        "IMG-C2",
        "IMG-S1",
        "IMG-SH",
        "IMG-V1",
        "IMG-V2+",
        "IMG-V4+",
        "JB1",
        "K1",
        "K2",
        "K3",
        "K4",
        "P1",
        "P2",
        "P3",
        "P4",
        "PD1",
        "PD4",
        "REFUSED",
        "S1",
        "S3+",
        "S4",
        "SH",
        "TXT-C3",
        "TXT-C4",
        "V1",
        "V2+",
        "V4+",
    )
)


def reject(reason: str) -> NoReturn:
    raise ValueError(reason)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def strict_json(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                reject("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=unique, parse_constant=reject)


def _object(value: Any, keys: set[str] | frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        reject("unsupported metadata object")
    return cast(dict[str, Any], value)


def _one(value: Any) -> Any:
    if type(value) is not list or len(value) != 1:
        reject("unsupported metadata cardinality")
    return value[0]


def verify_metadata(event: Any, response_id: str) -> None:
    """Consume exactly the local all-false annotation form; no safety inference."""
    value = _object(event, {"type", "sequence_number", "response_id", "metadata"})
    if (
        value["type"] != "response.metadata"
        or type(value["sequence_number"]) is not int
        or type(value["response_id"]) is not str
        or value["response_id"] != response_id
    ):
        reject("unbound metadata envelope")
    payload = _object(value["metadata"], {"openai_chatgpt_moderation_metadata"})
    scopes = _object(
        payload["openai_chatgpt_moderation_metadata"],
        {"prompt", "generation", "tool_call", "tool_response"},
    )
    for key in ("generation", "tool_call", "tool_response"):
        _object(scopes[key], set())
    prompt = _object(scopes["prompt"], {"omnimod"})
    omnimod = _object(prompt["omnimod"], {"protection_type", "outputs"})
    if omnimod["protection_type"] != "omnimod":
        reject("unsupported metadata protection type")
    output = _object(_one(omnimod["outputs"]), {"results", "is_blocked"})
    result = _object(_one(output["results"]), {"category_flags"})
    flags = _object(result["category_flags"], _OMNIMOD_FLAGS)
    if output["is_blocked"] is not False or any(
        flag is not False for flag in flags.values()
    ):
        reject("metadata requires conservative STOP")


def verify_stream_lifecycle(
    events: list[dict[str, Any]],
    *,
    function_call: Mapping[str, Any] | None = None,
    function_calls: Sequence[Mapping[str, Any]] | None = None,
    metadata_contract: str | None = None,
) -> None:
    """Verify every event in original order under an explicit closed contract.

    Default accepts the unchanged legacy stream language. Unknown contract
    versions and metadata variants fail closed, even if a response completed.
    """
    if metadata_contract is not None and metadata_contract != METADATA_CONTRACT_VERSION:
        reject("unsupported metadata contract")
    if (
        type(events) is not list
        or not events
        or any(type(e) is not dict for e in events)
    ):
        reject("malformed stream lifecycle")
    if function_call is not None and function_calls is not None:
        reject("ambiguous tool binding")
    tools = [function_call] if function_call is not None else function_calls
    if tools is not None:
        if not isinstance(tools, (list, tuple)) or not tools:
            reject("empty or unsupported tool binding")
        ids = set()
        for tool in tools:
            if (
                not isinstance(tool, Mapping)
                or type(tool.get("id")) is not str
                or not tool["id"]
                or tool["id"] in ids
                or type(tool.get("name")) is not str
                or not tool["name"]
                or type(tool.get("args")) is not dict
            ):
                reject("invalid or duplicate tool binding")
            ids.add(tool["id"])
    try:
        _verify_stream_lifecycle(
            events, function_calls=tools, metadata_contract=metadata_contract
        )
    except (KeyError, IndexError, TypeError, AttributeError) as error:
        message = "malformed stream lifecycle"
        raise ValueError(message) from error


def _verify_stream_lifecycle(
    events: list[dict[str, Any]],
    *,
    function_calls: Sequence[Mapping[str, Any]] | None,
    metadata_contract: str | None,
) -> None:
    """Accept only a closed, sequential reasoning/text stream; unknown forms fail closed."""
    cursor = 0

    def take(kind: str) -> dict[str, Any]:
        nonlocal cursor
        event = events[cursor]
        if event.get("type") != kind:
            reject("unexpected event or incomplete stream lifecycle")
        cursor += 1
        return event

    terminal = events[-1]["response"]
    if (
        terminal.get("status") != "completed"
        or not isinstance(terminal.get("id"), str)
        or not terminal["id"]
        or not isinstance(terminal.get("model"), str)
        or not terminal["model"]
        or terminal.get("error") is not None
        or terminal.get("incomplete_details") is not None
        or not isinstance(terminal.get("output"), list)
        or any(
            type(e.get("sequence_number")) is not int or e["sequence_number"] != i
            for i, e in enumerate(events)
        )
    ):
        reject("invalid terminal or sequence lifecycle")
    for kind in ("response.created", "response.in_progress"):
        response = take(kind)["response"]
        if (
            response.get("id") != terminal.get("id")
            or response.get("model") != terminal.get("model")
            or response.get("status") != "in_progress"
            or response.get("error") is not None
            or response.get("incomplete_details") is not None
            or response.get("output") != []
        ):
            reject("inconsistent response lifecycle")

    item_ids = set()
    tool_index = 0
    output_index = 0
    message_closed = False
    completed = []
    while events[cursor].get("type") == "response.output_item.added":
        start = take("response.output_item.added")
        item = start["item"]
        kind = item.get("type")
        if (
            message_closed
            or kind
            not in (
                {"reasoning", "message"}
                if function_calls is None
                else {"reasoning", "function_call"}
            )
            or type(start.get("output_index")) is not int
            or start["output_index"] != output_index
            or not isinstance(item.get("id"), str)
            or not item["id"]
            or item["id"] in item_ids
            or item.get("status") not in {None, "in_progress"}
        ):
            reject("unsupported or conflicting item start")
        item_ids.add(item["id"])

        def content_event(
            event: dict[str, Any],
            item_id: str = item["id"],
            item_index: int = output_index,
        ) -> dict[str, Any]:
            if (
                event.get("item_id") != item_id
                or type(event.get("output_index")) is not int
                or event["output_index"] != item_index
                or type(event.get("content_index")) is not int
                or event["content_index"] != 0
            ):
                reject("orphan or conflicting content event")
            return event

        if kind == "message":
            if item.get("role") != "assistant" or item.get("content") != []:
                reject("invalid message start")
            part_start = content_event(take("response.content_part.added"))["part"]
            if part_start.get("type") != "output_text" or part_start.get("text") != "":
                reject("unsupported content part start")
            chunks = []
            while events[cursor].get("type") == "response.output_text.delta":
                delta = content_event(take("response.output_text.delta"))["delta"]
                if not isinstance(delta, str):
                    reject("non-text delta")
                chunks.append(delta)
            if events[cursor].get("type") == "response.metadata":
                if not chunks or metadata_contract != METADATA_CONTRACT_VERSION:
                    reject("unsupported metadata contract or position")
                verify_metadata(take("response.metadata"), terminal["id"])
            text_end = content_event(take("response.output_text.done"))["text"]
            if not chunks or "".join(chunks) != text_end:
                reject("unfinished or inconsistent text")
            part_end = content_event(take("response.content_part.done"))["part"]
            if canonical(part_end) != canonical({**part_start, "text": text_end}):
                reject("content part changed outside streamed text")
            expected = {**item, "status": "completed", "content": [part_end]}
            message_closed = True
        elif kind == "function_call":
            if function_calls is None or tool_index >= len(function_calls):
                reject("function call outside bound tool lifecycle")
            function_call = function_calls[tool_index]
            if (
                item.get("arguments") != ""
                or item.get("status") != "in_progress"
                or item.get("call_id") != function_call["id"]
                or item.get("name") != function_call["name"]
            ):
                reject("invalid function call start")

            def argument_event(
                event: dict[str, Any],
                item_id: str = item["id"],
                item_index: int = output_index,
            ) -> dict[str, Any]:
                if (
                    event.get("item_id") != item_id
                    or type(event.get("output_index")) is not int
                    or event["output_index"] != item_index
                ):
                    reject("orphan or conflicting function argument event")
                return event

            chunks = []
            while (
                events[cursor].get("type") == "response.function_call_arguments.delta"
            ):
                delta = argument_event(take("response.function_call_arguments.delta"))[
                    "delta"
                ]
                if not isinstance(delta, str):
                    reject("non-text function argument delta")
                chunks.append(delta)
            if events[cursor].get("type") == "response.metadata":
                if (
                    not chunks
                    or metadata_contract != METADATA_CONTRACT_VERSION
                    or tool_index != len(function_calls) - 1
                ):
                    reject("unsupported tool metadata contract or position")
                verify_metadata(take("response.metadata"), terminal["id"])
            arguments = argument_event(take("response.function_call_arguments.done"))[
                "arguments"
            ]
            if (
                not chunks
                or "".join(chunks) != arguments
                or canonical(strict_json(arguments)) != canonical(function_call["args"])
            ):
                reject("unfinished or inconsistent function arguments")
            expected = {**item, "status": "completed", "arguments": arguments}
            tool_index += 1
            message_closed = tool_index == len(function_calls)
        else:
            # Reasoning payloads are redacted. Support only opaque start/done
            # pairs with identical recorded metadata; no unverified inner events.
            if tool_index:
                reject("reasoning interposed in tool sequence")
            expected = item
        end = take("response.output_item.done")
        if (
            type(end.get("output_index")) is not int
            or end["output_index"] != output_index
            or canonical(end.get("item")) != canonical(expected)
        ):
            reject("item did not close with its bound content")
        completed.append(end["item"])
        output_index += 1
    take("response.completed")
    if (
        cursor != len(events)
        or not message_closed
        or (function_calls is not None and tool_index != len(function_calls))
        or (
            terminal["output"] and canonical(terminal["output"]) != canonical(completed)
        )
    ):
        reject("open or trailing stream lifecycle")
