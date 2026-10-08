"""The planner's published contract must describe the strict accepted plans."""

from copy import deepcopy

import pytest

from general_manager.chat.planned import models
from general_manager.chat.planned.validation import PlanValidationError, validate_plan


def test_contract_preserves_model_exports_and_publishes_enforced_vocabulary() -> None:
    from general_manager.chat.planned import contract

    assert models.RequirementKind is contract.RequirementKind
    assert models.RoutingFeature is contract.RoutingFeature
    assert models.PlanIntent is contract.PlanIntent
    assert models.CALCULATION_OPERATIONS is contract.CALCULATION_OPERATIONS
    assert models.REQUIREMENT_KINDS is contract.REQUIREMENT_KINDS
    assert models.ROUTING_FEATURE_VALUES is contract.ROUTING_FEATURE_VALUES
    schema = contract.PLAN_SCHEMA
    assert schema["required"] == list(contract.PLAN_FIELDS)
    task_schema = schema["properties"]["tasks"]["items"]
    assert task_schema["required"] == list(contract.TASK_FIELDS)
    requirement_schema = task_schema["properties"]["requirements"]["items"]
    assert requirement_schema["required"] == list(contract.REQUIREMENT_FIELDS)
    assert set(requirement_schema["properties"]["kind"]["enum"]) == set(
        models.REQUIREMENT_KINDS
    )
    operation_rule = requirement_schema["allOf"][0]
    assert operation_rule["if"]["properties"]["kind"] == {"const": "calculation"}
    assert operation_rule["then"]["properties"]["operation"]["enum"] == list(
        models.CALCULATION_OPERATIONS
    )
    assert operation_rule["else"]["properties"]["operation"] == {"type": "null"}
    assert task_schema["properties"]["completion_criteria"]["description"] == (
        contract.COMPLETION_CRITERIA_RULE
    )
    assert task_schema["properties"]["routing_features"]["description"] == (
        contract.ROUTING_FEATURES_RULE
    )
    assert contract.COMPLETION_CRITERIA_RULE in contract.PLAN_INSTRUCTION
    assert contract.ROUTING_FEATURES_RULE in contract.PLAN_INSTRUCTION
    for operation in models.CALCULATION_OPERATIONS:
        assert operation in contract.PLAN_INSTRUCTION


def test_contract_examples_validate_without_normalization_and_have_real_evidence() -> (
    None
):
    from general_manager.chat.planned import contract

    assert set(contract.PLAN_EXAMPLES) == {"query", "calculation", "clarification"}
    before = deepcopy(contract.PLAN_EXAMPLES)
    for example in contract.PLAN_EXAMPLES.values():
        plan = validate_plan(example)
        assert plan.intent == "read"
        assert all(task.requirements for task in plan.tasks)
        for task in plan.tasks:
            assert set(task.completion_criteria) == {
                requirement.requirement_id for requirement in task.requirements
            }
    assert before == contract.PLAN_EXAMPLES
    clarification = validate_plan(contract.PLAN_EXAMPLES["clarification"])
    assert clarification.tasks[0].requirements[0].kind == "schema"
    assert "evidence" in contract.PLAN_INSTRUCTION
    assert "clarification" in contract.PLAN_INSTRUCTION


@pytest.mark.parametrize("kind", ["schema", "path", "query", "calculation"])
@pytest.mark.parametrize("operation", [None, *models.CALCULATION_OPERATIONS, "add"])
def test_every_published_kind_operation_pair_matches_strict_validation(
    kind: str, operation: str | None
) -> None:
    from general_manager.chat.planned import contract

    payload = deepcopy(contract.PLAN_EXAMPLES["query"])
    task = payload["tasks"][0]
    task["requirements"][0].update(kind=kind, operation=operation)
    task["routing_features"] = ["requires_calculation"] if kind == "calculation" else []
    valid = (
        operation in models.CALCULATION_OPERATIONS
        if kind == "calculation"
        else operation is None
    )
    if valid and kind == "calculation":
        # This test checks the kind/operation vocabulary independently of source graphs.
        from general_manager.chat.planned.validation import _validate_requirement_record

        _validate_requirement_record(
            models.EvidenceRequirement("r", kind, "operation vocabulary", operation),
            set(),
        )
    elif valid:
        validate_plan(payload)
    else:
        with pytest.raises(PlanValidationError):
            validate_plan(payload)


def test_named_selection_targets_need_independent_query_evidence_even_when_empty():
    from general_manager.chat.planned import contract
    from general_manager.chat.planned.synthesis import _SYNTHESIS_INSTRUCTION

    assert "Resolve each named selection target" in contract.PLAN_INSTRUCTION
    assert "even when the requested result is empty" in contract.PLAN_INSTRUCTION
    assert (
        "An explanatory entity cannot replace a requested target"
        in _SYNTHESIS_INSTRUCTION
    )
    assert "actual narrowing" in _SYNTHESIS_INSTRUCTION
