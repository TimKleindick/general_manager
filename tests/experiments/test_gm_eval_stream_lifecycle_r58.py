"""Closed transport syntax controls; no new model-quality or historical score claim."""

from copy import deepcopy
from hashlib import sha256
import importlib
import importlib.util
import inspect
import json
from pathlib import Path

import pytest


def api():
    name = "experiments.gm_eval.stream_lifecycle"
    assert importlib.util.find_spec(name) is not None, "Shared lifecycle owner missing"
    return importlib.import_module(name)


def captured_stream(index=0):
    return json.loads(
        Path(__file__).with_name("data").joinpath("e078_streams_r58.json").read_text()
    )["streams"][index]["events"]


def verify(events, **kwargs):
    owner = api()
    return owner.verify_stream_lifecycle(
        events, metadata_contract=owner.METADATA_CONTRACT_VERSION, **kwargs
    )


def minimal_stream(*, function=False):
    """Independent metadata-free synthetic text/tool lifecycle, not edited evidence."""
    response = {
        "id": "independent-response",
        "model": "model",
        "status": "in_progress",
        "output": [],
    }
    item = (
        {
            "id": "item",
            "type": "function_call",
            "status": "in_progress",
            "arguments": "",
            "call_id": "call",
            "name": "query",
        }
        if function
        else {
            "id": "item",
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        }
    )
    events = [
        {"type": "response.created", "response": deepcopy(response)},
        {"type": "response.in_progress", "response": deepcopy(response)},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": deepcopy(item),
        },
    ]
    if function:
        events.extend(
            [
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "item_id": "item",
                    "delta": "{}",
                },
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": 0,
                    "item_id": "item",
                    "arguments": "{}",
                },
            ]
        )
        complete = {**item, "arguments": "{}", "status": "completed"}
    else:
        part = {"type": "output_text", "text": ""}
        common = {"output_index": 0, "content_index": 0, "item_id": "item"}
        events.extend(
            [
                {"type": "response.content_part.added", **common, "part": part},
                {"type": "response.output_text.delta", **common, "delta": "hello"},
                {"type": "response.output_text.done", **common, "text": "hello"},
                {
                    "type": "response.content_part.done",
                    **common,
                    "part": {**part, "text": "hello"},
                },
            ]
        )
        complete = {
            **item,
            "status": "completed",
            "content": [{**part, "text": "hello"}],
        }
    events.extend(
        [
            {"type": "response.output_item.done", "output_index": 0, "item": complete},
            {
                "type": "response.completed",
                "response": {**response, "status": "completed", "output": [complete]},
            },
        ]
    )
    for i, event in enumerate(events):
        event["sequence_number"] = i
    return events


@pytest.mark.parametrize("index", [0, 1])
def test_original_complete_metadata_stream_consumed_without_mutation(index):
    events = captured_stream(index)
    original = deepcopy(events)
    assert verify(events) is None
    assert events == original
    expected_hash = (
        "1ba1ac5ad6098e7495f39eead5754f0ba215d310560ace7fba2d76793176233e"
        if index == 0
        else "ddee34e1bf311c859b3436d43ed121b368f133710b4fcff3351e9066c5759d48"
    )
    assert (
        sha256((json.dumps(events, indent=2) + "\n").encode()).hexdigest()
        == expected_hash
    )
    metadata = [e for e in events if e["type"] == "response.metadata"]
    assert len(metadata) == 1
    assert metadata[0]["sequence_number"] == (105 if index == 0 else 107)
    text = next(e["text"] for e in events if e["type"] == "response.output_text.done")
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


def test_metadata_requires_explicit_exact_version_and_remains_optional():
    owner = api()
    for version in [None, "", "future-contract", False, 1]:
        with pytest.raises(ValueError):
            owner.verify_stream_lifecycle(captured_stream(), metadata_contract=version)
    for function in [False, True]:
        kwargs = (
            {"function_call": {"id": "call", "name": "query", "args": {}}}
            if function
            else {}
        )
        events = minimal_stream(function=function)
        assert owner.verify_stream_lifecycle(events, **kwargs) is None
        assert verify(events, **kwargs) is None


def test_contract_is_response_and_sequence_bound_without_case_specific_ids():
    events = captured_stream()
    for event in events:
        if "response" in event:
            event["response"]["id"] = "different-valid-response"
        if event["type"] == "response.metadata":
            event["response_id"] = "different-valid-response"
    assert verify(events) is None


