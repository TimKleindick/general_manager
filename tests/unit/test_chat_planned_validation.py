"""Contract tests for planned-chat plan and task-graph validation."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from general_manager.chat.planned.models import (
    PlannedTask,
    ValidatedPlan,
)
from general_manager.chat.planned.validation import (
    PlanValidationError,
    validate_dynamic_children,
    validate_plan,
)


CALCULATION_OPERATIONS = (
    "count",
    "sum",
    "average",
    "minimum",
    "maximum",
    "difference",
    "ratio",
    "percentage",
)


def requirement(
    requirement_id: str = "requirement_1",
    *,
    kind: str = "query",
    description: str = "Return matching records.",
    operation: str | None = None,
) -> dict[str, object]:
    return {
        "requirement_id": requirement_id,
        "kind": kind,
        "description": description,
        "operation": operation,
        **(
            {
                "binding": {
                    "source_requirement_ids": [
                        "aggregate"
                        if operation in {"difference", "ratio", "percentage"}
                        else "query_1"
                    ],
                    "value_path": None
                    if operation in {"difference", "ratio", "percentage"}
                    else []
                    if operation == "count"
                    else ["value"],
                    "group_by": [],
                    "unit_path": None,
                }
            }
            if kind == "calculation"
            else {}
        ),
    }


def calculation_requirements(operation: str) -> list[dict[str, object]]:
    sources = [requirement("query_1")]
    if operation in {"difference", "ratio", "percentage"}:
        sources.append(requirement("aggregate", kind="calculation", operation="sum"))
    return [*sources, requirement(kind="calculation", operation=operation)]


def task(
    task_id: str = "task_1",
    *,
    objective: str = "Find the requested records.",
    depends_on: list[str] | None = None,
    requirements: list[dict[str, object]] | None = None,
    completion_criteria: list[str] | None = None,
    routing_features: list[str] | None = None,
) -> dict[str, object]:
    raw_dependencies = [] if depends_on is None else depends_on
    raw_requirements = [requirement()] if requirements is None else requirements
    derived_features: list[str] = []
    if raw_dependencies:
        derived_features.append("has_dependency")
    if any(item.get("kind") == "calculation" for item in raw_requirements):
        derived_features.append("requires_calculation")
    if sum(item.get("kind") == "query" for item in raw_requirements) > 1:
        derived_features.append("multiple_queries")
    return {
        "task_id": task_id,
        "objective": objective,
        "depends_on": raw_dependencies,
        "requirements": raw_requirements,
        "completion_criteria": (
            [item.get("requirement_id", "") for item in raw_requirements]
            if completion_criteria is None
            else completion_criteria
        ),
        "routing_features": (
            derived_features if routing_features is None else routing_features
        ),
    }


def plan(
    tasks: list[dict[str, object]] | None = None,
    *,
    intent: str = "read",
) -> dict[str, object]:
    return {"intent": intent, "tasks": [] if tasks is None else tasks}


def parent_task() -> PlannedTask:
    result = validate_plan(plan([task()]))
    return result.tasks[0]


def child_payload(*children: dict[str, object]) -> dict[str, object]:
    return {"children": list(children)}


def test_read_plan_accepts_six_roots_with_one_edge_dependencies() -> None:
    tasks = [task(f"task_{index}") for index in range(1, 7)]
    tasks[1]["depends_on"] = ["task_1"]
    tasks[1]["routing_features"] = ["has_dependency"]

    validated = validate_plan(plan(tasks))

    assert isinstance(validated, ValidatedPlan)
    assert len(validated.tasks) == 6
    assert validated.tasks[1].depends_on == ("task_1",)


def test_mutation_plan_accepts_zero_tasks() -> None:
    validated = validate_plan(plan(intent="mutation"))

    assert validated.intent == "mutation"
    assert validated.tasks == ()


@pytest.mark.parametrize(
    "tasks",
    [
        [],
        [task(f"task_{index}") for index in range(1, 8)],
    ],
)
def test_read_plan_requires_one_to_six_roots(
    tasks: list[dict[str, object]],
) -> None:
    with pytest.raises(PlanValidationError):
        validate_plan(plan(tasks))


def test_mutation_plan_rejects_tasks() -> None:
    with pytest.raises(PlanValidationError):
        validate_plan(plan([task()], intent="mutation"))


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "read"},
        {"tasks": []},
        {"intent": "read", "tasks": [], "unexpected": True},
        {"intent": "read", "tasks": [task(task_id="task_1", objective="")]},
    ],
)
def test_plan_requires_exact_top_level_keys_and_values(
    payload: object,
) -> None:
    with pytest.raises(PlanValidationError):
        validate_plan(payload)


@pytest.mark.parametrize(
    "missing",
    [
        "task_id",
        "objective",
        "depends_on",
        "requirements",
        "completion_criteria",
        "routing_features",
    ],
)
def test_task_requires_every_structural_field(missing: str) -> None:
    raw_task = task()
    raw_task.pop(missing)

    with pytest.raises(PlanValidationError):
        validate_plan(plan([raw_task]))


def test_task_rejects_unknown_structural_keys() -> None:
    raw_task = task()
    raw_task["provider_role"] = "executor"

    with pytest.raises(PlanValidationError):
        validate_plan(plan([raw_task]))


@pytest.mark.parametrize("task_id", ["", " ", 1, None])
def test_task_id_must_be_non_empty_string(task_id: object) -> None:
    with pytest.raises(PlanValidationError):
        validate_plan(plan([task(task_id=task_id)]))  # type: ignore[arg-type]


def test_task_ids_are_unique() -> None:
    with pytest.raises(PlanValidationError):
        validate_plan(plan([task("task_1"), task("task_1")]))


@pytest.mark.parametrize(
    "dependencies",
    [
        ["task_2"],
        ["task_1", "task_1"],
        ["missing"],
    ],
)
def test_root_dependencies_must_be_unique_and_earlier(
    dependencies: list[str],
) -> None:
    tasks = [task("task_1"), task("task_2", depends_on=dependencies)]

    with pytest.raises(PlanValidationError):
        validate_plan(plan(tasks))


def test_root_dependency_graph_rejects_cycles() -> None:
    tasks = [
        task("task_1", depends_on=["task_2"]),
        task("task_2", depends_on=["task_1"]),
    ]

    with pytest.raises(PlanValidationError):
        validate_plan(plan(tasks))


def test_root_dependency_depth_is_one_edge() -> None:
    tasks = [
        task("task_1"),
        task("task_2", depends_on=["task_1"]),
        task("task_3", depends_on=["task_2"]),
    ]

    with pytest.raises(PlanValidationError):
        validate_plan(plan(tasks))


def test_root_dependency_depth_allows_multiple_roots_on_one_parent() -> None:
    tasks = [
        task("task_1"),
        task("task_2", depends_on=["task_1"]),
        task("task_3", depends_on=["task_1"]),
    ]

    assert len(validate_plan(plan(tasks)).tasks) == 3


def test_completion_criteria_must_list_every_requirement_exactly_once() -> None:
    requirements = [
        requirement("requirement_1"),
        requirement("requirement_2", kind="schema"),
    ]
    valid = validate_plan(
        plan(
            [
                task(
                    requirements=requirements,
                    completion_criteria=["requirement_2", "requirement_1"],
                )
            ]
        )
    )
    assert valid.tasks[0].completion_criteria == (
        "requirement_2",
        "requirement_1",
    )

    for criteria in (
        ["requirement_1"],
        ["requirement_1", "requirement_1"],
        ["requirement_1", "missing"],
    ):
        with pytest.raises(PlanValidationError):
            validate_plan(
                plan([task(requirements=requirements, completion_criteria=criteria)])
            )


def test_routing_features_are_exact_deterministic_structural_facts() -> None:
    requirements = [
        requirement("query_1"),
        requirement("query_2"),
        requirement("calculation", kind="calculation", operation="sum"),
    ]
    payload = task(
        "task_2",
        depends_on=["task_1"],
        requirements=requirements,
        routing_features=[
            "has_dependency",
            "requires_calculation",
            "multiple_queries",
        ],
    )

    validated = validate_plan(plan([task("task_1"), payload]))

    assert validated.tasks[1].routing_features == (
        "has_dependency",
        "requires_calculation",
        "multiple_queries",
    )
    payload["routing_features"] = [
        "multiple_queries",
        "has_dependency",
        "requires_calculation",
    ]
    assert validate_plan(plan([task("task_1"), payload])).tasks[1].routing_features == (
        "multiple_queries",
        "has_dependency",
        "requires_calculation",
    )
    for features in (
        [],
        ["has_dependency"],
        ["requires_calculation", "multiple_queries"],
        ["has_dependency", "requires_calculation", "multiple_queries", "role"],
    ):
        payload["routing_features"] = features
        with pytest.raises(PlanValidationError):
            validate_plan(plan([task("task_1"), payload]))


@pytest.mark.parametrize(
    "missing",
    ["requirement_id", "kind", "description", "operation"],
)
def test_requirement_requires_every_structural_field(missing: str) -> None:
    raw_requirement = requirement()
    raw_requirement.pop(missing)

    with pytest.raises(PlanValidationError):
        validate_plan(plan([task(requirements=[raw_requirement])]))


def test_requirement_rejects_unknown_structural_keys() -> None:
    raw_requirement = requirement()
    raw_requirement["criteria"] = "must be complete"

    with pytest.raises(PlanValidationError):
        validate_plan(plan([task(requirements=[raw_requirement])]))


@pytest.mark.parametrize("kind", ["schema", "path", "query", "calculation"])
def test_supported_requirement_kinds_are_preserved(kind: str) -> None:
    operation = "sum" if kind == "calculation" else None
    validated = validate_plan(
        plan(
            [
                task(
                    requirements=calculation_requirements("sum")
                    if kind == "calculation"
                    else [requirement(kind=kind, operation=operation)]
                )
            ]
        )
    )

    assert validated.tasks[0].requirements[-1].kind == kind


@pytest.mark.parametrize("operation", CALCULATION_OPERATIONS)
def test_only_allow_list_calculation_operations_are_supported(
    operation: str,
) -> None:
    validated = validate_plan(
        plan([task(requirements=calculation_requirements(operation))])
    )

    assert validated.tasks[0].requirements[-1].operation == operation


@pytest.mark.parametrize(
    ("kind", "operation"),
    [
        ("calculation", "median"),
        ("calculation", None),
        ("query", "sum"),
        ("schema", "count"),
        ("path", "ratio"),
    ],
)
def test_requirement_operations_are_strictly_kind_appropriate(
    kind: str,
    operation: str | None,
) -> None:
    with pytest.raises(PlanValidationError):
        validate_plan(
            plan([task(requirements=[requirement(kind=kind, operation=operation)])])
        )


def test_requirement_ids_are_unique_within_a_task() -> None:
    with pytest.raises(PlanValidationError):
        validate_plan(
            plan(
                [
                    task(
                        "task_1",
                        requirements=[requirement("same"), requirement("same")],
                    ),
                ]
            )
        )


def test_plan_types_are_frozen_and_use_tuples() -> None:
    validated = validate_plan(plan([task()]))

    assert isinstance(validated.tasks, tuple)
    assert isinstance(validated.tasks[0].requirements, tuple)
    with pytest.raises(FrozenInstanceError):
        validated.tasks[0].objective = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "payload",
    [
        ("read", []),
        {"intent": "read", "tasks": (task(),)},
        {"intent": "read", "tasks": [task(depends_on=("task_1",))]},
    ],
)
def test_payload_must_be_json_compatible(payload: object) -> None:
    with pytest.raises(PlanValidationError):
        validate_plan(payload)


def test_invalid_plan_error_has_stable_public_semantics() -> None:
    with pytest.raises(PlanValidationError) as caught:
        validate_plan({"intent": "read", "tasks": []})

    assert caught.value.reason == "invalid_plan"
    assert caught.value.code == "invalid_plan"
    assert str(caught.value) == "invalid_plan"
    assert caught.value.detail


@pytest.mark.parametrize(
    ("field", "value", "path", "expected_fragment"),
    [
        ("operation", "query", "$.tasks[0].requirements[0].operation", "null"),
        ("kind", "clarification", "$.tasks[0].requirements[0].kind", "query"),
        ("description", " ", "$.tasks[0].requirements[0].description", "non-empty"),
        (
            "requirement_id",
            None,
            "$.tasks[0].requirements[0].requirement_id",
            "non-empty",
        ),
    ],
)
def test_requirement_rejections_identify_the_exact_field_and_expected_form(
    field: str, value: object, path: str, expected_fragment: str
) -> None:
    raw_requirement = requirement()
    raw_requirement[field] = value
    with pytest.raises(PlanValidationError) as caught:
        validate_plan(plan([task(requirements=[raw_requirement])]))
    assert caught.value.path == path
    assert expected_fragment in caught.value.expected
    assert path in caught.value.detail
    assert str(caught.value) == "invalid_plan"


@pytest.mark.parametrize("operation", [None, "add", "aggregate", 42])
def test_calculation_rejection_lists_allowed_operations(operation: object) -> None:
    raw_requirement = requirement(kind="calculation")
    raw_requirement["operation"] = operation
    with pytest.raises(PlanValidationError) as caught:
        validate_plan(plan([task(requirements=[raw_requirement])]))
    assert caught.value.path == "$.tasks[0].requirements[0].operation"
    for allowed in CALCULATION_OPERATIONS:
        assert allowed in caught.value.expected


@pytest.mark.parametrize(
    ("bad_task", "path", "expected_fragment"),
    [
        (
            task(completion_criteria=["Return matching records."]),
            "$.tasks[0].completion_criteria",
            "requirement_1",
        ),
        (
            task(completion_criteria=["other_task_requirement"]),
            "$.tasks[0].completion_criteria",
            "own",
        ),
        (task(depends_on=["nonexistent"]), "$.tasks[0].depends_on[0]", "earlier root"),
        (
            task(routing_features=["has_dependency"]),
            "$.tasks[0].routing_features",
            "[]",
        ),
    ],
)
def test_reference_and_routing_errors_include_path_and_exact_expected_form(
    bad_task: dict[str, object], path: str, expected_fragment: str
) -> None:
    with pytest.raises(PlanValidationError) as caught:
        validate_plan(plan([bad_task]))
    assert caught.value.path == path
    assert expected_fragment in caught.value.expected


def test_missing_field_and_nested_non_json_values_have_precise_paths() -> None:
    missing = requirement()
    del missing["operation"]
    with pytest.raises(PlanValidationError) as caught:
        validate_plan(plan([task(requirements=[missing])]))
    assert caught.value.path == "$.tasks[0].requirements[0].operation"
    assert "required" in caught.value.expected
    payload = plan([task(objective=float("nan"))])
    with pytest.raises(PlanValidationError) as caught:
        validate_plan(payload)
    assert caught.value.path == "$.tasks[0].objective"
    assert "finite" in caught.value.expected


def test_dynamic_children_are_bounded_owned_and_non_recursive() -> None:
    parent = parent_task()
    children = validate_dynamic_children(
        parent,
        child_payload(task("child_1"), task("child_2")),
        {parent},
    )

    assert all(child.parent_id == parent.task_id for child in children)
    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            children[0],
            child_payload(task("grandchild")),
            {parent, *children},
        )


def test_dynamic_children_require_exact_children_key_and_are_capped_at_two() -> None:
    parent = parent_task()

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(parent, {"tasks": [task("child_1")]}, {parent})
    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_1"), task("child_2"), task("child_3")),
            {parent},
        )


def test_dynamic_children_have_globally_unique_ids() -> None:
    parent = parent_task()

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("task_2")),
            {parent, validate_plan(plan([task("task_2")])).tasks[0]},
        )
    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_1"), task("child_1")),
            {parent},
        )


def test_dynamic_children_reserve_parent_id_without_parent_history() -> None:
    parent = parent_task()

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task(parent.task_id)),
            (),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", ""),
        ("objective", ""),
        ("depends_on", ["task_1"]),
        ("requirements", ("not-a-requirement",)),
        ("completion_criteria", ("missing",)),
        ("routing_features", ("requires_calculation",)),
    ],
)
def test_dynamic_children_validate_existing_task_record_invariants(
    field: str,
    value: object,
) -> None:
    parent = parent_task()
    malformed = replace(parent, **{field: value})

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(parent, child_payload(task("child_1")), [malformed])


def test_dynamic_children_validate_existing_requirement_invariants() -> None:
    parent = parent_task()
    malformed_requirement = replace(parent.requirements[0], description="")
    malformed = replace(
        parent,
        requirements=(malformed_requirement,),
        completion_criteria=(malformed_requirement.requirement_id,),
    )

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(parent, child_payload(task("child_1")), [malformed])


def test_dynamic_children_validate_parent_runtime_invariants() -> None:
    parent = parent_task()
    malformed_parent = replace(parent, parent_id="another_root")

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            malformed_parent,
            child_payload(task("child_1")),
            (),
        )


def test_dynamic_children_cannot_depend_on_another_subtree() -> None:
    parent = parent_task()
    other_root = validate_plan(plan([task("other_root")])).tasks[0]

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_1", depends_on=["other_root"])),
            {parent, other_root},
        )


def test_dynamic_children_may_depend_only_on_their_parent_or_siblings() -> None:
    parent = parent_task()
    children = validate_dynamic_children(
        parent,
        child_payload(task("child_1"), task("child_2", depends_on=[parent.task_id])),
        {parent},
    )

    assert children[1].depends_on == (parent.task_id,)


def test_dynamic_child_cannot_supply_its_own_parent() -> None:
    parent = parent_task()
    raw_child = task("child_1")
    raw_child["parent_id"] = "other_root"

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(parent, child_payload(raw_child), {parent})


def test_dynamic_children_allow_positive_dependency_on_existing_sibling() -> None:
    parent = parent_task()
    first = validate_dynamic_children(parent, child_payload(task("child_1")), {parent})

    second = validate_dynamic_children(
        parent,
        child_payload(task("child_2", depends_on=["child_1"])),
        {parent, *first},
    )

    assert second[0].depends_on == ("child_1",)


def test_existing_sibling_records_cannot_have_cross_root_dependencies() -> None:
    parent = parent_task()
    child = validate_dynamic_children(parent, child_payload(task("child_1")), {parent})[
        0
    ]
    other_root = validate_plan(plan([task("other_root")])).tasks[0]
    invalid_existing_child = replace(
        child,
        depends_on=(other_root.task_id,),
        routing_features=("has_dependency",),
    )

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_2")),
            {parent, invalid_existing_child, other_root},
        )


def test_existing_other_subtree_child_cannot_depend_on_selected_parent() -> None:
    parent = parent_task()
    other_root = validate_plan(plan([task("other_root")])).tasks[0]
    other_child = validate_dynamic_children(
        other_root,
        child_payload(task("other_child")),
        [other_root],
    )[0]
    invalid_other_child = replace(
        other_child,
        depends_on=(parent.task_id,),
        routing_features=("has_dependency",),
    )

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_1")),
            [parent, other_root, invalid_other_child],
        )


def test_existing_orphan_child_is_rejected_even_when_another_root_is_selected() -> None:
    parent = parent_task()
    other_root = validate_plan(plan([task("other_root")])).tasks[0]
    other_child = validate_dynamic_children(
        other_root,
        child_payload(task("other_child")),
        [other_root],
    )[0]
    orphan = replace(other_child, parent_id="missing_root")

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_1")),
            [parent, other_root, orphan],
        )


def test_existing_grandchild_is_rejected_in_an_unselected_subtree() -> None:
    parent = parent_task()
    other_root = validate_plan(plan([task("other_root")])).tasks[0]
    other_child = validate_dynamic_children(
        other_root,
        child_payload(task("other_child")),
        [other_root],
    )[0]
    grandchild = replace(
        other_child,
        task_id="grandchild",
        parent_id=other_child.task_id,
    )

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_1")),
            [parent, other_root, other_child, grandchild],
        )


def test_existing_other_subtree_cannot_exceed_child_limit() -> None:
    parent = parent_task()
    other_root = validate_plan(plan([task("other_root")])).tasks[0]
    other_children = validate_dynamic_children(
        other_root,
        child_payload(task("other_child_1"), task("other_child_2")),
        [other_root],
    )
    overfull_child = replace(other_children[0], task_id="other_child_3")

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_1")),
            [parent, other_root, *other_children, overfull_child],
        )


def test_existing_other_subtree_child_cycle_is_rejected() -> None:
    parent = parent_task()
    other_root = validate_plan(plan([task("other_root")])).tasks[0]
    other_children = validate_dynamic_children(
        other_root,
        child_payload(task("other_child_1"), task("other_child_2")),
        [other_root],
    )
    cyclic_children = (
        replace(
            other_children[0],
            depends_on=(other_children[1].task_id,),
            routing_features=("has_dependency",),
        ),
        replace(
            other_children[1],
            depends_on=(other_children[0].task_id,),
            routing_features=("has_dependency",),
        ),
    )

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_1")),
            [parent, other_root, *cyclic_children],
        )


def test_dynamic_children_limit_is_cumulative_across_repeated_calls() -> None:
    parent = parent_task()
    first = validate_dynamic_children(
        parent,
        child_payload(task("child_1"), task("child_2")),
        {parent},
    )

    with pytest.raises(PlanValidationError):
        validate_dynamic_children(
            parent,
            child_payload(task("child_3"), task("child_4")),
            {parent, *first},
        )


def test_model_calculation_cannot_omit_or_invent_its_binding():
    requirements = calculation_requirements("sum")
    requirements[-1].pop("binding")
    with pytest.raises(PlanValidationError) as error:
        validate_plan(plan([task(requirements=requirements)]))
    assert error.value.path.endswith(".binding")
    requirements = calculation_requirements("sum")
    requirements[-1]["binding"]["source_requirement_ids"] = ["foreign"]
    with pytest.raises(PlanValidationError):
        validate_plan(plan([task(requirements=requirements)]))
