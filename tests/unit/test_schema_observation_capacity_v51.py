"""Operational schema references never link or complete requirements."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    EvidenceStore,
    canonical_call_identity,
)
from general_manager.chat.planned.models import (
    EvidenceRequirement,
    PlannedTask,
    SchemaBinding,
)
from general_manager.chat.planned.scheduler import (
    _executor_messages,
    _project_tool_history,
)
from general_manager.chat.planned.schema_projection import (
    compact_messages,
    logical_messages,
)
from general_manager.chat.providers.base import Message, ToolCallEvent


def fixture():
    req = EvidenceRequirement(
        "r",
        "schema",
        "inspect",
        None,
        schema=SchemaBinding("Part", "overview", (), "current"),
    )
    task = PlannedTask("t", "inspect", (), (req,), ("r",), ())
    payload = {
        "manager": "Part",
        "schema_view": "overview",
        "snapshot": "a" * 64,
        "contract_version": 2,
        "inspection_version": 1,
        "schema_complete": False,
        "fields": [{"description": "untrusted observation " * 100}],
    }
    args = {"manager": "Part"}
    provenance = {
        "tool": "get_manager_schema",
        "manager": "Part",
        "schema_view": "overview",
        "snapshot": "a" * 64,
    }
    record = EvidenceRecord.create(
        "e",
        "t",
        "schema",
        canonical_call_identity("get_manager_schema", args),
        provenance,
        payload,
    )
    store = EvidenceStore()
    store.add(record)
    store.observe_schema("t", payload)
    history = [
        Message(
            "assistant",
            "",
            tool_calls=(ToolCallEvent("call", "get_manager_schema", args),),
        ),
        Message(
            "tool",
            json.dumps(payload),
            tool_call_id="call",
            tool_name="get_manager_schema",
            tool_result=deepcopy(payload),
        ),
    ]
    return task, store, history


def test_unlinked_current_observation_has_separate_bound_reference_and_raw_audit():
    task, store, history = fixture()
    before = deepcopy(history)
    messages = _executor_messages("inspect", task, store, tool_history=history)
    stub = json.loads(messages[-1].content)
    assert set(stub) == {"schema_observation_ref"}, "unlinked duplicate is still raw"
    assert stub["schema_observation_ref"]["requirement_ids"] == []
    assert stub["schema_observation_ref"]["call_id"] == "call"
    assert messages[-1].logical_content == history[-1].content
    from experiments.siwc_eval.provider import encode

    assert len(encode(messages)[-1]["output"]) < len(history[-1].content)
    restored = logical_messages(compact_messages(messages))
    assert restored[-1] == history[-1]
    assert restored[-2].tool_calls == history[0].tool_calls
    assert restored[-2].content == history[0].content
    assert store.for_requirement("t", task.requirements[0]) == ()
    assert history[-1] == before[-1]
    assert history[0].tool_calls == before[0].tool_calls
    assert history[0].content == before[0].content
    ref = json.loads(messages[1].content.split("=", 1)[1])
    assert ref["task_evidence"][0]["requirement_ids"] == []


@pytest.mark.parametrize(
    "change",
    [
        "stale",
        "other_task",
        "args",
        "name",
        "id",
        "duplicate_call",
        "payload",
        "error",
        "legacy",
    ],
)
def test_unmatched_stale_and_legacy_observations_keep_the_complete_result(change):
    task, store, history = fixture()
    if change == "stale":
        store.invalidate_schema("t", "Part")
    elif change == "other_task":
        task = replace(task, task_id="other")
    elif change == "args":
        history[0] = replace(
            history[0],
            tool_calls=(replace(history[0].tool_calls[0], args={"manager": "Other"}),),
        )
    elif change == "name":
        history[-1] = replace(history[-1], tool_name="query")
    elif change == "id":
        history[-1] = replace(history[-1], tool_call_id="other")
    elif change == "duplicate_call":
        history[0] = replace(history[0], tool_calls=history[0].tool_calls * 2)
    elif change in {"payload", "error"}:
        history[-1] = replace(
            history[-1], content='{"status":"error"}', tool_result={"status": "error"}
        )
    else:
        record = store.get("e")
        store = EvidenceStore()
        payload = {"fields": ["legacy"]}
        store.add(
            EvidenceRecord.create("e", "t", "schema", record.call_identity, {}, payload)
        )
        history[-1] = replace(
            history[-1], content=json.dumps(payload), tool_result=payload
        )
    assert _project_tool_history(task, store, history)[-1] == history[-1]


@pytest.mark.parametrize(
    "change",
    [
        "content",
        "receipt",
        "call_id",
        "name",
        "source",
        "missing_reference",
        "foreign_task",
        "link",
        "snapshot",
    ],
)
def test_operational_reference_tampering_and_missing_source_are_rejected(change):
    task, store, history = fixture()
    messages = _executor_messages("inspect", task, store, tool_history=history)
    assert messages[-1].logical_content is not None, "observation projection missing"
    if change == "content":
        messages[-1] = replace(messages[-1], content="{}")
    elif change == "receipt":
        messages[-1] = replace(messages[-1], projection_receipt={})
    elif change == "call_id":
        messages[-1] = replace(messages[-1], tool_call_id="other")
    elif change == "name":
        messages[-1] = replace(messages[-1], tool_name="query")
    elif change == "source":
        messages[-1] = replace(
            messages[-1],
            schema_observation=replace(
                messages[-1].schema_observation, payload_json="{}"
            ),
        )
    elif change == "missing_reference":
        del messages[1]
    else:
        ref = json.loads(messages[1].content.split("=", 1)[1])
        if change == "foreign_task":
            ref["task"]["task_id"] = "other"
        elif change == "link":
            ref["task_evidence"][0]["requirement_ids"] = ["r"]
        else:
            ref["task_evidence"][0]["payload"]["snapshot"] = "b" * 64
        from general_manager.chat.planned.schema_projection import reference_message

        messages[1] = reference_message(
            ref, messages[1].schema_slots, reference_scope="executor"
        )
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        logical_messages(messages)


@pytest.mark.parametrize("field", ["logical_content", "tool_result_json"])
def test_equal_python_scalars_cannot_replace_exact_observation_data(field):
    task, store, history = fixture()
    messages = _executor_messages("inspect", task, store, tool_history=history)
    message = messages[-1]
    receipt = deepcopy(message.projection_receipt)
    if field == "logical_content":
        changed = json.loads(message.logical_content)
        changed["schema_complete"] = 0
        text = json.dumps(changed)
        receipt["logical_content_sha256"] = sha256(text.encode()).hexdigest()
        messages[-1] = replace(
            message, logical_content=text, projection_receipt=receipt
        )
    else:
        changed = json.loads(message.schema_observation.tool_result_json)
        changed["schema_complete"] = 0
        text = json.dumps(changed)
        receipt["logical_tool_result_sha256"] = sha256(text.encode()).hexdigest()
        messages[-1] = replace(
            message,
            schema_observation=replace(
                message.schema_observation, tool_result_json=text
            ),
            projection_receipt=receipt,
        )
    with pytest.raises(ValueError, match="invalid_schema_projection"):
        logical_messages(messages)
