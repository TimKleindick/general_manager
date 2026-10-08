"""Executor contracts and repair guidance preserve strict acceptance and bounds."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from typing import Any, cast

import pytest

from general_manager.chat.planned.contract import PLAN_SCHEMA, TASK_FIELDS
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from general_manager.chat.planned.models import (
    EvidenceRequirement,
    PlannedTask,
    ValidatedPlan,
)
from general_manager.chat.planned.provider_calls import (
    InvalidProviderRoundError,
    ProviderRoundResult,
)
from general_manager.chat.planned.resolver import ManagerResolver
from general_manager.chat.planned.scheduler import (
    PreparedPlannedTurn,
    SchedulerCallbacks,
    _Runner,
    _TaskRuntime,
    _executor_messages,
    _parse_action,
    _parse_action_with_feedback,
)
from general_manager.chat.planned.validation import (
    MAX_CHILDREN_PER_ROOT,
    PlanValidationError,
    validate_dynamic_children,
)
from general_manager.chat.providers.base import Message, TokenUsage, ToolCallEvent
from tests.unit.test_chat_planned_scheduler import _StableExactResolver, _role_settings


def _task(task_id: str = "root") -> PlannedTask:
    return PlannedTask(
        task_id,
        "Read the requested records",
        (),
        (EvidenceRequirement("query", "query", "Read records", None),),
        ("query",),
        (),
    )


def _child(task_id: str = "child", depends_on: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "objective": "Read the requested records",
        "depends_on": list(depends_on),
        "requirements": [
            {
                "requirement_id": "query",
                "kind": "query",
                "description": "Read the requested records",
                "operation": None,
            }
        ],
        "completion_criteria": ["query"],
        "routing_features": ["has_dependency"] if depends_on else [],
    }


def _runner(*tasks: PlannedTask) -> _Runner:
    prepared = PreparedPlannedTurn.for_plan(
        ValidatedPlan("read", tasks or (_task(),)),
        _role_settings(),
        user_text="Read records",
        resolver=cast(ManagerResolver, _StableExactResolver()),
    )
    runner = _Runner(
        prepared,
        {},
        None,
        [],
        SchedulerCallbacks(
            execute_tool=lambda *_: {"data": [{"value": 2}]},
            emit_tool_called=lambda **_: None,
            enforce_rate_limit=None,
        ),
        100.0,
        lambda: 0.0,
    )
    for runtime in runner.runtimes.values():
        runtime.status = "running"
        runtime.role = "executor"
        runtime.candidates = ("PartManager",)
    return runner


def _reference(messages: list[Message]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            next(
                message.content.removeprefix("REFERENCE_DATA=")
                for message in messages
                if message.content.startswith("REFERENCE_DATA=")
            )
        ),
    )


def _rounds(
    monkeypatch: pytest.MonkeyPatch, responses: list[object]
) -> list[tuple[str, list[Message]]]:
    pending = iter(responses)
    calls: list[tuple[str, list[Message]]] = []

    async def complete(
        provider: Any, messages: list[Message], _tools: object, _timeout: float
    ) -> ProviderRoundResult:
        calls.append((provider.role, deepcopy(messages)))
        response = next(pending)
        if isinstance(response, Exception):
            raise response
        return ProviderRoundResult(
            "" if isinstance(response, ToolCallEvent) else json.dumps(response),
            response if isinstance(response, ToolCallEvent) else None,
            TokenUsage(1, 1),
        )

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.complete_provider_round", complete
    )
    return calls


def test_executor_publishes_full_dynamic_child_contract_without_root_only_rules() -> (
    None
):
    original = deepcopy(PLAN_SCHEMA)
    messages = _executor_messages("Read records", _task(), EvidenceStore())
    schema = _reference(messages).get("required_action_schema")
    assert isinstance(schema, dict)
    actions = {item["properties"]["action"]["const"]: item for item in schema["oneOf"]}
    assert set(actions) == {
        "complete",
        "block",
        "spawn_children",
        "calculate",
        "calculate_batch",
        "bind_calculation",
        "clarify_selector",
    }
    selector = actions["clarify_selector"]
    assert selector["required"] == [
        "action",
        "requirement_id",
        "language",
        "selector",
    ]
    assert selector["additionalProperties"] is False
    assert selector["properties"]["selector"]["required"] == ["evidence_id", "field"]
    assert selector["properties"]["selector"]["additionalProperties"] is False
    children = actions["spawn_children"]["properties"]["children"]
    child = children["items"]
    assert children["maxItems"] == MAX_CHILDREN_PER_ROOT
    assert child["required"] == list(TASK_FIELDS)
    assert child["additionalProperties"] is False
    root = cast(dict[str, Any], original)["properties"]["tasks"]["items"]
    for field in ("requirements", "completion_criteria", "routing_features"):
        assert child["properties"][field] == root["properties"][field]
    dependency = child["properties"]["depends_on"]["description"]
    assert "sibling" in dependency and "owning root" in dependency
    assert "earlier root" not in dependency and "depth" not in dependency
    assert "later" in dependency
    assert "cumulative" in children["description"]
    assert "recurs" in children["description"]
    assert "parent_id" in children["description"]
    assert PLAN_SCHEMA == original
    assert "untrusted" in messages[0].content


def test_executor_reference_preserves_current_task_structure_and_child_ownership() -> (
    None
):
    root = _task()
    child = validate_dynamic_children(
        root, {"children": [_child(depends_on=("root",))]}, [root]
    )[0]
    for task in (root, child):
        reference = _reference(
            _executor_messages("Read records", task, EvidenceStore())
        )
        assert reference["task"].get("parent_id", "absent") == task.parent_id
        assert reference["task"].get("depends_on") == list(task.depends_on)
        assert reference["task"].get("completion_criteria") == list(
            task.completion_criteria
        )
        assert reference["task"].get("routing_features") == list(task.routing_features)
    assert child.parent_id == "root"


def _invalid_children(
    case: str,
) -> tuple[PlannedTask, list[PlannedTask], dict[str, Any]]:
    parent = _task()
    existing = [parent]
    children = [_child()]
    child = children[0]
    if case == "historical_four_missing":
        children = [
            {"objective": child["objective"], "requirements": child["requirements"]}
        ]
    elif case == "fresh_five_missing":
        children = [{"objective": child["objective"]}]
    elif case == "routing":
        child["routing_features"] = ["multiple_queries"]
    elif case == "completion":
        child["completion_criteria"] = ["not_this_requirement"]
    elif case == "operation":
        child["requirements"][0]["operation"] = "sum"
    elif case == "duplicate_id":
        child["task_id"] = parent.task_id
    elif case == "duplicate_requirement":
        child["requirements"].append(deepcopy(child["requirements"][0]))
    elif case == "cycle":
        children = [_child("a", ("b",)), _child("b", ("a",))]
    elif case == "self_dependency":
        children = [_child("child", ("child",))]
    elif case == "cross_subtree":
        existing.append(_task("other"))
        children = [_child("child", ("other",))]
    elif case == "recursive":
        parent = validate_dynamic_children(parent, {"children": children}, existing)[0]
        existing.append(parent)
        children = [_child("grandchild")]
    elif case == "cumulative_limit":
        existing.extend(
            validate_dynamic_children(
                parent, {"children": [_child("a"), _child("b")]}, existing
            )
        )
    elif case == "extra_field":
        child["parent_id"] = "root"
    else:
        raise AssertionError(case)
    return parent, existing, {"children": children}


@pytest.mark.parametrize(
    "case",
    [
        "historical_four_missing",
        "fresh_five_missing",
        "routing",
        "completion",
        "operation",
        "duplicate_id",
        "duplicate_requirement",
        "cycle",
        "self_dependency",
        "cross_subtree",
        "recursive",
        "cumulative_limit",
        "extra_field",
    ],
)
def test_child_rejection_reaches_next_request_with_actual_validator_details(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    parent, existing, payload = _invalid_children(case)
    with pytest.raises(PlanValidationError) as raised:
        validate_dynamic_children(parent, payload, existing)
    error = raised.value
    runner = _runner(*(task for task in existing if task.parent_id is None))
    for task in existing:
        if task.parent_id is not None:
            runner.runtimes[task.task_id] = _TaskRuntime(task, task.parent_id)
    runtime = runner.runtimes[parent.task_id]
    runtime.role, runtime.status = "executor", "running"
    before = set(runner.runtimes)
    calls = _rounds(
        monkeypatch,
        [
            {"action": "spawn_children", **payload},
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )

    async def run() -> None:
        assert await runner._execute_one_pass(runtime, ()) == "provider_failed"
        assert set(runner.runtimes) == before
        assert runtime.status == "running" and runtime.child_count == 0
        assert runner.evidence.records == ()
        await runner._execute_one_pass(runtime, ())

    asyncio.run(run())
    assert _reference(calls[1][1]).get("action_validation_error") == {
        "code": error.code,
        "path": error.path,
        "expected": error.expected,
        "detail": error.detail,
    }
    assert runner.prepared.budget.subtree_count(runtime.root_id) == 2
    assert runtime.tool_history == []


@pytest.mark.parametrize(
    ("raw", "code", "path"),
    [
        ("not JSON", "invalid_action_json", "$"),
        ('{"action":"complete","action":"block"}', "invalid_action_json", "$"),
        (
            '{"action":"block","reason":"manager_unresolved"} extra',
            "invalid_action_json",
            "$",
        ),
        ("[]", "invalid_action", "$"),
        ('{"action":"invented"}', "invalid_action", "$.action"),
        ('{"action":"complete"}', "invalid_action_fields", "$.evidence_ids"),
        (
            '{"action":"complete","evidence_ids":["e"],"injected":"PRIVATE"}',
            "invalid_action_fields",
            "$",
        ),
        (
            '{"action":"complete","evidence_ids":[]}',
            "invalid_completion_evidence_ids",
            "$.evidence_ids",
        ),
        (
            '{"action":"complete","evidence_ids":[3]}',
            "invalid_completion_evidence_ids",
            "$.evidence_ids",
        ),
        ('{"action":"spawn_children","children":{}}', "invalid_children", "$.children"),
        (
            '{"action":"calculate","requirement_id":2,"operation":"sum","operands":[]}',
            "invalid_calculation_action",
            "$.requirement_id",
        ),
        (
            '{"action":"calculate","requirement_id":"calc","operation":null,"operands":[]}',
            "invalid_calculation_action",
            "$.operation",
        ),
        (
            '{"action":"calculate","requirement_id":"calc","operation":"sum","operands":{}}',
            "invalid_calculation_action",
            "$.operands",
        ),
    ],
)
def test_invalid_action_syntax_has_fixed_guidance_without_raw_output(
    raw: str, code: str, path: str
) -> None:
    action, feedback = _parse_action_with_feedback(raw)
    assert action is None and _parse_action(raw) is None
    assert feedback is not None
    assert feedback["code"] == code and feedback["path"] == path
    assert feedback["expected"]
    assert "PRIVATE" not in json.dumps(feedback)


def test_valid_later_sibling_dependency_and_action_shapes_remain_accepted() -> None:
    parent = _task()
    payload = {"children": [_child("first", ("later",)), _child("later", ("root",))]}
    children = validate_dynamic_children(parent, payload, [parent])
    assert children[0].depends_on == ("later",)
    assert all(child.parent_id == "root" for child in children)
    actions = [
        {"action": "spawn_children", **payload},
        {"action": "spawn_children", "children": []},
        {"action": "complete", "evidence_ids": ["e", "e"]},
        {
            "action": "calculate",
            "requirement_id": "calc",
            "operation": "sum",
            "operands": [],
        },
        {"action": "block", "reason": "manager_unresolved"},
    ]
    for action in actions:
        assert _parse_action_with_feedback(json.dumps(action)) == (action, None)


def test_completion_rejection_feedback_cannot_supply_missing_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner()
    runtime = runner.runtimes["root"]
    calls = _rounds(
        monkeypatch,
        [{"action": "complete", "evidence_ids": ["invented"]}] * 4,
    )
    asyncio.run(runner.run_task(runtime))
    assert runtime.status == "blocked" and runtime.reason == "provider_failed"
    assert [role for role, _ in calls] == [
        "executor",
        "executor",
    ]
    assert runner.prepared.budget.subtree_count("root") == 2
    feedback = _reference(calls[1][1]).get("action_validation_error")
    assert feedback and feedback["code"] == "invalid_completion_evidence"
    assert feedback["path"] == "$.evidence_ids"
    assert runner.evidence.records == ()
    assert all(_reference(messages)["task_evidence"] == [] for _, messages in calls)


@pytest.mark.parametrize("case", ["cross_task", "unlinked", "unsatisfied"])
def test_completion_still_requires_ownership_links_and_all_declared_evidence(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    task = _task()
    if case == "unsatisfied":
        task = replace(
            task,
            requirements=(
                *task.requirements,
                EvidenceRequirement("schema", "schema", "Inspect fields", None),
            ),
            completion_criteria=("query", "schema"),
        )
    runner = _runner(task)
    runtime = runner.runtimes["root"]
    record = EvidenceRecord.create(
        "q",
        "other" if case == "cross_task" else "root",
        "query",
        "query",
        {"tool": "query"},
        {"data": []},
    )
    runner.evidence.add(
        record, requirement=None if case == "unlinked" else task.requirements[0]
    )
    calls = _rounds(
        monkeypatch,
        [
            {"action": "complete", "evidence_ids": ["q"]},
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )

    async def run() -> None:
        assert await runner._execute_one_pass(runtime, ()) == "provider_failed"
        assert runtime.status == "running"
        await runner._execute_one_pass(runtime, ())

    asyncio.run(run())
    feedback = _reference(calls[1][1])["action_validation_error"]
    assert feedback["code"] == (
        "unsatisfied_requirements"
        if case == "unsatisfied"
        else "invalid_completion_evidence"
    )
    assert runner.evidence.records == (record,)


@pytest.mark.parametrize(
    "case",
    [
        "requirement",
        "operation",
        "operand_shape",
        "operand_path",
        "unknown_evidence",
        "empty_values",
    ],
)
def test_invalid_calculation_guidance_preserves_rejection_and_evidence(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    root = _task()
    task = replace(
        root,
        requirements=(
            *root.requirements,
            EvidenceRequirement("calc", "calculation", "Sum values", "sum"),
        ),
        completion_criteria=("query", "calc"),
        routing_features=("requires_calculation",),
    )
    runner = _runner(task)
    runtime = runner.runtimes["root"]
    source = EvidenceRecord.create(
        "q", "root", "query", "query", {"tool": "query"}, {"values": []}
    )
    runner.evidence.add(source, requirement=task.requirements[0])
    action: dict[str, Any] = {
        "action": "calculate",
        "requirement_id": "calc",
        "operation": "sum",
        "operands": [{"evidence_id": "q", "path": ["values"]}],
    }
    expected_path = "$.operands"
    if case == "requirement":
        action["requirement_id"] = "invented"
        expected_path = "$.requirement_id"
    elif case == "operation":
        action["operation"] = "count"
        expected_path = "$.operation"
    elif case == "operand_shape":
        action["operands"] = [{"evidence_id": "q"}]
        expected_path = "$.operands[0]"
    elif case == "operand_path":
        action["operands"][0]["path"] = [True]
        expected_path = "$.operands[0].path"
    elif case == "unknown_evidence":
        action["operands"][0]["evidence_id"] = "invented"
    calls = _rounds(
        monkeypatch, [action, {"action": "block", "reason": "manager_unresolved"}]
    )

    async def run() -> None:
        assert await runner._execute_one_pass(runtime, ()) == "provider_failed"
        assert runtime.status == "running"
        assert runner.evidence.records == (source,)
        await runner._execute_one_pass(runtime, ())

    asyncio.run(run())
    feedback = _reference(calls[1][1]).get("action_validation_error")
    assert feedback and feedback["path"] == expected_path
    assert feedback["expected"]
    assert runner.evidence.records == (source,)


@pytest.mark.parametrize(
    "detail",
    [
        "a provider round must produce text or tool calls.",
        "a provider round cannot contain text and a tool call.",
        "tool call IDs must be unique within a provider round.",
        "a provider round must finish with one done event.",
    ],
)
def test_invalid_provider_round_preserves_validator_detail_and_counts_usage_once(
    monkeypatch: pytest.MonkeyPatch,
    detail: str,
) -> None:
    runner = _runner()
    runtime = runner.runtimes["root"]
    calls = _rounds(
        monkeypatch,
        [
            InvalidProviderRoundError(detail, usage=TokenUsage(2, 3)),
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )

    async def run() -> None:
        assert await runner._execute_one_pass(runtime, ()) == "provider_failed"
        await runner._execute_one_pass(runtime, ())

    asyncio.run(run())
    feedback = _reference(calls[1][1]).get("action_validation_error")
    assert feedback and feedback["code"] == "invalid_provider_round"
    assert feedback["detail"] == detail
    assert "nonempty text" in feedback["expected"]
    assert runner.usage == TokenUsage(3, 4)
    assert runner.prepared.budget.subtree_count("root") == 2


def test_transport_failure_clears_old_guidance_without_exposing_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner()
    runtime = runner.runtimes["root"]
    calls = _rounds(
        monkeypatch,
        [
            {"action": "block", "reason": "invented"},
            RuntimeError("PRIVATE backend detail"),
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )

    async def run() -> None:
        for _ in range(3):
            await runner._execute_one_pass(runtime, ())

    asyncio.run(run())
    assert (
        _reference(calls[1][1])["action_validation_error"]["code"]
        == "invalid_block_reason"
    )
    assert "action_validation_error" not in _reference(calls[2][1])
    assert "PRIVATE" not in repr(calls)


def test_invalid_child_can_be_corrected_without_leaking_feedback_to_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner()
    runtime = runner.runtimes["root"]
    calls = _rounds(
        monkeypatch,
        [
            {"action": "spawn_children", "children": [{"objective": "Read records"}]},
            {"action": "spawn_children", "children": [_child()]},
            ToolCallEvent("read", "query", {"manager": "PartManager"}),
            {"action": "complete", "evidence_ids": ["child:query:1"]},
            {"action": "complete", "evidence_ids": ["root:child:child:query:1"]},
        ],
    )
    asyncio.run(runner.run_task(runtime))
    assert runtime.status == runner.runtimes["child"].status == "resolved"
    assert runner.prepared.budget.subtree_count("root") == 5
    assert _reference(calls[1][1]).get("action_validation_error")
    assert all(
        "action_validation_error" not in _reference(messages)
        for _, messages in calls[2:]
    )


def test_oversized_child_diagnostics_are_bounded_as_untrusted_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _runner()
    runtime = runner.runtimes["root"]
    rejected = "untrusted identifier " * 5000
    child = _child()
    child[rejected] = "an extra untrusted field"
    calls = _rounds(
        monkeypatch,
        [
            {"action": "spawn_children", "children": [child]},
            {"action": "block", "reason": "manager_unresolved"},
        ],
    )

    async def run() -> None:
        await runner._execute_one_pass(runtime, ())
        await runner._execute_one_pass(runtime, ())

    asyncio.run(run())
    feedback = _reference(calls[1][1]).get("action_validation_error")
    assert feedback and feedback["truncated"] is True
    assert len(json.dumps(feedback)) < 4096
    assert rejected not in json.dumps(feedback)
    assert rejected not in calls[1][1][0].content
    assert runner.evidence.records == () and runtime.child_count == 0
