"""Successful schema observations explain incompatible links without granting proof."""

import asyncio
from dataclasses import replace
from copy import deepcopy

import pytest

from general_manager.chat.planned.models import EvidenceRequirement, PlannedTask
from general_manager.chat.planned.scheduler import _executor_messages
from general_manager.chat.providers.base import ToolCallEvent
from tests.unit.test_chat_schema_requirements import A, B, record, requirement
from tests.unit.test_chat_planned_tool_feedback import _runner, _reference


def _inspect(runner, runtime, args):
    return asyncio.run(
        runner.execute_tool(
            runtime, ToolCallEvent("inspection", "get_manager_schema", args)
        )
    )


@pytest.mark.parametrize(
    "req,observed_view,names,expected_scope",
    [
        (
            requirement("overview", []),
            "detail",
            ("Filter",),
            {"manager": "Part", "view": "overview", "types": [], "snapshot": "current"},
        ),
        (
            requirement("detail", ["State"]),
            "detail",
            ("Filter",),
            {
                "manager": "Part",
                "view": "detail",
                "types": ["State"],
                "snapshot": "current",
            },
        ),
        (
            EvidenceRequirement("s", "schema", "Inspect Part", None),
            "overview",
            (),
            {"view": "full"},
        ),
    ],
)
def test_incompatible_named_schema_scope_is_visible_without_linking(
    req,
    observed_view,
    names,
    expected_scope,
):
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    payload = record("observed", observed_view, names).payload()
    args = {"manager": "Part", "requirement_id": "s"}
    if observed_view == "detail":
        args.update(view="detail", types=list(names), snapshot=A)
    runner = _runner(lambda *_: deepcopy(payload), tasks=[task])
    runtime = runner.runtimes["t"]
    result, progress = _inspect(runner, runtime, args)
    assert result == payload and progress
    before = tuple(runner.evidence.records)
    messages = _executor_messages(
        "Inspect Part", task, runner.evidence, tool_history=runtime.tool_history
    )
    context = _reference(messages)
    feedback = context["schema_link_feedback"]
    assert feedback == [
        {
            "requirement_id": "s",
            "evidence_id": "t:schema:1",
            "status": "incompatible_schema_scope",
        }
    ]
    assert (
        context["task"]["requirements"][0].get("schema", {"view": "full"})
        == expected_scope
    )
    assert context["task_evidence"][0]["payload"] == payload
    assert runner.evidence.for_requirement("t", req) == ()
    assert tuple(runner.evidence.records) == before
    assert before[0].payload() == payload
    assert not runner.requirements_satisfied(runtime)


def test_repeated_incompatible_details_report_one_current_observation():
    req = requirement("overview", [])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    payload = record("observed", "detail", ("Filter",)).payload()
    runner = _runner(lambda *_: deepcopy(payload), tasks=[task])
    runtime = runner.runtimes["t"]
    args = {
        "manager": "Part",
        "view": "detail",
        "types": ["Filter"],
        "snapshot": A,
        "requirement_id": "s",
    }
    assert _inspect(runner, runtime, args)[1]
    assert not _inspect(runner, runtime, args)[1]
    messages = _executor_messages(
        "Inspect Part", task, runner.evidence, tool_history=runtime.tool_history
    )
    assert len(_reference(messages)["schema_link_feedback"]) == 1
    assert len(runner.evidence.records) == 1
    assert not runner.requirements_satisfied(runtime)


def test_selected_schema_requirement_survives_backend_argument_isolation():
    req = requirement("overview", [])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    payload = record("observed", "detail", ("Filter",)).payload()
    executed = []

    def execute(_name, args, _scope):
        executed.append(deepcopy(args))
        args["manager"] = "Changed by callback"
        return deepcopy(payload)

    runner = _runner(execute, tasks=[task])
    runtime = runner.runtimes["t"]
    args = {
        "manager": "Part",
        "view": "detail",
        "types": ["Filter"],
        "snapshot": A,
        "requirement_id": "s",
    }
    _inspect(runner, runtime, args)
    assert "requirement_id" not in executed[0]
    assert runtime.tool_history[0].tool_calls[0].args == args
    assert "requirement_id" not in runner.evidence.records[0].call_identity


@pytest.mark.parametrize(
    "damage", ["missing_id", "wrong_id", "stale", "payload", "name"]
)
def test_unmatched_or_stale_history_does_not_claim_schema_scope_feedback(damage):
    req = requirement("overview", [])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    payload = record("observed", "detail", ("Filter",)).payload()
    runner = _runner(lambda *_: deepcopy(payload), tasks=[task])
    runtime = runner.runtimes["t"]
    args = {
        "manager": "Part",
        "view": "detail",
        "types": ["Filter"],
        "snapshot": A,
        "requirement_id": "s",
    }
    _inspect(runner, runtime, args)
    history = deepcopy(runtime.tool_history)
    if damage in {"missing_id", "wrong_id"}:
        call = history[0].tool_calls[0]
        changed = dict(call.args)
        if damage == "missing_id":
            changed.pop("requirement_id", None)
        else:
            changed["requirement_id"] = "foreign"
        history[0] = replace(history[0], tool_calls=(replace(call, args=changed),))
    elif damage == "stale":
        runner.evidence.observe_schema(
            "t", record("changed", "overview", snapshot=B).payload()
        )
    elif damage == "payload":
        history[-1] = replace(history[-1], content="{}", tool_result={})
    else:
        history[-1] = replace(history[-1], tool_name="query")
    messages = _executor_messages(
        "Inspect Part", task, runner.evidence, tool_history=history
    )
    assert "schema_link_feedback" not in _reference(messages)


