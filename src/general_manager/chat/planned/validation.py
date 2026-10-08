"""Strict validation for planned-chat plans and bounded task children."""

from __future__ import annotations

from dataclasses import replace

from collections.abc import Collection, Mapping
import json
from math import isfinite
from typing import NoReturn, cast

from general_manager.chat.planned.contract import (
    CALCULATION_OPERATIONS,
    COMPLETION_CRITERIA_RULE,
    DEPENDENCY_RULE,
    MAX_ROOT_DEPENDENCY_DEPTH as MAX_ROOT_DEPENDENCY_DEPTH,
    MAX_ROOT_TASKS as MAX_ROOT_TASKS,
    PLAN_FIELDS,
    PLAN_INTENTS,
    REQUIREMENT_FIELDS,
    REQUIREMENT_KINDS,
    ROUTING_FEATURE_VALUES,
    ROUTING_FEATURES_RULE,
    TASK_FIELDS,
    expected_routing_features,
)
from general_manager.chat.planned.models import (
    EvidenceRequirement,
    CalculationBinding,
    SchemaBinding,
    PlannedTask,
    RequirementKind,
    RoutingFeature,
    ValidatedPlan,
)


MAX_CHILDREN_PER_ROOT = 2
_PLAN_KEYS = frozenset(PLAN_FIELDS)
_TASK_KEYS = frozenset(TASK_FIELDS)
_REQUIREMENT_KEYS = frozenset(REQUIREMENT_FIELDS)
_CHILDREN_KEYS = frozenset(("children",))
_INTENTS = frozenset(PLAN_INTENTS)


class PlanValidationError(ValueError):
    """Private validation detail with a stable public ``invalid_plan`` reason."""

    reason = "invalid_plan"
    code = "invalid_plan"

    def __init__(
        self, detail: str, *, path: str = "$", expected: str | None = None
    ) -> None:
        self.path = path
        self.expected = detail if expected is None else expected
        self.detail = f"{path}: expected {self.expected}. {detail}"
        super().__init__(self.reason)


def _invalid(detail: str, *, path: str = "$", expected: str | None = None) -> NoReturn:
    raise PlanValidationError(detail, path=path, expected=expected)


def _field_path(path: str, field: str) -> str:
    return f"{path}.{field}" if field.isidentifier() else f"{path}[{json.dumps(field)}]"


def _ensure_json_compatible(
    value: object, active: set[int] | None = None, path: str = "$"
) -> None:
    """Reject Python values that cannot be represented by strict JSON."""
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            _invalid("Non-finite number.", path=path, expected="a finite JSON number")
        return
    if active is None:
        active = set()
    if isinstance(value, (Mapping, list)):
        identity = id(value)
        if identity in active:
            _invalid("Cyclic value.", path=path, expected="an acyclic JSON value")
        active.add(identity)
        try:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    if not isinstance(key, str):
                        _invalid(
                            "Non-string object key.",
                            path=path,
                            expected="string object keys",
                        )
                    _ensure_json_compatible(item, active, _field_path(path, key))
            else:
                for index, item in enumerate(value):
                    _ensure_json_compatible(item, active, f"{path}[{index}]")
        finally:
            active.remove(identity)
        return
    _invalid("Non-JSON value.", path=path, expected="only JSON-compatible values")


def _mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _invalid("Not an object.", path=path, expected="a JSON object")
    return cast(Mapping[str, object], value)


