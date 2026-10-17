"""Rejected bindings expose actual same-task sources without selecting a repair."""

import asyncio
from copy import deepcopy
import json

import pytest

from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from tests.unit.test_chat_planned_deferred_binding import deferred
from tests.unit.test_chat_planned_tool_feedback import _record_rounds


def prepared(populations, binding, *, extra=None):
    runner, runtime, _, _ = deferred()
    runner.evidence = EvidenceStore()
    source = runtime.task.requirements[0]
    for index, rows in enumerate(populations):
        record = EvidenceRecord.create(
            f"q{index}",
            runtime.task.task_id,
            "query",
            f"query-{index}",
            {},
            {
                "data": rows,
                "complete": True,
                "has_more": False,
                "total_count": len(rows),
            },
        )
        runner.evidence.add(record, requirement=source)
    if extra is not None:
        runner.evidence.add(extra)
    raw = {
        "source_requirement_ids": [source.requirement_id],
        "value_path": binding[0],
        "group_by": binding[1],
        "unit_path": binding[2],
    }
    action = {"action": "bind_calculation", "requirement_id": "annual", "binding": raw}
    return runner, runtime, action


def rejected(monkeypatch, populations, binding, *, extra=None):
    runner, runtime, action = prepared(populations, binding, extra=extra)
    before_task = deepcopy(runtime.task)
    before_evidence = runner.evidence.records
    _record_rounds(monkeypatch, [action])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runtime.task == before_task
    assert runner.evidence.records == before_evidence
    assert runtime.action_validation_error["code"] == "invalid_calculation_binding"
    return runtime.action_validation_error


def detail(feedback):
    assert feedback["detail"].startswith("{"), (
        "binding feedback must retain source observations"
    )
    assert feedback.get("truncated") is not True
    return json.loads(feedback["detail"])


def test_nested_then_flat_sources_keep_both_rejections_and_observed_fields(monkeypatch):
    nested = [
        {"id": 1, "shipmentsList": {"items": [{"quantity": 7, "unit": "pieces"}]}}
    ]
    flat = [{"projectId": 1, "quantity": 7, "unit": "pieces"}]
    binding = (
        ["shipmentsList", "items", "quantity"],
        [["id"]],
        ["shipmentsList", "items", "unit"],
    )
    data = detail(rejected(monkeypatch, [nested, flat], binding))
    assert data["binding_paths"]["value_path"] == binding[0]
    errors = {item["evidence_id"]: item["error"] for item in data["source_errors"]}
    assert "Collection traversal" in errors["q0"]
    assert "bound field is absent" in errors["q1"]
    observed = data["observed_scalar_paths"]
    assert observed["q0"]["paths"] == [["id"]]
    assert observed["q1"]["paths"] == [["projectId"], ["quantity"], ["unit"]]
    assert "repair" not in data and "selected_source" not in data


@pytest.mark.parametrize(
    "role,path",
    [
        ("value_path", ["absent"]),
        ("group_by", ["unknown_group"]),
        ("unit_path", ["missing_unit"]),
    ],
)
def test_missing_value_group_or_unit_paths_are_exposed_without_invented_fields(
    monkeypatch, role, path
):
    rows = [{"quantity": 7, "projectId": 1, "unit": "pieces"}]
    values = {
        "value_path": ["quantity"],
        "group_by": [["projectId"]],
        "unit_path": ["unit"],
    }
    values[role] = [path] if role == "group_by" else path
    data = detail(
        rejected(
            monkeypatch,
            [rows],
            (values["value_path"], values["group_by"], values["unit_path"]),
        )
    )
    assert data["binding_paths"][role] == values[role]
    assert data["observed_scalar_paths"]["q0"]["paths"] == [
        ["projectId"],
        ["quantity"],
        ["unit"],
    ]
    assert path not in data["observed_scalar_paths"]["q0"]["paths"]


