"""Strict validation for planned-chat plans and bounded task children."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from math import isfinite
from typing import NoReturn, cast

from general_manager.chat.planned.models import (
    CALCULATION_OPERATIONS,
    EvidenceRequirement,
    PlanIntent,
    PlannedTask,
    RequirementKind,
    RoutingFeature,
    ValidatedPlan,
)


MAX_ROOT_TASKS = 6
MAX_CHILDREN_PER_ROOT = 2
MAX_ROOT_DEPENDENCY_DEPTH = 1

_PLAN_KEYS = frozenset(("intent", "tasks"))
_TASK_KEYS = frozenset(
    (
        "task_id",
        "objective",
        "depends_on",
        "requirements",
        "completion_criteria",
        "routing_features",
    )
)
_REQUIREMENT_KEYS = frozenset(("requirement_id", "kind", "description", "operation"))
_CHILDREN_KEYS = frozenset(("children",))
_INTENTS = frozenset(("read", "mutation"))
_REQUIREMENT_KINDS = frozenset(("schema", "path", "query", "calculation"))
_ROUTING_FEATURES: tuple[RoutingFeature, ...] = (
    "has_dependency",
    "requires_calculation",
    "multiple_queries",
)


class PlanValidationError(ValueError):
    """Private validation detail with a stable public ``invalid_plan`` reason."""

    reason = "invalid_plan"
    code = "invalid_plan"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(self.reason)


def _invalid(detail: str) -> NoReturn:
    raise PlanValidationError(detail)


def _ensure_json_compatible(value: object, active: set[int] | None = None) -> None:
    """Reject Python values that cannot be represented by strict JSON."""
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            _invalid("payload contains a non-finite number.")
        return
    if active is None:
        active = set()
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            _invalid("payload contains a cyclic object.")
        active.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    _invalid("payload object keys must be strings.")
                _ensure_json_compatible(item, active)
        finally:
            active.remove(identity)
        return
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            _invalid("payload contains a cyclic object.")
        active.add(identity)
        try:
            for item in value:
                _ensure_json_compatible(item, active)
        finally:
            active.remove(identity)
        return
    _invalid("payload must contain only JSON-compatible values.")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _invalid(f"{label} must be an object.")
    return cast(Mapping[str, object], value)


def _exact_keys(
    value: Mapping[str, object], expected: frozenset[str], label: str
) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        detail = f"{label} has invalid fields"
        if missing:
            detail += f"; missing {', '.join(missing)}"
        if unknown:
            detail += f"; unknown {', '.join(unknown)}"
        _invalid(f"{detail}.")


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{label} must be a non-empty string.")
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        _invalid(f"{label} must be an array.")
    result: list[str] = []
    for item in value:
        result.append(_required_text(item, label))
    if len(result) != len(set(result)):
        _invalid(f"{label} must not contain duplicates.")
    return tuple(result)


def _parse_requirement(
    value: object,
    seen_requirement_ids: set[str],
) -> EvidenceRequirement:
    mapping = _mapping(value, "requirement")
    _exact_keys(mapping, _REQUIREMENT_KEYS, "requirement")
    requirement_id = _required_text(mapping["requirement_id"], "requirement_id")
    if requirement_id in seen_requirement_ids:
        _invalid(f"duplicate requirement_id {requirement_id!r}.")
    seen_requirement_ids.add(requirement_id)

    kind = mapping["kind"]
    if not isinstance(kind, str) or kind not in _REQUIREMENT_KINDS:
        _invalid("requirement kind is not supported.")
    description = _required_text(mapping["description"], "description")
    operation = mapping["operation"]
    if kind == "calculation":
        if operation not in CALCULATION_OPERATIONS:
            _invalid("calculation operation is not supported.")
    elif operation is not None:
        _invalid("only calculation requirements may define operation.")
    if operation is not None and not isinstance(operation, str):
        _invalid("requirement operation must be a string or null.")
    return EvidenceRequirement(
        requirement_id=requirement_id,
        kind=cast(RequirementKind, kind),
        description=description,
        operation=operation,
    )


def _parse_task(
    value: object,
    *,
    parent_id: str | None,
    seen_requirement_ids: set[str],
) -> PlannedTask:
    mapping = _mapping(value, "task")
    _exact_keys(mapping, _TASK_KEYS, "task")
    task_id = _required_text(mapping["task_id"], "task_id")
    objective = _required_text(mapping["objective"], "objective")
    depends_on = _string_list(mapping["depends_on"], "depends_on")
    raw_requirements = mapping["requirements"]
    if not isinstance(raw_requirements, list):
        _invalid("requirements must be an array.")
    requirements = tuple(
        _parse_requirement(item, seen_requirement_ids) for item in raw_requirements
    )
    completion_criteria = _string_list(
        mapping["completion_criteria"], "completion_criteria"
    )
    requirement_ids = {requirement.requirement_id for requirement in requirements}
    if (
        len(completion_criteria) != len(requirements)
        or set(completion_criteria) != requirement_ids
    ):
        _invalid("completion_criteria must list every requirement exactly once.")

    routing_features = _string_list(mapping["routing_features"], "routing_features")
    if any(feature not in _ROUTING_FEATURES for feature in routing_features):
        _invalid("routing_features contains an unsupported feature.")
    expected_features: list[RoutingFeature] = []
    if depends_on:
        expected_features.append("has_dependency")
    if any(requirement.kind == "calculation" for requirement in requirements):
        expected_features.append("requires_calculation")
    if sum(requirement.kind == "query" for requirement in requirements) > 1:
        expected_features.append("multiple_queries")
    if tuple(routing_features) != tuple(expected_features):
        _invalid("routing_features must match task structure.")
    return PlannedTask(
        task_id=task_id,
        objective=objective,
        depends_on=depends_on,
        requirements=requirements,
        completion_criteria=completion_criteria,
        routing_features=tuple(
            cast(RoutingFeature, feature) for feature in routing_features
        ),
        parent_id=parent_id,
    )


def _validate_root_graph(tasks: tuple[PlannedTask, ...]) -> None:
    task_ids = {task.task_id for task in tasks}
    for index, task in enumerate(tasks):
        if task.task_id in task.depends_on:
            _invalid(f"task {task.task_id!r} cannot depend on itself.")
        for dependency in task.depends_on:
            if dependency not in task_ids:
                _invalid(f"task {task.task_id!r} has an unknown dependency.")
            if dependency not in {candidate.task_id for candidate in tasks[:index]}:
                _invalid(f"task {task.task_id!r} must depend on an earlier root.")

    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(task_id: str) -> int:
        if task_id in visiting:
            _invalid("root task dependencies must be acyclic.")
        if task_id in depths:
            return depths[task_id]
        visiting.add(task_id)
        task = next(task for task in tasks if task.task_id == task_id)
        task_depth = 0
        if task.depends_on:
            task_depth = 1 + max(depth(dependency) for dependency in task.depends_on)
        visiting.remove(task_id)
        depths[task_id] = task_depth
        return task_depth

    for task in tasks:
        if depth(task.task_id) > MAX_ROOT_DEPENDENCY_DEPTH:
            _invalid("root dependency depth exceeds one edge.")


def validate_plan(payload: object) -> ValidatedPlan:
    """Validate one complete JSON plan before any application data access."""
    _ensure_json_compatible(payload)
    mapping = _mapping(payload, "plan")
    _exact_keys(mapping, _PLAN_KEYS, "plan")
    intent = mapping["intent"]
    if not isinstance(intent, str) or intent not in _INTENTS:
        _invalid("plan intent must be read or mutation.")
    raw_tasks = mapping["tasks"]
    if not isinstance(raw_tasks, list):
        _invalid("tasks must be an array.")
    if intent == "read" and not 1 <= len(raw_tasks) <= MAX_ROOT_TASKS:
        _invalid("read plans must contain one to six root tasks.")
    if intent == "mutation" and raw_tasks:
        _invalid("mutation plans must contain zero tasks.")

    seen_task_ids: set[str] = set()
    tasks: list[PlannedTask] = []
    for raw_task in raw_tasks:
        parsed = _parse_task(
            raw_task,
            parent_id=None,
            seen_requirement_ids=set(),
        )
        if parsed.task_id in seen_task_ids:
            _invalid(f"duplicate task_id {parsed.task_id!r}.")
        seen_task_ids.add(parsed.task_id)
        tasks.append(parsed)
    validated_tasks = tuple(tasks)
    _validate_root_graph(validated_tasks)
    return ValidatedPlan(intent=cast(PlanIntent, intent), tasks=validated_tasks)


def _validate_child_graph(
    children: tuple[PlannedTask, ...],
    parent_id: str,
    existing_sibling_ids: Collection[str] = (),
) -> None:
    child_ids = {child.task_id for child in children}
    existing_ids = set(existing_sibling_ids)
    allowed_dependencies = child_ids | existing_ids | {parent_id}
    for child in children:
        if child.task_id in child.depends_on:
            _invalid(f"child {child.task_id!r} cannot depend on itself.")
        if any(
            dependency not in allowed_dependencies for dependency in child.depends_on
        ):
            _invalid("dynamic children cannot depend on another subtree.")

    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(task_id: str) -> int:
        if task_id == parent_id or task_id in existing_ids:
            return 0
        if task_id in visiting:
            _invalid("dynamic child dependencies must be acyclic.")
        if task_id in depths:
            return depths[task_id]
        visiting.add(task_id)
        child = next(child for child in children if child.task_id == task_id)
        child_depth = 0
        if child.depends_on:
            child_depth = 1 + max(depth(dependency) for dependency in child.depends_on)
        visiting.remove(task_id)
        depths[task_id] = child_depth
        return child_depth

    for child in children:
        depth(child.task_id)


def validate_dynamic_children(
    parent: PlannedTask,
    payload: object,
    existing_tasks: Collection[PlannedTask],
) -> tuple[PlannedTask, ...]:
    """Validate at most two non-recursive children owned by one root."""
    if not isinstance(parent, PlannedTask):
        _invalid("dynamic children require a planned task parent.")
    if parent.parent_id is not None:
        _invalid("dynamic children cannot be created recursively.")
    existing = tuple(existing_tasks)
    if any(not isinstance(task, PlannedTask) for task in existing):
        _invalid("existing tasks must be planned task records.")
    existing_ids = [task.task_id for task in existing]
    if len(existing_ids) != len(set(existing_ids)):
        _invalid("existing task IDs must be globally unique.")
    existing_id_set = set(existing_ids)
    existing_children = tuple(
        task for task in existing if task.parent_id == parent.task_id
    )

    _ensure_json_compatible(payload)
    mapping = _mapping(payload, "dynamic children")
    _exact_keys(mapping, _CHILDREN_KEYS, "dynamic children")
    raw_children = mapping["children"]
    if not isinstance(raw_children, list):
        _invalid("children must be an array.")
    if len(raw_children) > MAX_CHILDREN_PER_ROOT:
        _invalid("a root may create at most two dynamic children.")
    if len(existing_children) + len(raw_children) > MAX_CHILDREN_PER_ROOT:
        _invalid("a root may create at most two dynamic children.")

    seen_ids = set(existing_id_set)
    children: list[PlannedTask] = []
    for raw_child in raw_children:
        child = _parse_task(
            raw_child,
            parent_id=parent.task_id,
            seen_requirement_ids=set(),
        )
        if child.task_id in seen_ids:
            _invalid(f"duplicate task_id {child.task_id!r}.")
        seen_ids.add(child.task_id)
        children.append(child)
    validated_children = tuple(children)
    existing_sibling_ids = {task.task_id for task in existing_children}
    _validate_child_graph(existing_children, parent.task_id)
    _validate_child_graph(
        validated_children,
        parent.task_id,
        existing_sibling_ids,
    )
    return validated_children
