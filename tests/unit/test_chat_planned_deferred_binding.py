"""A tool-free plan can defer exact field selection without allowing unbound math."""

import asyncio
from dataclasses import replace
from tests.unit.test_chat_planned_calculation_batch import setup
from tests.unit.test_chat_planned_tool_feedback import _record_rounds
from general_manager.chat.planned.validation import validate_plan


def deferred():
    runner, actions = setup()
    runtime = runner.runtimes["task_1"]
    req = runtime.task.requirements[-1]
    binding = req.binding.as_mapping()
    runtime.task = replace(
        runtime.task,
        requirements=(
            *runtime.task.requirements[:-1],
            replace(req, binding=None, binding_required=True),
        ),
    )
    return runner, runtime, actions, binding


def test_explicit_null_binding_is_a_valid_schema_discovery_plan():
    value = {
        "intent": "read",
        "tasks": [
            {
                "task_id": "t",
                "objective": "Read schema then aggregate",
                "depends_on": [],
                "requirements": [
                    {
                        "requirement_id": "q",
                        "kind": "query",
                        "operation": None,
                        "description": "Read complete relevant rows",
                    },
                    {
                        "requirement_id": "s",
                        "kind": "calculation",
                        "operation": "sum",
                        "description": "Aggregate quantity by year",
                        "binding": None,
                    },
                ],
                "completion_criteria": ["q", "s"],
                "routing_features": ["requires_calculation"],
            }
        ],
    }
    plan = validate_plan(value)
    assert plan.tasks[0].requirements[1].binding_required
    assert plan.tasks[0].requirements[1].binding is None


def test_deferred_requirement_rejects_calculation_until_bound(monkeypatch):
    runner, runtime, actions, _ = deferred()
    _record_rounds(monkeypatch, [actions[0]])
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert not any(r.kind == "calculation" for r in runner.evidence.records)


def test_bind_from_observed_query_then_calculate_and_never_rebind(monkeypatch):
    runner, runtime, actions, binding = deferred()
    change = {**binding, "value_path": ["other"]}
    _record_rounds(
        monkeypatch,
        [
            {
                "action": "bind_calculation",
                "requirement_id": "annual",
                "binding": binding,
            },
            {"action": "calculate_batch", "calculations": actions},
            {
                "action": "bind_calculation",
                "requirement_id": "annual",
                "binding": change,
            },
        ],
    )
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert [
        r.payload()["value"] for r in runner.evidence.records if r.kind == "calculation"
    ] == [100, 120, 140]
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runtime.task.requirements[-1].binding.as_mapping() == binding


def test_binding_rejects_foreign_source_requirement(monkeypatch):
    runner, runtime, _, binding = deferred()
    binding["source_requirement_ids"] = ["foreign"]
    _record_rounds(
        monkeypatch,
        [
            {
                "action": "bind_calculation",
                "requirement_id": "annual",
                "binding": binding,
            }
        ],
    )
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runtime.task.requirements[-1].binding is None


def test_unexecutable_binding_is_rejected_before_commit_and_can_be_corrected(
    monkeypatch,
):
    runner, runtime, _, binding = deferred()
    bad = {**binding, "value_path": ["missing", "items", "quantity"]}
    _record_rounds(
        monkeypatch,
        [
            {"action": "bind_calculation", "requirement_id": "annual", "binding": bad},
            {
                "action": "bind_calculation",
                "requirement_id": "annual",
                "binding": binding,
            },
        ],
    )
    assert asyncio.run(runner._execute_one_pass(runtime, ())) == "provider_failed"
    assert runtime.task.requirements[-1].binding is None
    assert runtime.action_validation_error["code"] == "invalid_calculation_binding"
    assert asyncio.run(runner._execute_one_pass(runtime, ())) is None
    assert runtime.task.requirements[-1].binding.as_mapping() == binding
