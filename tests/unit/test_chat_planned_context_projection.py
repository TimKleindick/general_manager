"""Only exact, linked evidence copies may become compact history references."""

from dataclasses import replace
from copy import deepcopy
import json
import pytest
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.models import EvidenceRequirement, PlannedTask
from general_manager.chat.planned.scheduler import _executor_messages
from general_manager.chat.providers.base import Message, ToolCallEvent


def fixture(*, linked=True, payload=None):
    requirement = EvidenceRequirement("schema", "schema", "schema", None)
    task = PlannedTask("t", "inspect", (), (requirement,), ("schema",), ())
    args = {"manager": "Part"}
    payload = (
        {"fields": [{"description": "UNIQUE_SCHEMA_PAYLOAD"}]}
        if payload is None
        else payload
    )
    store = EvidenceStore()
    record = EvidenceRecord.create(
        "e",
        "t",
        "schema",
        canonical_call_identity("get_schema", args),
        {"tool": "get_schema"},
        payload,
    )
    store.add(record, requirement=requirement if linked else None)
    history = [
        Message(
            "assistant", "", tool_calls=(ToolCallEvent("call", "get_schema", args),)
        ),
        Message(
            "tool",
            json.dumps(payload),
            tool_call_id="call",
            tool_name="get_schema",
            tool_result=deepcopy(payload),
        ),
    ]
    return task, store, history


def test_exact_linked_payload_is_in_reference_data_once_without_mutation():
    task, store, history = fixture()
    before = deepcopy(history)
    messages = _executor_messages("inspect", task, store, tool_history=history)
    assert sum(m.content.count("UNIQUE_SCHEMA_PAYLOAD") for m in messages) == 1
    assert messages[-1].tool_result == {"evidence_ref": "e"}
    assert messages[-1].tool_call_id == "call"
    assert messages[-2].tool_calls == history[0].tool_calls
    assert history[-1] == before[-1]
    assert history[0].tool_calls == before[0].tool_calls
    assert history[0].content == before[0].content
    assert store.get("e").payload() == history[-1].tool_result


@pytest.mark.parametrize(
    "change",
    [
        "unlinked",
        "different_payload",
        "different_arguments",
        "wrong_name",
        "wrong_id",
        "error",
    ],
)
def test_unmatched_or_operational_results_stay_complete(change):
    task, store, history = fixture(
        linked=change != "unlinked",
        payload={"status": "error", "code": "private"} if change == "error" else None,
    )
    if change == "different_payload":
        history[-1] = replace(history[-1], content="{}", tool_result={})
    if change == "different_arguments":
        history[0] = replace(
            history[0],
            tool_calls=(ToolCallEvent("call", "get_schema", {"manager": "Other"}),),
        )
    if change == "wrong_name":
        history[-1] = replace(history[-1], tool_name="query")
    if change == "wrong_id":
        history[-1] = replace(history[-1], tool_call_id="other")
    messages = _executor_messages("inspect", task, store, tool_history=history)
    assert messages[-1] == history[-1]


def test_grouped_calls_keep_order_and_both_exact_results():
    task, store, history = fixture()
    second = EvidenceRecord.create(
        "e2",
        "t",
        "schema",
        canonical_call_identity("get_schema", {"manager": "Other"}),
        {},
        {"fields": ["other"]},
    )
    store.add(second, requirement=task.requirements[0])
    history[0] = replace(
        history[0],
        tool_calls=(
            *history[0].tool_calls,
            ToolCallEvent("call2", "get_schema", {"manager": "Other"}),
        ),
    )
    history.append(
        Message(
            "tool",
            json.dumps(second.payload()),
            tool_call_id="call2",
            tool_name="get_schema",
            tool_result=second.payload(),
        )
    )
    messages = _executor_messages("inspect", task, store, tool_history=history)
    assert [m.tool_call_id for m in messages[-2:]] == ["call", "call2"]
    assert [m.tool_result for m in messages[-2:]] == [
        {"evidence_ref": "e"},
        {"evidence_ref": "e2"},
    ]
