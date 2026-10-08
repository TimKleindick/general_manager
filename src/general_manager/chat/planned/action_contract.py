"""Executor action structure and bounded, task-local rejection diagnostics."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

from general_manager.chat.planned.contract import (
    CALCULATION_OPERATIONS,
    PLAN_SCHEMA,
    CALCULATION_BINDING_SCHEMA,
)
from general_manager.chat.planned.events import PLANNED_PUBLIC_MESSAGES
from general_manager.chat.planned.validation import MAX_CHILDREN_PER_ROOT


EXECUTOR_ACTION_FIELDS = {
    "complete": ("action", "evidence_ids"),
    "block": ("action", "reason"),
    "spawn_children": ("action", "children"),
    "calculate": ("action", "requirement_id", "operation", "operands"),
    "calculate_batch": ("action", "calculations"),
    "bind_calculation": ("action", "requirement_id", "binding"),
    "clarify_selector": ("action", "requirement_id", "language", "selector"),
}
CALCULATION_EVIDENCE_RULE = (
    "Use this task's requirement-linked query evidence at valid structured paths, "
    'or requirement-linked framework calculation evidence at path ["value"]. '
    "Derived values are recomputed from all original sources; no arbitrary literals. "
    "Match this task's "
    "calculation requirement and operation. count takes one collection operand; "
    "difference, ratio and percentage take two numeric operands. Other operations "
    "take at least one numeric value, optionally from one sequence. Values must "
    "be finite numbers; division by zero is invalid. Respect the declared source, "
    "group and unit binding; query reductions include every row of one complete "
    "group. Use calculate_batch for related calculations: all are validated before "
    "any evidence is committed. A queried scalar needs no singleton sum unless a "
    "declared aggregate requirement needs that group result. For a deferred binding=null, "
    "inspect schema/query evidence, then use bind_calculation with the exact source/field/group "
    "binding before calculation. A binding is assigned once and cannot be changed."
    " multiply is binary numeric multiplication, without inferred units. sum_products "
    "requires an explicit conversion binding from complete current query/schema "
    "evidence; operands alternate each row's quantity then its observed factor, "
    "in original group-row order. Numeric field names and raw unit strings do not "
    "prove dimensions. Every factor origin needs its observed same-object constructor "
    "identity_contract and own exposed schema. No scalar broadcasting or invented factors."
)
_MAX_DIAGNOSTIC_CHARACTERS = 1024


def executor_action_schema() -> dict[str, Any]:
    """Publish existing action and child rules without changing their validators."""
    child = deepcopy(cast(dict[str, Any], PLAN_SCHEMA)["properties"]["tasks"]["items"])
    child["properties"]["task_id"]["description"] = (
        "Globally unique across existing tasks and this proposed child batch."
    )
    child["properties"]["depends_on"]["description"] = (
        "Distinct IDs of this child's owning root or sibling children only, "
        "including later siblings in the proposed batch or existing siblings. "
        "Never self, another subtree, or a dependency cycle."
    )
    from general_manager.chat.planned.selector_clarification import (
        SELECTOR_CLARIFICATION_SCHEMA,
    )

    selector = SELECTOR_CLARIFICATION_SCHEMA["properties"]["clarification"][
        "properties"
    ]
    properties: dict[str, dict[str, Any]] = {
        "clarify_selector": {
            "requirement_id": {"type": "string", "minLength": 1},
            "language": deepcopy(selector["language"]),
            "selector": deepcopy(selector["selector"]),
        },
        "bind_calculation": {
            "requirement_id": {"type": "string", "minLength": 1},
            "binding": deepcopy(CALCULATION_BINDING_SCHEMA),
        },
        "complete": {
            "evidence_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
                "description": (
                    "Every listed ID must identify evidence linked to a declared "
                    "requirement of this task. Select the final relevant results; earlier "
                    "unselected queries are not supplied to synthesis. Selected evidence "
                    "and its verified calculation sources must cover every requirement. "
                    "Operational feedback is not evidence."
                ),
            }
        },
        "block": {"reason": {"enum": list(PLANNED_PUBLIC_MESSAGES)}},
        "spawn_children": {
            "children": {
                "type": "array",
                "maxItems": MAX_CHILDREN_PER_ROOT,
                "items": child,
                "description": (
                    "Only an existing root may create children; recursive children "
                    f"are invalid. The cumulative limit is {MAX_CHILDREN_PER_ROOT} "
                    "children per root, including existing children and this batch. "
                    "Use the parent requirement_id for a child requirement when several "
                    "parent requirements share its kind/operation; ambiguous child evidence "
                    "stays reference-only until explicitly linked by a parent read call. "
                    "An empty batch is allowed. parent_id is assigned internally; "
                    "do not supply it. Child acceptance supplies no evidence and "
                    "does not satisfy completion."
                ),
            }
        },
        "calculate": {
            "requirement_id": {
                "type": "string",
                "description": "The ID of this task's declared calculation requirement.",
            },
            "operation": {
                "enum": list(CALCULATION_OPERATIONS),
                "description": "Exactly the operation declared by that requirement.",
            },
            "operands": {
                "type": "array",
                "description": CALCULATION_EVIDENCE_RULE,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["evidence_id", "path"],
                    "properties": {
                        "evidence_id": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": r"\S",
                        },
                        "path": {
                            "type": "array",
                            "items": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "integer", "minimum": 0},
                                ]
                            },
                        },
                    },
                },
            },
        },
    }
    properties["calculate_batch"] = {
        "calculations": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(EXECUTOR_ACTION_FIELDS["calculate"]),
                "properties": {
                    "action": {"const": "calculate"},
                    **deepcopy(properties["calculate"]),
                },
            },
        }
    }
    return {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": list(fields),
                "properties": {"action": {"const": action}, **properties[action]},
            }
            for action, fields in EXECUTOR_ACTION_FIELDS.items()
        ]
    }


def action_validation_feedback(
    code: str, path: str, expected: object, *, detail: str | None = None
) -> dict[str, object]:
    """Retain ordinary validator details verbatim, marking oversized excerpts."""
    feedback = {"code": code, "path": path, "expected": expected}
    if detail is not None:
        feedback["detail"] = detail
    for key, value in tuple(feedback.items()):
        if isinstance(value, str) and len(value) > _MAX_DIAGNOSTIC_CHARACTERS:
            feedback[key] = value[: _MAX_DIAGNOSTIC_CHARACTERS - 1] + "…"
            feedback["truncated"] = True
    return feedback