def test_metadata_in_final_function_arguments_requires_explicit_contract():
    events = minimal_stream(function=True)
    metadata = next(e for e in captured_stream() if e["type"] == "response.metadata")
    metadata["response_id"] = "independent-response"
    events.insert(4, metadata)
    for i, event in enumerate(events):
        event["sequence_number"] = i
    with pytest.raises(ValueError):
        api().verify_stream_lifecycle(
            events, function_call={"id": "call", "name": "query", "args": {}}
        )
    assert (
        verify(events, function_call={"id": "call", "name": "query", "args": {}})
        is None
    )


def original_tools():
    data = json.loads(
        Path(__file__).with_name("data").joinpath("e078_streams_r58.json").read_text()
    )
    return data["streams"][2]["expected_tools"]


def test_complete_original_multi_tool_stream_binds_every_declared_call():
    assert (
        "function_calls" in inspect.signature(api().verify_stream_lifecycle).parameters
    ), "Complete provider-tool binding missing"
    events = captured_stream(2)
    original = deepcopy(events)
    assert verify(events, function_calls=original_tools()) is None
    assert events == original
    assert (
        sha256((json.dumps(events, indent=2) + "\n").encode()).hexdigest()
        == "35c7981753882f258fb42d3162a35e3bc78fa04979487b6cbb5831ed25fb2609"
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "extra",
        "reordered",
        "duplicate",
        "wrong_id",
        "wrong_name",
        "wrong_args",
        "empty",
        "ambiguous_selector",
        "early_metadata",
        "wrong_item_index",
        "flag_true",
    ],
)
def test_complete_tool_binding_rejects_partial_or_conflicting_sources(damage):
    assert (
        "function_calls" in inspect.signature(api().verify_stream_lifecycle).parameters
    ), "Complete provider-tool binding missing"
    events = captured_stream(2)
    tools = original_tools()
    kwargs = {}
    if damage == "missing":
        tools.pop()
    elif damage == "extra":
        tools.append({"id": "extra", "name": "query", "args": {}})
    elif damage == "reordered":
        tools.reverse()
    elif damage == "duplicate":
        tools[1] = deepcopy(tools[0])
    elif damage == "wrong_id":
        tools[1]["id"] = "wrong"
    elif damage == "wrong_name":
        tools[1]["name"] = "other"
    elif damage == "wrong_args":
        tools[1]["args"]["manager"] = "Other"
    elif damage == "empty":
        tools = []
    elif damage == "ambiguous_selector":
        kwargs["function_call"] = tools[0]
    elif damage == "early_metadata":
        position = next(
            i for i, e in enumerate(events) if e["type"] == "response.metadata"
        )
        events.insert(4, events.pop(position))
        for i, event in enumerate(events):
            event["sequence_number"] = i
    elif damage == "wrong_item_index":
        events[6]["output_index"] = 0
    elif damage == "flag_true":
        value = next(e for e in events if e["type"] == "response.metadata")
        value["metadata"]["openai_chatgpt_moderation_metadata"]["prompt"]["omnimod"][
            "outputs"
        ][0]["is_blocked"] = True
    with pytest.raises(ValueError):
        verify(events, function_calls=tools, **kwargs)


