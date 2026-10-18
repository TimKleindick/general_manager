"""Expose selective discovery work without choosing types or granting evidence."""

from typing import Any

from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import PlannedTask

INSTRUCTION = (
    " SELECTIVE_SCHEMA_BINDING: pending_schema_bindings identifies explicit detail work. "
    "Inspect the manager's own compact overview first. observed_sources points only to "
    "current same-task observations in task_evidence; it is not a completed requirement. "
    "Follow the output field and root argument type signatures for the actual query and "
    "calculation paths. Select only missing object/input/enum definitions named by that "
    "manager's observed type_manifest. Before binding, discover every required "
    "next-hop input/enum/object name with targeted detail reads at that same snapshot; "
    "these discovery observations may remain unlinked and cannot complete the task. "
    "Once the needed type set is known, use bind_schema with requirement_id, types and "
    "the exact observed snapshot, then read those details with the same manager, names, "
    "snapshot and requirement_id. Binding alone supplies no schema evidence; "
    "read or repeat the exact selected definitions to link every bound type. "
    "Do not try to rebind or widen a declared selection. Manager references require their target's own "
    "overview and requirement. Never replace missing details with a full-schema rescue. "
    "Once current required fields, filters, entity identity and unit contracts are "
    "observed, bind calculations, calculate and complete with the eligible evidence; "
    "schema_complete=false for selective views is not an obstacle. Preserve declared "
    "legacy full requirements, snapshot/exposure checks and legitimate blockers."
)


def pending_schema_context(
    task: PlannedTask, evidence: EvidenceStore
) -> list[dict[str, Any]]:
    return [
        {
            "requirement_id": req.requirement_id,
            "manager": req.schema.manager,
            "required_action": "bind_schema",
            "binding_validated": False,
            "observed_sources": [
                {
                    "evidence_id": item.evidence_id,
                    "snapshot": item.payload()["snapshot"],
                }
                for item in evidence.schema_observations(
                    task.task_id, req.schema.manager
                )
            ],
        }
        for req in task.requirements
        if req.kind == "schema"
        and req.schema is not None
        and req.schema.view == "detail"
        and not req.schema.types
    ]
