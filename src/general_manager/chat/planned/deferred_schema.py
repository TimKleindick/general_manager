"""Expose selective discovery work without choosing types or granting evidence."""

from typing import Any

from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import PlannedTask

INSTRUCTION = (
    "SELECTIVE_SCHEMA_BINDING: read the manager's own overview; discover all needed next-hop "
    "object/input/enum names from its type_manifest and same-snapshot details. observed_sources are "
    "not completed requirements. When selectors are known, bind_schema once "
    "(requirement_id/types/snapshot), then read those exact definitions linked to that ID. Binding "
    "is not evidence. No rebind/widen/full rescue; manager references need their own "
    "overview/requirement. With fields, filters, identities and units witnessed, "
    "bind/calculate/complete; selective schema_complete=false is valid. Preserve legacy full and "
    "snapshot/exposure checks. "
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
