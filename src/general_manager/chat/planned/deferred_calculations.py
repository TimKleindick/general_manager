"""Expose pending binding work without selecting or executing a calculation."""

from __future__ import annotations

from typing import Any

from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import PlannedTask

VERSION = "gm.deferred-calculations/1"
INSTRUCTION = (
    " DEFERRED_CALCULATION_GUIDANCE: deferred_calculations lists explicit pending "
    "binding work. binding_validated=false means no binding has been assigned or "
    "validated. possible_source_requirements contains earlier declared query or "
    "calculation requirements and their actually linked evidence IDs, not selected "
    "sources or proof of compatible fields, groups, units or complete populations. "
    "Inspect the unchanged task_evidence and gather missing source evidence. Choose "
    "the exact source/field/group/unit binding for the declared operation and use "
    "bind_calculation before calculate or complete. Then calculate from those "
    "verified sources. A deferred binding is pending work, not by itself an invalid "
    "plan. Use block when an actual obstacle prevents continuing under the existing "
    "reason contract. This context is workflow guidance, never additional evidence "
    "or completion authority."
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