@pytest.mark.parametrize(
    "damage",
    [
        "wrong_id",
        "missing_id",
        "bool_sequence",
        "sequence_gap",
        "sequence_duplicate",
        "extra_envelope",
        "missing_metadata",
        "unknown_metadata",
        "missing_scope",
        "extra_scope",
        "null_prompt",
        "list_empty_scope",
        "populated_generation",
        "unknown_prompt",
        "wrong_protection",
        "extra_omnimod",
        "zero_outputs",
        "two_outputs",
        "zero_results",
        "two_results",
        "extra_output",
        "extra_result",
        "flag_true",
        "flag_integer",
        "flag_null",
        "missing_flag",
        "renamed_flag",
        "extra_flag",
        "blocked_true",
        "blocked_integer",
        "missing_blocked",
        "wrong_event_type",
        "duplicate_metadata",
        "metadata_before_deltas",
        "metadata_after_text_done",
        "metadata_after_terminal",
        "delta_after_metadata",
        "failed_terminal",
        "terminal_error",
        "incomplete_terminal",
        "wrong_text_done",
        "wrong_part",
        "wrong_item",
        "wrong_terminal_id",
        "extra_unknown_event",
    ],
)
def test_unproved_metadata_or_lifecycle_variants_still_reject(damage):
    events = captured_stream()
    position = next(i for i, e in enumerate(events) if e["type"] == "response.metadata")
    event = events[position]
    metadata = event["metadata"]
    scopes = metadata["openai_chatgpt_moderation_metadata"]
    omnimod = scopes["prompt"]["omnimod"]
    output = omnimod["outputs"][0]
    result = output["results"][0]
    flags = result["category_flags"]
    if damage == "wrong_id":
        event["response_id"] = "other"
    elif damage == "missing_id":
        del event["response_id"]
    elif damage == "bool_sequence":
        event["sequence_number"] = True
    elif damage == "sequence_gap":
        event["sequence_number"] += 1
    elif damage == "sequence_duplicate":
        event["sequence_number"] -= 1
    elif damage == "extra_envelope":
        event["error"] = {"code": "blocked"}
    elif damage == "missing_metadata":
        del event["metadata"]
    elif damage == "unknown_metadata":
        metadata["future"] = {}
    elif damage == "missing_scope":
        del scopes["generation"]
    elif damage == "extra_scope":
        scopes["future"] = {}
    elif damage == "null_prompt":
        scopes["prompt"] = None
    elif damage == "list_empty_scope":
        scopes["tool_call"] = []
    elif damage == "populated_generation":
        scopes["generation"] = {"is_blocked": False}
    elif damage == "unknown_prompt":
        scopes["prompt"]["future"] = {}
    elif damage == "wrong_protection":
        omnimod["protection_type"] = "other"
    elif damage == "extra_omnimod":
        omnimod["future"] = None
    elif damage == "zero_outputs":
        omnimod["outputs"] = []
    elif damage == "two_outputs":
        omnimod["outputs"].append(deepcopy(output))
    elif damage == "zero_results":
        output["results"] = []
    elif damage == "two_results":
        output["results"].append(deepcopy(result))
    elif damage == "extra_output":
        output["future"] = None
    elif damage == "extra_result":
        result["is_blocked"] = False
    elif damage == "flag_true":
        flags["A1"] = True
    elif damage == "flag_integer":
        flags["A1"] = 0
    elif damage == "flag_null":
        flags["A1"] = None
    elif damage == "missing_flag":
        del flags["A1"]
    elif damage == "renamed_flag":
        flags["future"] = flags.pop("A1")
    elif damage == "extra_flag":
        flags["future"] = False
    elif damage == "blocked_true":
        output["is_blocked"] = True
    elif damage == "blocked_integer":
        output["is_blocked"] = 0
    elif damage == "missing_blocked":
        del output["is_blocked"]
    elif damage == "wrong_event_type":
        event["type"] = "response.future_metadata"
    elif damage == "duplicate_metadata":
        events.insert(position, deepcopy(event))
    elif damage == "metadata_before_deltas":
        events.insert(4, events.pop(position))
    elif damage == "metadata_after_text_done":
        events.insert(position + 1, events.pop(position))
    elif damage == "metadata_after_terminal":
        events.append(events.pop(position))
    elif damage == "delta_after_metadata":
        events.insert(position + 1, deepcopy(events[position - 1]))
    elif damage == "failed_terminal":
        events[-1]["type"] = "response.failed"
    elif damage == "terminal_error":
        events[-1]["response"]["error"] = {"code": "blocked"}
    elif damage == "incomplete_terminal":
        events[-1]["response"]["status"] = "incomplete"
    elif damage == "wrong_text_done":
        events[position + 1]["text"] += "changed"
    elif damage == "wrong_part":
        events[position + 2]["part"]["text"] += "changed"
    elif damage == "wrong_item":
        events[position + 3]["item"]["content"][0]["text"] += "changed"
    elif damage == "wrong_terminal_id":
        events[-1]["response"]["id"] = "other"
    elif damage == "extra_unknown_event":
        events.insert(position, {"type": "response.future"})
    if damage.startswith("metadata_") or damage in {
        "duplicate_metadata",
        "delta_after_metadata",
        "extra_unknown_event",
    }:
        for i, value in enumerate(events):
            value["sequence_number"] = i
    with pytest.raises(ValueError):
        verify(events)


@pytest.mark.parametrize("events", [[], [{}], [None], None, {}])
def test_malformed_or_incomplete_streams_fail_closed(events):
    with pytest.raises(ValueError):
        verify(events)
