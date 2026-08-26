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
    REQUIREMENT_KINDS,
    ROUTING_FEATURE_VALUES,
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


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        _invalid(f"{label} must be a tuple.")
    result: list[str] = []
    for item in value:
        result.append(_required_text(item, label))
    if len(result) != len(set(result)):
        _invalid(f"{label} must not contain duplicates.")
    return tuple(result)


def _validate_requirement_record(
    value: object,
    seen_requirement_ids: set[str],
) -> EvidenceRequirement:
    if not isinstance(value, EvidenceRequirement):
        _invalid("requirements must contain EvidenceRequirement records.")
    requirement_id = _required_text(value.requirement_id, "requirement_id")
    if requirement_id in seen_requirement_ids:
        _invalid(f"duplicate requirement_id {requirement_id!r}.")
    seen_requirement_ids.add(requirement_id)
    if not isinstance(value.kind, str) or value.kind not in REQUIREMENT_KINDS:
        _invalid("requirement kind is not supported.")
    _required_text(value.description, "description")
    operation = value.operation
    if operation is not None and not isinstance(operation, str):
        _invalid("requirement operation must be a string or null.")
    if value.kind == "calculation":
        if operation not in CALCULATION_OPERATIONS:
            _invalid("calculation operation is not supported.")
    elif operation is not None:
        _invalid("only calculation requirements may define operation.")
    return value


def _expected_routing_features(
    depends_on: tuple[str, ...],
    requirements: tuple[EvidenceRequirement, ...],
) -> tuple[RoutingFeature, ...]:
    expected: list[RoutingFeature] = []
    if depends_on:
        expected.append("has_dependency")
    if any(requirement.kind == "calculation" for requirement in requirements):
        expected.append("requires_calculation")
    if sum(requirement.kind == "query" for requirement in requirements) > 1:
        expected.append("multiple_queries")
    return tuple(expected)


def _validate_task_record(value: object) -> PlannedTask:
    """Validate a runtime task record before using it as trusted graph state."""
    if not isinstance(value, PlannedTask):
        _invalid("existing tasks must be planned task records.")
    _required_text(value.task_id, "task_id")
    _required_text(value.objective, "objective")
    depends_on = _string_tuple(value.depends_on, "depends_on")
    if value.task_id in depends_on:
        _invalid(f"task {value.task_id!r} cannot depend on itself.")
    if not isinstance(value.requirements, tuple):
        _invalid("requirements must be a tuple.")
    seen_requirement_ids: set[str] = set()
    requirements = tuple(
        _validate_requirement_record(item, seen_requirement_ids)
        for item in value.requirements
    )
    completion_criteria = _string_tuple(
        value.completion_criteria, "completion_criteria"
    )
    requirement_ids = {requirement.requirement_id for requirement in requirements}
    if (
        len(completion_criteria) != len(requirements)
        or set(completion_criteria) != requirement_ids
    ):
        _invalid("completion_criteria must list every requirement exactly once.")
    routing_features = _string_tuple(value.routing_features, "routing_features")
    if any(feature not in ROUTING_FEATURE_VALUES for feature in routing_features):
        _invalid("routing_features contains an unsupported feature.")
    if set(routing_features) != set(
        _expected_routing_features(depends_on, requirements)
    ):
        _invalid("routing_features must match task structure.")
    if value.parent_id is not None:
        _required_text(value.parent_id, "parent_id")
        if value.parent_id == value.task_id:
            _invalid("a task cannot own itself.")
    return value


def _parse_requirement(
    value: object,
    seen_requirement_ids: set[str],
) -> EvidenceRequirement:
    mapping = _mapping(value, "requirement")
    _exact_keys(mapping, _REQUIREMENT_KEYS, "requirement")
    requirement_id = _required_text(mapping["requirement_id"], "requirement_id")
    kind = mapping["kind"]
    if not isinstance(kind, str) or kind not in REQUIREMENT_KINDS:
        _invalid("requirement kind is not supported.")
    description = _required_text(mapping["description"], "description")
    operation = mapping["operation"]
    if operation is not None and not isinstance(operation, str):
        _invalid("requirement operation must be a string or null.")
    parsed = EvidenceRequirement(
        requirement_id=requirement_id,
        kind=kind,
        description=description,
        operation=operation,
    )
    return _validate_requirement_record(parsed, seen_requirement_ids)


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
    routing_features = _string_list(mapping["routing_features"], "routing_features")
    parsed = PlannedTask(
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
    return _validate_task_record(parsed)


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
) -> None:
    child_ids = {child.task_id for child in children}
    children_by_id = {child.task_id: child for child in children}
    allowed_dependencies = child_ids | {parent_id}
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
        if task_id == parent_id:
            return 0
        if task_id in visiting:
            _invalid("dynamic child dependencies must be acyclic.")
        if task_id in depths:
            return depths[task_id]
        visiting.add(task_id)
        child = children_by_id[task_id]
        child_depth = 0
        if child.depends_on:
            child_depth = 1 + max(depth(dependency) for dependency in child.depends_on)
        visiting.remove(task_id)
        depths[task_id] = child_depth
        return child_depth

    # Evaluating every child lets the visiting guard detect all dependency cycles.
    for child in children:
        depth(child.task_id)


def _validate_dynamic_graph(records: Collection[PlannedTask]) -> None:
    """Validate every root subtree represented by dynamic task records."""
    task_records = tuple(records)
    for task_record in task_records:
        _validate_task_record(task_record)

    task_ids = [task.task_id for task in task_records]
    if len(task_ids) != len(set(task_ids)):
        _invalid("existing task IDs must be globally unique.")

    root_ids = {task.task_id for task in task_records if task.parent_id is None}
    children_by_root: dict[str, list[PlannedTask]] = {
        root_id: [] for root_id in root_ids
    }
    for task in task_records:
        if task.parent_id is None:
            continue
        if task.parent_id not in root_ids:
            _invalid("dynamic child parent must resolve to an existing root.")
        children_by_root[task.parent_id].append(task)

    for root_id, children in children_by_root.items():
        if len(children) > MAX_CHILDREN_PER_ROOT:
            _invalid("a root may create at most two dynamic children.")
        _validate_child_graph(tuple(children), root_id)


def validate_dynamic_children(
    parent: PlannedTask,
    payload: object,
    existing_tasks: Collection[PlannedTask],
) -> tuple[PlannedTask, ...]:
    """Validate at most two non-recursive children owned by one root."""
    parent = _validate_task_record(parent)
    if parent.parent_id is not None:
        _invalid("dynamic children cannot be created recursively.")
    try:
        existing = tuple(existing_tasks)
    except TypeError:
        _invalid("existing tasks must be a collection.")
    records = list(existing)
    if not any(task_record == parent for task_record in records):
        records.append(parent)
    _validate_dynamic_graph(records)
    existing_children = tuple(
        task for task in records if task.parent_id == parent.task_id
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

    seen_ids = {task.task_id for task in records}
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
    _validate_dynamic_graph((*records, *validated_children))
    return validated_children
