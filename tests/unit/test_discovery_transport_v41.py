"""Discovery origin and cost-sensitive sharing, independent of any eval case."""

from copy import deepcopy
import json

import pytest

from general_manager.chat.planned.schema_projection import (
    SchemaSlot,
    expand_reference,
    history_slots,
    historical_schema_origin,
    project_reference,
    with_historical_schema,
)
from general_manager.chat.providers.base import Message


def discovery():
    return [
        {
            "contract_version": 2,
            "manager": "Order",
            "description": "Orders",
            "type": "database",
            "fields": [],
            "filters": [],
            "relations": [],
            "roots": [],
        }
    ]


@pytest.mark.parametrize("version", [2.0, True, "2", None])
def test_discovery_contract_version_requires_exact_json_integer(version):
    result = discovery()
    result[0]["contract_version"] = version
    text = "Historical tool data (search_managers): " + json.dumps(result)
    assert (
        historical_schema_origin(
            "assistant",
            text,
            tool_name="search_managers",
            tool_result=result,
            binding={},
        )
        is None
    )


def fixture(payload):
    ref = {"task_evidence": [{"kind": "schema", "payload": payload}]}
    slots = (
        SchemaSlot(("task_evidence", 0, "payload"), "task_schema", '{"snapshot":"a"}'),
    )
    return ref, slots


def test_discovery_history_requires_separate_structured_origin():
    result = discovery()
    text = "Historical tool data (search_managers): " + json.dumps(
        result, sort_keys=True
    )
    bare = Message("assistant", text)
    assert history_slots([bare]) == ()
    annotated = with_historical_schema(
        bare,
        tool_name="search_managers",
        tool_result=result,
        binding={
            "conversation_id": "c",
            "message_id": "m",
            "tool_args": {"query": "Order"},
        },
    )
    slots = history_slots([annotated])
    assert len(slots) == 1
    assert json.loads(slots[0].binding)["tool"] == "search_managers"
    ref = {"conversation_context": [{"role": "assistant", "content": text}]}
    assert expand_reference(project_reference(ref, slots), ref, slots) == ref


@pytest.mark.parametrize("mutation", ["user", "text", "result", "tool", "shape"])
def test_discovery_origin_rejects_unbound_or_wrong_data(mutation):
    result = discovery()
    text = "Historical tool data (search_managers): " + json.dumps(
        result, sort_keys=True
    )
    role, tool = "assistant", "search_managers"
    if mutation == "user":
        role = "user"
    elif mutation == "text":
        text += " "
    elif mutation == "result":
        result[0]["manager"] = "Foreign"
    elif mutation == "tool":
        tool = "query"
    else:
        result = [{"secret": "not a discovery result"}]
        text = "Historical tool data (search_managers): " + json.dumps(
            result, sort_keys=True
        )
    assert (
        historical_schema_origin(
            role, text, tool_name=tool, tool_result=result, binding={}
        )
        is None
    )


def test_small_repeated_default_container_is_profitably_shared():
    default = {"type": "OrderDirection!", "default": "ASC", "default_graphql": "ASC"}
    ref, slots = fixture({"directions": [deepcopy(default) for _ in range(42)]})
    projection = project_reference(ref, slots)
    assert default in projection["objects"]
    assert expand_reference(projection, ref, slots) == ref
    assert len(json.dumps(json.dumps(projection))) < len(json.dumps(json.dumps(ref)))


def test_small_one_off_values_remain_inline():
    ref, slots = fixture({"fields": [{"type": "Int"}, {"type": "String"}]})
    projection = project_reference(ref, slots)
    assert projection["objects"] == []
    assert expand_reference(projection, ref, slots) == ref


def test_repeated_defaults_keep_distinct_snapshot_bindings_and_literals():
    default = {"type": "Direction!", "default": "DOWN", "default_graphql": "DOWN"}
    payload = {"directions": [deepcopy(default) for _ in range(30)], "$gm_ref": 7}
    ref, slots = fixture(payload)
    ref["task_evidence"].append({"kind": "schema", "payload": deepcopy(payload)})
    slots += (
        SchemaSlot(("task_evidence", 1, "payload"), "task_schema", '{"snapshot":"b"}'),
    )
    projection = project_reference(ref, slots)
    assert [item["binding"] for item in projection["occurrences"]] == [
        '{"snapshot":"a"}',
        '{"snapshot":"b"}',
    ]
    assert expand_reference(projection, ref, slots) == ref
