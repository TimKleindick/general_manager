"""Recorded backend arguments and provider feedback have distinct contracts."""

from copy import deepcopy
import importlib
import importlib.util
import json

import pytest

from experiments.siwc_eval.provider import encode
from general_manager.chat.planned.models import PlannedTask


def api():
    name = "experiments.gm_eval.validation_history"
    assert importlib.util.find_spec(name) is not None, (
        "Runtime-owned history adapter missing"
    )
    return importlib.import_module(name)


def task():
    return PlannedTask(
        task_id="read",
        objective="Read synthetic project data",
        depends_on=(),
        requirements=(),
        completion_criteria=(),
        routing_features=(),
    )


def sources():
    return [
        {
            "index": 1,
            "tool": {
                "id": "schema",
                "name": "get_manager_schema",
                "args": {"manager": "Project", "requirement_id": "project_schema"},
            },
            "args": {"manager": "Project"},
            "result": {
                "manager": "Project",
                "schema_view": "overview",
                "snapshot": "snapshot-A",
            },
        },
        {
            "index": 1,
            "tool": {
                "id": "path",
                "name": "find_path",
                "args": {
                    "from_manager": "Project",
                    "to_manager": "Shipment",
                    "requirement_id": "path_requirement",
                },
            },
            "args": {"from_manager": "Project", "to_manager": "Shipment"},
            "result": {"paths": [["Project", "Shipment"]]},
        },
        {
            "index": 2,
            "tool": {
                "id": "query",
                "name": "query",
                "args": {
                    "manager": "Project",
                    "fields": ["code"],
                    "requirement_id": "query_requirement",
                },
            },
            "args": {"manager": "Project", "fields": ["code"]},
            "result": {"data": [{"code": "P01"}], "has_more": False},
        },
    ]


def test_bound_history_preserves_schema_selection_and_original_provider_groups():
    rows = sources()
    original = deepcopy(rows)
    history = api().history_from_bound_sources(task(), rows)
    wire = encode(history)
    assert rows == original
    assert [m.role for m in history] == [
        "assistant",
        "tool",
        "tool",
        "assistant",
        "tool",
    ]
    assert [t.id for t in history[0].tool_calls] == ["schema", "path"]
    assert [t.id for t in history[3].tool_calls] == ["query"]
    calls = [m for m in wire if m.get("type") == "function_call"]
    assert [c["call_id"] for c in calls] == ["schema", "path", "query"]
    assert json.loads(calls[0]["arguments"]) == {
        "manager": "Project",
        "requirement_id": "project_schema",
    }
    assert "requirement_id" not in json.loads(calls[1]["arguments"])
    assert "requirement_id" not in json.loads(calls[2]["arguments"])
    outputs = [m for m in wire if m.get("type") == "function_call_output"]
    assert [json.loads(m["output"]) for m in outputs] == [row["result"] for row in rows]


@pytest.mark.parametrize(
    "damage",
    ["reordered", "non_integer", "missing_args", "changed_backend", "duplicate_tool"],
)
def test_history_rejects_unbound_source_order_or_argument_claims(damage):
    rows = sources()
    if damage == "reordered":
        rows.reverse()
    elif damage == "non_integer":
        rows[0]["index"] = True
    elif damage == "missing_args":
        del rows[0]["args"]
    elif damage == "changed_backend":
        rows[0]["args"]["manager"] = "Other"
    elif damage == "duplicate_tool":
        rows[1]["tool"]["id"] = rows[0]["tool"]["id"]
    with pytest.raises(ValueError):
        api().history_from_bound_sources(task(), rows)
