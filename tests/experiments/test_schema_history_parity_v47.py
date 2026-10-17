"""Parity verifies durable provenance before comparing route-local locators."""

from copy import deepcopy
import json

import pytest

from experiments.gm_eval.history_parity import history_messages, provider_messages
from general_manager.chat.planned.planner_context import project_planner_history
from general_manager.chat.planned.schema_projection import with_historical_schema
from general_manager.chat.providers.base import Message


def fixture(conversation="1", row="2"):
    native = {
        "contract_version": 2,
        "inspection_version": 3,
        "manager": "Part",
        "schema_view": "full",
        "snapshot": "a" * 64,
        "schema_complete": True,
        "types": {
            f"Input{i}": {"kind": "input", "fields": {"value": {"type": "String"}}}
            for i in range(100)
        },
    }
    origin = {
        "conversation_id": conversation,
        "message_id": row,
        "tool_args": {"manager": "Part", "view": "full"},
    }
    original = with_historical_schema(
        Message(
            "assistant",
            "Historical tool data (get_manager_schema): "
            + json.dumps(native, sort_keys=True),
        ),
        tool_name="get_manager_schema",
        tool_result=native,
        binding=origin,
    )
    projected = project_planner_history([original])[0]
    source = {
        "durable_index": 1,
        "origin": origin,
        "original": {"role": original.role, "content": original.content},
        "projected": {"role": projected.role, "content": projected.content},
    }
    reference = {
        "planner_context_version": "gm.planner-context/1",
        "user_text": "Read it",
        "conversation_context": [source["projected"]],
    }
    call = {
        "role": "planner",
        "messages": [
            {
                "role": "user",
                "content": "REFERENCE_DATA="
                + json.dumps(reference, ensure_ascii=False),
            }
        ],
    }
    return source, call


def test_verified_route_local_ids_compare_equal_without_changing_raw_requests():
    left, lc = fixture()
    right, rc = fixture("8", "93")
    before = deepcopy(lc)
    assert lc != rc
    assert provider_messages(lc, [left]) == provider_messages(rc, [right])
    assert lc == before
    assert history_messages([left["projected"]], [left]) == [left["original"]]


@pytest.mark.parametrize(
    "field",
    [
        "conversation_id",
        "message_id",
        "tool_args",
        "snapshot",
        "manager",
        "schema_view",
        "inspection_version",
        "original_content_sha256",
        "original_payload_sha256",
        "reload",
        "current_schema_or_completion_authority",
        "extra",
        "role",
    ],
)
def test_changed_origin_or_semantics_cannot_be_normalized_away(field):
    source, call = fixture()
    ref = json.loads(call["messages"][0]["content"].split("=", 1)[1])
    item = ref["conversation_context"][0]
    pointer = json.loads(item["content"].split("=", 1)[1])
    if field in {"conversation_id", "message_id", "tool_args"}:
        pointer["origin"][field] = "foreign"
    elif field == "role":
        item["role"] = "tool"
    else:
        pointer[field] = "corrupt"
    item["content"] = "HISTORICAL_SCHEMA_REFERENCE=" + json.dumps(
        pointer, separators=(",", ":")
    )
    call["messages"][0]["content"] = "REFERENCE_DATA=" + json.dumps(ref)
    with pytest.raises(ValueError, match="invalid_parity_schema_history"):
        provider_messages(call, [source])


def test_missing_pointer_and_changed_non_schema_data_still_fail_parity():
    source, call = fixture()
    expected = provider_messages(call, [source])
    for change in ("literal", "user"):
        altered = deepcopy(call)
        ref = json.loads(altered["messages"][0]["content"].split("=", 1)[1])
        if change == "literal":
            ref["conversation_context"] = [source["original"]]
        else:
            ref["user_text"] = "Changed choice or unit"
        altered["messages"][0]["content"] = "REFERENCE_DATA=" + json.dumps(ref)
        assert provider_messages(altered, [source]) != expected


def test_unqualified_user_text_is_never_interpreted_as_schema_origin():
    source, _call = fixture()
    user = {"role": "user", "content": source["projected"]["content"]}
    assert history_messages([user], []) == [user]


def test_duplicate_source_locator_and_unresolved_reference_fail_closed():
    source, call = fixture()
    for sources in ([], [source, source]):
        with pytest.raises(ValueError, match="invalid_parity_schema_history"):
            provider_messages(call, sources)


def test_equal_payloads_from_different_durable_rows_remain_distinct():
    first, left = fixture()
    second, right = fixture(row="3")
    second["durable_index"] = 2
    sources = [first, second]
    assert provider_messages(left, sources) != provider_messages(right, sources)


def test_all_non_reference_messages_and_fields_remain_exact():
    source, call = fixture()
    call["messages"].insert(
        0,
        {
            "role": "system",
            "content": "Keep C04 and kilograms",
            "other": {"value": 120},
        },
    )
    before = deepcopy(call["messages"][0])
    assert provider_messages(call, [source])[0] == before


@pytest.mark.parametrize("left,right", [(True, 1), (1, 1.0)])
def test_json_scalar_types_remain_exact_in_non_schema_reference_fields(left, right):
    source, call = fixture()
    compared = []
    for value in (left, right):
        altered = deepcopy(call)
        ref = json.loads(altered["messages"][0]["content"].split("=", 1)[1])
        ref["verbatim_application_value"] = value
        altered["messages"][0]["content"] = "REFERENCE_DATA=" + json.dumps(ref)
        compared.append(provider_messages(altered, [source]))
    assert compared[0] != compared[1]


def test_duplicate_reference_json_keys_cannot_disappear_in_comparison():
    source, call = fixture()
    call["messages"][0]["content"] = call["messages"][0]["content"].replace(
        '"user_text": "Read it"', '"user_text": "Changed", "user_text": "Read it"'
    )
    with pytest.raises(ValueError, match="invalid_parity_schema_history"):
        provider_messages(call, [source])
