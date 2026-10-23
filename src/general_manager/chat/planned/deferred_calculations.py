"""Expose pending binding work without selecting or executing a calculation."""

from __future__ import annotations

from typing import Any

from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import PlannedTask

VERSION = "gm.deferred-calculations/1"
INSTRUCTION = (
    "DEFERRED_CALCULATION_GUIDANCE: binding_validated=false is pending work, not invalid_plan. "
    "possible_source_requirements are candidates, not compatible selected sources. Gather evidence, "
    "bind exact source/field/group/unit with bind_calculation, then calculate the declared "
    "operation. Never complete unbound work. cross_query needs two ordered direct ungrouped sum IDs "
    "and earlier schemas proving common typed entity, gte/lt UTC period, units and stable "
    "query-time provenance. Guidance is not evidence; block only for an actual obstacle. "
)


def deferred_calculation_context(
    task: PlannedTask, evidence: EvidenceStore
) -> list[dict[str, Any]]:
    """List only explicit deferred requirements and task-local earlier sources."""
    sources: list[dict[str, Any]] = []
    result = []
    for requirement in task.requirements:
        if (
            requirement.kind == "calculation"
            and requirement.binding_required
            and requirement.binding is None
        ):
            result.append(
                {
                    "requirement_id": requirement.requirement_id,
                    "operation": requirement.operation,
                    "required_action": "bind_calculation",
                    "binding_validated": False,
                    "possible_source_requirements": [
                        dict(source) for source in sources
                    ],
                }
            )
        if requirement.kind in {"query", "calculation"}:
            sources.append(
                {
                    "requirement_id": requirement.requirement_id,
                    "kind": requirement.kind,
                    "evidence_ids": [
                        record.evidence_id
                        for record in evidence.for_requirement(
                            task.task_id, requirement
                        )
                    ],
                }
            )
    return result
