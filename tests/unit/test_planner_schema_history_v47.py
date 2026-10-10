"""Only durable, exact native schema origins may become planner reload refs."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from general_manager.chat.planned.planner import _request_messages
from general_manager.chat.planned.schema_projection import with_historical_schema
from general_manager.chat.providers.base import Message, TOOL_RESULT_MISSING


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def schema(view="overview", *, version=3):
    value = {
        "contract_version": 2,
        "manager": "Part",
        "schema_view": view,
        "snapshot": "a" * 64,
        "schema_complete": view == "full",
    }
    if version is not None:
        value["inspection_version"] = version
    defs = {
        f"VisibleGeneratedInputType{i:03}": {
            "kind": "input",
            "fields": {
                "value": {
                    "type": "String",
                    "description": "DETAILS_ONLY_IN_NATIVE_ORIGINAL",
                }
            },
        }
        for i in range(150)
    }
    if view == "overview":
        value.update(
            {
                "type": "PartType",
                "roots": ["partList"],
                "root_fields": {},
                "output_fields": {},
                "type_manifest": {k: {"kind": "input"} for k in defs},
            }
        )
    else:
        value["types"] = defs
    return value


def history(value=None, *, row="1", role="assistant", sort_keys=True, args=None):
    value = schema() if value is None else value
    args = (
        {"manager": "Part", "view": value.get("schema_view", "overview")}
        if args is None
        else args
    )
    if value.get("schema_view") == "detail":
        args = {**args, "types": list(value["types"]), "snapshot": value["snapshot"]}
    prefix = (
        "Historical tool data (get_manager_schema): " if role == "assistant" else ""
    )
    content = prefix + json.dumps(value, sort_keys=sort_keys)
    return with_historical_schema(
        Message(role, content),
        tool_name="get_manager_schema",
        tool_result=value,
        binding={"conversation_id": "fixture/c1", "message_id": row, "tool_args": args},
    )


def snapshot(value):
    return deepcopy(value, {id(TOOL_RESULT_MISSING): TOOL_RESULT_MISSING})


def reference(messages):
    result = _request_messages(
        "Read the selected population", messages, {}, correction=False
    )
    return json.loads(result[-1].content.split("=", 1)[1])


def pointer(context):
    assert context["content"].startswith("HISTORICAL_SCHEMA_REFERENCE=")
    return json.loads(context["content"].split("=", 1)[1])


@pytest.mark.parametrize("view", ["overview", "detail", "full"])
@pytest.mark.parametrize("sort_keys", [True, False])
def test_schema_history_is_temporarily_projected_with_exact_native_origin(
    view, sort_keys
):
    value = schema(view)
    message = history(value, sort_keys=sort_keys)
    before = snapshot(message)
    ref = reference([message])
    assert ref["planner_context_version"] == "gm.planner-context/1"
    item = pointer(ref["conversation_context"][0])
    assert item["manager"] == "Part"
    assert item["snapshot"] == value["snapshot"]
    assert item["schema_view"] == view and item["inspection_version"] == 3
    assert (
        item["original_content_sha256"] == sha256(message.content.encode()).hexdigest()
    )
    assert item["original_payload_sha256"] == sha256(dump(value).encode()).hexdigest()
    assert item["origin"]["message_id"] == "1"
    assert item["reload"]["tool"] == "get_manager_schema"
    assert item["reload"]["arguments"]["manager"] == "Part"
    assert item["reload"]["arguments"]["view"] == view
    if view == "detail":
        assert set(item["reload"]["arguments"]["types"]) == set(value["types"])
        assert item["reload"]["arguments"]["snapshot"] == value["snapshot"]
    assert len(json.dumps(item)) < len(json.dumps(message.content))
    assert message == before
    assert json.loads(message.historical_schema.payload_json) == value


def test_query_user_choices_and_unit_text_remain_complete_and_ordered():
    value = schema()
    rows = "QUERY_IDENTITY_ROWS=" + dump([value, value])
    choices = "Choose C04 or C05; selected C04 means 120 pieces at 2 kg/piece."
    messages = [
        Message("user", choices),
        history(value),
        Message("assistant", rows),
        Message("tool", rows, tool_name="query", tool_result=[value, value]),
        Message("user", "C04; 2 kg/piece"),
    ]
    original = snapshot(messages)
    contexts = reference(messages)["conversation_context"]
    assert [x["role"] for x in contexts] == [m.role for m in messages]
    for i in [0, 2, 3, 4]:
        assert contexts[i]["content"] == messages[i].content
    pointer(contexts[1])
    assert messages == original


def test_equal_schema_payloads_retain_each_independent_record_origin():
    messages = [history(row="11"), history(row="12")]
    refs = [pointer(m) for m in reference(messages)["conversation_context"]]
    assert refs[0]["original_payload_sha256"] == refs[1]["original_payload_sha256"]
    assert [m["origin"]["message_id"] for m in refs] == ["11", "12"]


@pytest.mark.parametrize(
    "change",
    [
        "unattested",
        "legacy",
        "error",
        "unknown_version",
        "missing_args",
        "old_annotation",
    ],
)
def test_unqualified_or_legacy_history_remains_fully_visible(change):
    value = schema()
    if change == "legacy":
        value.pop("schema_view")
    elif change == "error":
        value = {"status": "error", "code": "schema_snapshot_mismatch"}
    elif change == "unknown_version":
        value["inspection_version"] = 99
    message = history(value)
    if change == "unattested":
        message = replace(message, historical_schema=None)
    elif change == "missing_args":
        binding = json.loads(message.historical_schema.binding)
        binding["origin"]["tool_args"] = None
        message = replace(
            message,
            historical_schema=replace(message.historical_schema, binding=dump(binding)),
        )
    elif change == "old_annotation":
        message = replace(
            message,
            historical_schema=replace(message.historical_schema, payload_json=None),
        )
    assert reference([message])["conversation_context"][0]["content"] == message.content


@pytest.mark.parametrize(
    "change",
    [
        "content",
        "native_payload",
        "manager_args",
        "snapshot_args",
        "origin_payload_hash",
    ],
)
def test_corrupted_attestation_is_rejected_before_a_provider_call(change):
    message = history(schema("detail"))
    origin = message.historical_schema
    if change == "content":
        message = replace(message, content=message.content + " ")
    elif change == "native_payload":
        altered = json.loads(origin.payload_json)
        altered["manager"] = "Foreign"
        message = replace(
            message, historical_schema=replace(origin, payload_json=dump(altered))
        )
    else:
        binding = json.loads(origin.binding)
        if change == "manager_args":
            binding["origin"]["tool_args"]["manager"] = "Foreign"
        elif change == "snapshot_args":
            binding["origin"]["tool_args"]["snapshot"] = "b" * 64
        else:
            binding["payload_sha256"] = "0" * 64
        message = replace(
            message, historical_schema=replace(origin, binding=dump(binding))
        )
    with pytest.raises(ValueError, match=r"invalid_.*projection"):
        reference([message])


def test_old_protocol_schema_can_only_be_reloaded_with_its_original_binding():
    value = schema("detail", version=None)
    item = pointer(reference([history(value)])["conversation_context"][0])
    assert item["inspection_version"] == 2
    assert item["reload"]["arguments"]["snapshot"] == "a" * 64
    assert item["current_schema_or_completion_authority"] is False


@pytest.mark.parametrize(
    "change", ["bool_version", "malformed_view", "no_locator", "foreign_tool", "tiny"]
)
def test_unqualified_source_or_unprofitable_projection_keeps_exact_content(change):
    value = schema()
    if change == "bool_version":
        value["inspection_version"] = True
    elif change == "malformed_view":
        value["schema_view"] = ["overview"]
    elif change == "tiny":
        value["type_manifest"] = {}
    message = history(value)
    if change in {"no_locator", "foreign_tool"}:
        binding = json.loads(message.historical_schema.binding)
        if change == "no_locator":
            del binding["origin"]["message_id"]
        else:
            binding["tool"] = "query"
        message = replace(
            message,
            historical_schema=replace(message.historical_schema, binding=dump(binding)),
        )
    assert reference([message])["conversation_context"][0]["content"] == message.content


def test_real_persistence_adapter_retains_native_bytes_independently_of_text_sorting():
    from general_manager.chat.models import provider_messages_from_context

    value = schema()
    row = SimpleNamespace(
        pk=17,
        conversation_id="fixture/c1",
        role="tool",
        tool_calls=None,
        tool_call_id=None,
        tool_name="get_manager_schema",
        tool_args={"manager": "Part"},
        tool_result=value,
        content=json.dumps(value, sort_keys=True),
    )
    messages = provider_messages_from_context([row])
    assert len(messages) == 1 and messages[0].role == "assistant"
    assert messages[0].historical_schema.payload_json == dump(value)
    item = pointer(reference(messages)["conversation_context"][0])
    assert item["origin"]["message_id"] == "17"
    assert item["original_payload_sha256"] == sha256(dump(value).encode()).hexdigest()
    assert row.tool_result == value and row.content == json.dumps(value, sort_keys=True)