def _exact_keys(
    value: Mapping[str, object], expected: frozenset[str], path: str
) -> None:
    actual = frozenset(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        _invalid(
            "Missing required field.",
            path=_field_path(path, missing[0]),
            expected="a required field; exact object fields are "
            + ", ".join(sorted(expected)),
        )
    if unknown:
        _invalid(
            "Unknown field.",
            path=_field_path(path, unknown[0]),
            expected="no extra fields; exact object fields are "
            + ", ".join(sorted(expected)),
        )


def _required_text(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid("Invalid text.", path=path, expected="a non-empty string")
    return value


def _string_list(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        _invalid(
            "Not an array.",
            path=path,
            expected="an array of distinct non-empty strings",
        )
    result = tuple(
        _required_text(item, f"{path}[{index}]") for index, item in enumerate(value)
    )
    if len(result) != len(set(result)):
        _invalid("Duplicate value.", path=path, expected="distinct non-empty strings")
    return result


def _string_tuple(value: object, path: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        _invalid(
            "Not a tuple.", path=path, expected="a tuple of distinct non-empty strings"
        )
    result = tuple(
        _required_text(item, f"{path}[{index}]") for index, item in enumerate(value)
    )
    if len(result) != len(set(result)):
        _invalid("Duplicate value.", path=path, expected="distinct non-empty strings")
    return result


def _validate_requirement_record(
    value: object, seen_requirement_ids: set[str], path: str = "$"
) -> EvidenceRequirement:
    if not isinstance(value, EvidenceRequirement):
        _invalid(
            "Invalid requirement record.",
            path=path,
            expected="an EvidenceRequirement record",
        )
    requirement_id = _required_text(value.requirement_id, f"{path}.requirement_id")
    if requirement_id in seen_requirement_ids:
        _invalid(
            "Duplicate requirement ID.",
            path=f"{path}.requirement_id",
            expected="an ID unique within this task",
        )
    seen_requirement_ids.add(requirement_id)
    if not isinstance(value.kind, str) or value.kind not in REQUIREMENT_KINDS:
        _invalid(
            "Unsupported kind.",
            path=f"{path}.kind",
            expected="one of " + ", ".join(sorted(REQUIREMENT_KINDS)),
        )
    _required_text(value.description, f"{path}.description")
    operation = value.operation
    if value.kind == "calculation":
        if not isinstance(operation, str) or operation not in CALCULATION_OPERATIONS:
            _invalid(
                "Unsupported calculation operation.",
                path=f"{path}.operation",
                expected="one of " + ", ".join(CALCULATION_OPERATIONS),
            )
    elif operation is not None:
        _invalid(
            "Only calculation requirements define an operation.",
            path=f"{path}.operation",
            expected="null for kind " + value.kind,
        )
    if value.schema is not None:
        if value.kind != "schema" or not isinstance(value.schema, SchemaBinding):
            _invalid(
                "Only schema requirements define a schema binding.",
                path=f"{path}.schema",
                expected="a SchemaBinding on kind schema",
            )
        try:
            SchemaBinding.from_mapping(value.schema.as_mapping())
        except ValueError:
            _invalid(
                "Invalid schema binding.",
                path=f"{path}.schema",
                expected="an exact manager/view/type/snapshot binding",
            )
    return value


def _validate_task_record(value: object, path: str = "$") -> PlannedTask:
    """Validate a runtime task record before using it as trusted graph state."""
    if not isinstance(value, PlannedTask):
        _invalid("Invalid task record.", path=path, expected="a PlannedTask record")
    _required_text(value.task_id, f"{path}.task_id")
    _required_text(value.objective, f"{path}.objective")
    depends_on = _string_tuple(value.depends_on, f"{path}.depends_on")
    if value.task_id in depends_on:
        _invalid(
            "A task cannot depend on itself.",
            path=f"{path}.depends_on[{depends_on.index(value.task_id)}]",
            expected="another task ID",
        )
    if not isinstance(value.requirements, tuple):
        _invalid(
            "Invalid requirements.",
            path=f"{path}.requirements",
            expected="a tuple of EvidenceRequirement records",
        )
    seen_requirement_ids: set[str] = set()
    requirements = tuple(
        _validate_requirement_record(
            item, seen_requirement_ids, f"{path}.requirements[{index}]"
        )
        for index, item in enumerate(value.requirements)
    )
    earlier: dict[str, EvidenceRequirement] = {}
    for index, requirement in enumerate(requirements):
        binding = requirement.binding
        if binding is not None:
            sources = [earlier.get(source) for source in binding.source_requirement_ids]
            query_binding = binding.value_path is not None
            valid = requirement.kind == "calculation" and all(
                source is not None for source in sources
            )
            if binding.conversion is not None:
                valid = (
                    valid
                    and requirement.operation == "sum_products"
                    and len(sources) == 1
                    and sources[0] is not None
                    and sources[0].kind == "query"
                    and all(
                        earlier.get(source) is not None
                        and earlier[source].kind == "schema"
                        for source in binding.conversion.schema_requirement_ids
                    )
                )
            elif requirement.operation == "sum_products":
                valid = False
            elif query_binding:
                valid = (
                    valid
                    and len(sources) == 1
                    and all(
                        source is not None and source.kind == "query"
                        for source in sources
                    )
                )
                valid = valid and requirement.operation in {
                    "count",
                    "sum",
                    "average",
                    "minimum",
                    "maximum",
                }
                valid = valid and (
                    not binding.value_path
                    and not binding.group_by
                    and not binding.utc_year_by
                    if requirement.operation == "count"
                    else bool(binding.value_path)
                )
            else:
                valid = valid and all(
                    source is not None and source.kind == "calculation"
                    for source in sources
                )
            if not valid:
                _invalid(
                    "Invalid calculation source graph.",
                    path=f"{path}.requirements[{index}].binding",
                    expected="earlier compatible query or calculation requirements in this task",
                )
        earlier[requirement.requirement_id] = requirement
    completion_criteria = _string_tuple(
        value.completion_criteria, f"{path}.completion_criteria"
    )
    requirement_ids = [requirement.requirement_id for requirement in requirements]
    if len(completion_criteria) != len(requirements) or set(completion_criteria) != set(
        requirement_ids
    ):
        _invalid(
            COMPLETION_CRITERIA_RULE,
            path=f"{path}.completion_criteria",
            expected="exactly this task's own requirement IDs "
            + json.dumps(requirement_ids),
        )
    routing_features = _string_tuple(value.routing_features, f"{path}.routing_features")
    expected = expected_routing_features(
        depends_on, (requirement.kind for requirement in requirements)
    )
    if any(
        feature not in ROUTING_FEATURE_VALUES for feature in routing_features
    ) or set(routing_features) != set(expected):
        _invalid(
            ROUTING_FEATURES_RULE,
            path=f"{path}.routing_features",
            expected="exactly " + json.dumps(expected),
        )
    if value.parent_id is not None:
        _required_text(value.parent_id, f"{path}.parent_id")
        if value.parent_id == value.task_id:
            _invalid(
                "A task cannot own itself.",
                path=f"{path}.parent_id",
                expected="another task ID",
            )
    return value


def _parse_requirement(
    value: object, seen_requirement_ids: set[str], path: str
) -> EvidenceRequirement:
    mapping = _mapping(value, path)
    _exact_keys(
        mapping,
        _REQUIREMENT_KEYS | {"binding"}
        if mapping.get("kind") == "calculation"
        else _REQUIREMENT_KEYS | {"schema"}
        if mapping.get("kind") == "schema" and "schema" in mapping
        else _REQUIREMENT_KEYS,
        path,
    )
    try:
        binding = (
            CalculationBinding.from_mapping(mapping["binding"])
            if mapping.get("kind") == "calculation" and mapping["binding"] is not None
            else None
        )
    except ValueError:
        _invalid(
            "Invalid calculation binding.",
            path=f"{path}.binding",
            expected="a structured source/field/group binding",
        )
    try:
        schema = (
            SchemaBinding.from_mapping(mapping["schema"])
            if "schema" in mapping
            else None
        )
    except ValueError:
        _invalid(
            "Invalid schema binding.",
            path=f"{path}.schema",
            expected="an exact manager/view/type/snapshot binding",
        )
    # Runtime validation below checks every field before trusting these annotations.
    parsed = EvidenceRequirement(
        requirement_id=cast(str, mapping["requirement_id"]),
        kind=cast("RequirementKind", mapping["kind"]),
        description=cast(str, mapping["description"]),
        operation=cast("str | None", mapping["operation"]),
        binding=binding,
        binding_required=mapping.get("kind") == "calculation",
        schema=schema,
    )
    return _validate_requirement_record(parsed, seen_requirement_ids, path)


def _parse_task(
    value: object,
    *,
    parent_id: str | None,
    seen_requirement_ids: set[str],
    path: str = "$",
) -> PlannedTask:
    mapping = _mapping(value, path)
    _exact_keys(mapping, _TASK_KEYS, path)
    task_id = _required_text(mapping["task_id"], f"{path}.task_id")
    objective = _required_text(mapping["objective"], f"{path}.objective")
    depends_on = _string_list(mapping["depends_on"], f"{path}.depends_on")
    raw_requirements = mapping["requirements"]
    if not isinstance(raw_requirements, list):
        _invalid(
            "Invalid requirements.",
            path=f"{path}.requirements",
            expected="an array of requirements",
        )
    requirements = tuple(
        _parse_requirement(item, seen_requirement_ids, f"{path}.requirements[{index}]")
        for index, item in enumerate(raw_requirements)
    )
    completion_criteria = _string_list(
        mapping["completion_criteria"], f"{path}.completion_criteria"
    )
    routing_features = _string_list(
        mapping["routing_features"], f"{path}.routing_features"
    )
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
    return _validate_task_record(parsed, path)


def _validate_root_graph(tasks: tuple[PlannedTask, ...]) -> None:
    depths: dict[str, int] = {}
    for index, task in enumerate(tasks):
        # Requiring earlier roots also rejects every possible root cycle.
        for dependency_index, dependency in enumerate(task.depends_on):
            if dependency not in depths:
                _invalid(
                    DEPENDENCY_RULE,
                    path=f"$.tasks[{index}].depends_on[{dependency_index}]",
                    expected="an earlier root task ID from " + json.dumps(list(depths)),
                )
        depth = (
            1 + max(depths[dependency] for dependency in task.depends_on)
            if task.depends_on
            else 0
        )
        if depth > MAX_ROOT_DEPENDENCY_DEPTH:
            _invalid(
                DEPENDENCY_RULE,
                path=f"$.tasks[{index}].depends_on",
                expected=f"root dependency depth at most {MAX_ROOT_DEPENDENCY_DEPTH} edge",
            )
        depths[task.task_id] = depth


def validate_plan(payload: object) -> ValidatedPlan:
    """Validate one complete JSON plan before any application data access."""
    _ensure_json_compatible(payload)
    mapping = _mapping(payload, "$")
    _exact_keys(mapping, _PLAN_KEYS, "$")
    intent = mapping["intent"]
    if not isinstance(intent, str) or intent not in _INTENTS:
        _invalid(
            "Unsupported intent.",
            path="$.intent",
            expected="one of " + ", ".join(PLAN_INTENTS),
        )
    raw_tasks = mapping["tasks"]
    if not isinstance(raw_tasks, list):
        _invalid("Invalid tasks.", path="$.tasks", expected="an array of tasks")
    if intent == "read" and not 1 <= len(raw_tasks) <= MAX_ROOT_TASKS:
        _invalid(
            "Invalid read task count.",
            path="$.tasks",
            expected=f"1..{MAX_ROOT_TASKS} root tasks",
        )
    if intent == "mutation" and raw_tasks:
        _invalid("Mutation plans cannot contain tasks.", path="$.tasks", expected="[]")
    seen_task_ids: set[str] = set()
    tasks: list[PlannedTask] = []
    for index, raw_task in enumerate(raw_tasks):
        parsed = _parse_task(
            raw_task,
            parent_id=None,
            seen_requirement_ids=set(),
            path=f"$.tasks[{index}]",
        )
        if parsed.task_id in seen_task_ids:
            _invalid(
                "Duplicate task ID.",
                path=f"$.tasks[{index}].task_id",
                expected="a globally unique task_id",
            )
        seen_task_ids.add(parsed.task_id)
        tasks.append(parsed)
    validated_tasks = tuple(tasks)
    _validate_root_graph(validated_tasks)
    return ValidatedPlan(intent=intent, tasks=validated_tasks)


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
    for index, raw_child in enumerate(raw_children):
        child = _parse_task(
            raw_child,
            parent_id=parent.task_id,
            seen_requirement_ids=set(),
            path=f"$.children[{index}]",
        )
        if child.task_id in seen_ids:
            _invalid(f"duplicate task_id {child.task_id!r}.")
        seen_ids.add(child.task_id)
        children.append(child)
    validated_children = tuple(children)
    _validate_dynamic_graph((*records, *validated_children))
    return validated_children


def bind_calculation_requirement(
    task: PlannedTask, requirement_id: str, binding: CalculationBinding
) -> PlannedTask:
    """Finalize a deferred field binding without replacing an existing contract."""
    target = next(
        (
            req
            for req in task.requirements
            if req.requirement_id == requirement_id and req.kind == "calculation"
        ),
        None,
    )
    if target is None or not target.binding_required or target.binding is not None:
        _invalid(
            "Calculation binding is not deferred.",
            path="$.requirement_id",
            expected="an unbound calculation requirement in this task",
        )
    updated = replace(
        task,
        requirements=tuple(
            replace(req, binding=binding) if req is target else req
            for req in task.requirements
        ),
    )
    return _validate_task_record(updated)