@pytest.mark.parametrize(
    "second", [{"id": 2}, {"id": 2, "quantity": None}, {"id": 2, "quantity": [8]}]
)
def test_heterogeneous_paths_are_not_advertised_as_uniform_scalars(monkeypatch, second):
    data = detail(
        rejected(
            monkeypatch,
            [[{"id": 1, "quantity": 7}, second]],
            (["quantity"], [["id"]], None),
        )
    )
    observed = data["observed_scalar_paths"]["q0"]
    assert observed["paths"] == [["id"]]
    assert observed["non_uniform_path_count"] == 1


def test_literal_dotted_keys_and_nested_object_paths_remain_distinct(monkeypatch):
    rows = [{"id": 1, "metric.value": 7, "metric": {"value": 8}}]
    data = detail(
        rejected(monkeypatch, [rows], (["metric", "missing"], [["id"]], None))
    )
    assert data["observed_scalar_paths"]["q0"]["paths"] == [
        ["id"],
        ["metric", "value"],
        ["metric.value"],
    ]


@pytest.mark.parametrize("owner", ["other-task", "task_1"])
def test_foreign_and_unlinked_sources_never_supply_diagnostic_paths(monkeypatch, owner):
    extra = EvidenceRecord.create(
        "not-linked",
        owner,
        "query",
        "query-extra",
        {},
        {
            "data": [{"foreign_only": 7}],
            "complete": True,
            "has_more": False,
            "total_count": 1,
        },
    )
    data = detail(
        rejected(
            monkeypatch, [[{"id": 1}]], (["quantity"], [["id"]], None), extra=extra
        )
    )
    assert [item["evidence_id"] for item in data["source_errors"]] == ["q0"]
    assert "not-linked" not in json.dumps(data) and "foreign_only" not in json.dumps(
        data
    )


def test_corrected_flat_binding_is_accepted_without_choosing_or_mutating_sources(
    monkeypatch,
):
    nested = [
        {"id": 1, "shipmentsList": {"items": [{"quantity": 7, "unit": "pieces"}]}}
    ]
    flat = [{"projectId": 1, "quantity": 7, "unit": "pieces"}]
    runner, runtime, bad = prepared(
        [nested, flat], (["shipmentsList", "items", "quantity"], [["id"]], None)
    )
    good = deepcopy(bad)
    good["binding"].update(
        value_path=["quantity"], group_by=[["projectId"]], unit_path=["unit"]
    )
    before = runner.evidence.records
    _record_rounds(monkeypatch, [bad, good])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runtime.task.requirements[-1].binding is None
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.task.requirements[-1].binding.as_mapping() == good["binding"]
    assert runner.evidence.records == before


def test_wide_observations_use_existing_bounded_feedback_and_explicit_truncation(
    monkeypatch,
):
    rows = [{f"field_{i:03}": i for i in range(120)}]
    feedback = rejected(monkeypatch, [rows], (["absent"], [], None))
    assert feedback["detail"].startswith("{")
    assert len(feedback["detail"]) <= 1024
    assert feedback.get("truncated") is True
    assert '"evidence_id":"q0"' in feedback["detail"]


def test_empty_query_has_no_invented_observed_fields(monkeypatch):
    data = detail(rejected(monkeypatch, [[]], (["quantity"], [], None)))
    assert data["observed_scalar_paths"]["q0"]["paths"] == []
    assert data["observed_scalar_paths"]["q0"]["non_uniform_path_count"] == 0


def test_deep_observations_stop_at_explicit_depth_without_inventing_leaf_paths(
    monkeypatch,
):
    value = {"amount": 7}
    for _ in range(18):
        value = {"nested": value}
    data = detail(
        rejected(monkeypatch, [[{"id": 1, "deep": value}]], (["absent"], [], None))
    )
    assert data["observed_scalar_paths"]["q0"]["paths"] == [["id"]]
    assert data["observed_scalar_paths"]["q0"]["paths_truncated"] is True