def test_compatible_current_full_schema_links_without_mismatch_feedback():
    req = requirement("overview", [])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    payload = record("observed", "full", ("Filter",)).payload()
    runner = _runner(lambda *_: deepcopy(payload), tasks=[task])
    runtime = runner.runtimes["t"]
    args = {"manager": "Part", "view": "full", "requirement_id": "s"}
    _inspect(runner, runtime, args)
    messages = _executor_messages(
        "Inspect Part", task, runner.evidence, tool_history=runtime.tool_history
    )
    assert "schema_link_feedback" not in _reference(messages)
    assert runner.requirements_satisfied(runtime)


def test_partial_compatible_detail_fragment_is_pending_coverage_not_scope_mismatch():
    req = requirement("detail", ["Filter", "State"])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    payload = record("observed", "detail", ("Filter",)).payload()
    runner = _runner(lambda *_: deepcopy(payload), tasks=[task])
    runtime = runner.runtimes["t"]
    _inspect(
        runner,
        runtime,
        {
            "manager": "Part",
            "view": "detail",
            "types": ["Filter"],
            "snapshot": A,
            "requirement_id": "s",
        },
    )
    messages = _executor_messages(
        "Inspect Part", task, runner.evidence, tool_history=runtime.tool_history
    )
    assert "schema_link_feedback" not in _reference(messages)
    assert runner.evidence.is_linked_to("t", "s", "t:schema:1")
    assert not runner.requirements_satisfied(runtime)


def test_shared_call_identity_does_not_replace_the_selected_requirement():
    overview = requirement("overview", [])
    details = replace(requirement("detail", ["Filter"]), requirement_id="details")
    task = PlannedTask(
        "t", "Inspect Part", (), (overview, details), ("s", "details"), ()
    )
    payload = record("observed", "detail", ("Filter",)).payload()
    runner = _runner(lambda *_: deepcopy(payload), tasks=[task])
    runtime = runner.runtimes["t"]
    args = {"manager": "Part", "view": "detail", "types": ["Filter"], "snapshot": A}
    _inspect(runner, runtime, {**args, "requirement_id": "s"})
    _inspect(runner, runtime, {**args, "requirement_id": "details"})
    messages = _executor_messages(
        "Inspect Part", task, runner.evidence, tool_history=runtime.tool_history
    )
    feedback = _reference(messages)["schema_link_feedback"]
    assert [item["requirement_id"] for item in feedback] == ["s"]
    assert len(runner.evidence.records) == 1
    assert runner.evidence.for_requirement("t", details)
    assert not runner.evidence.for_requirement("t", overview)


def test_legacy_schema_payload_has_no_invented_observed_scope():
    legacy = EvidenceRequirement("legacy", "schema", "Inspect schema", None)
    details = requirement("detail", ["Filter"])
    task = PlannedTask("t", "Inspect Part", (), (legacy, details), ("legacy", "s"), ())
    payload = {"fields": ["code"]}
    runner = _runner(lambda *_: deepcopy(payload), tasks=[task])
    runtime = runner.runtimes["t"]
    for name in ["legacy", "s"]:
        _inspect(
            runner, runtime, {"manager": "Part", "view": "full", "requirement_id": name}
        )
    messages = _executor_messages(
        "Inspect Part", task, runner.evidence, tool_history=runtime.tool_history
    )
    assert "schema_link_feedback" not in _reference(messages)
    assert runner.evidence.for_requirement("t", legacy)
    assert not runner.evidence.for_requirement("t", details)


def test_feedback_references_existing_complete_scope_without_duplicate_payloads():
    req = requirement("overview", [])
    task = PlannedTask("t", "Inspect Part", (), (req,), ("s",), ())
    payload = record("observed", "detail", ("Filter",)).payload()
    runner = _runner(lambda *_: deepcopy(payload), tasks=[task])
    runtime = runner.runtimes["t"]
    _inspect(
        runner,
        runtime,
        {
            "manager": "Part",
            "view": "detail",
            "types": ["Filter"],
            "snapshot": A,
            "requirement_id": "s",
        },
    )
    context = _reference(
        _executor_messages(
            "Inspect Part", task, runner.evidence, tool_history=runtime.tool_history
        )
    )
    feedback = context["schema_link_feedback"][0]
    assert set(feedback) == {"requirement_id", "evidence_id", "status"}
    assert (
        next(
            item
            for item in context["task"]["requirements"]
            if item["requirement_id"] == feedback["requirement_id"]
        )["schema"]
        == req.schema.as_mapping()
    )
    observed = next(
        item
        for item in context["task_evidence"]
        if item["evidence_id"] == feedback["evidence_id"]
    )
    assert observed["payload"] == payload and observed["requirement_ids"] == []
