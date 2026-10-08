"""Explicit task completion selections and their verified arithmetic ancestry."""

from collections.abc import Sequence
from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate_evidence,
)
from general_manager.chat.planned.evidence import EvidenceRecord, EvidenceStore
from general_manager.chat.planned.models import EvidenceRequirement


def selected_evidence(
    store: EvidenceStore, task_id: str, identifiers: Sequence[str]
) -> tuple[EvidenceRecord, ...]:
    """Return selected snapshots and necessary sources, never all task history."""
    selected: set[str] = set()
    pending = list(identifiers)
    while pending:
        evidence_id = pending.pop()
        if evidence_id in selected:
            continue
        record = store.get(evidence_id)
        if (
            record is None
            or record.task_id != task_id
            or not store.is_linked(task_id, evidence_id)
            or not store.schema_current(record)
        ):
            message = "selected evidence must be linked to the current task."
            raise CalculationError(message)
        if record.kind == "calculation":
            # This verifies the entire ancestry, including identity, task and links.
            calculate_evidence(
                "selection-validation",
                task_id,
                "sum",
                [CalculationOperand(evidence_id, ("value",))],
                store,
                require_linked=True,
            )
            pending.extend(item["evidence_id"] for item in record.payload()["operands"])
        selected.add(evidence_id)
    return tuple(record for record in store.records if record.evidence_id in selected)


def requirement_coverage(
    store: EvidenceStore,
    task_id: str,
    requirement: EvidenceRequirement,
    selected: Sequence[EvidenceRecord],
) -> tuple[bool, dict[str, object]]:
    """Describe the same exact coverage check using verified selected ancestry."""
    import json
    from collections import Counter
    from general_manager.chat.planned.calculation_scope import group_key

    diagnostic: dict[str, object] = {
        "requirement_id": requirement.requirement_id,
        "kind": requirement.kind,
    }
    records = [
        record
        for record in selected
        if record in store.for_requirement(task_id, requirement)
    ]
    if not records:
        diagnostic["code"] = "missing_selected_evidence"
        return False, diagnostic
    if requirement.schema is not None and requirement.schema.view == "detail":
        covered = {
            name for record in records for name in record.payload().get("types", {})
        }
        missing = sorted(set(requirement.schema.types) - covered)
        diagnostic.update(code="missing_schema_types", missing_types=missing)
        return not missing, diagnostic
    binding = requirement.binding
    if binding is None or binding.value_path is None:
        return True, diagnostic
    scopes = [record.payload().get("scope") for record in records]
    if any(not isinstance(scope, dict) for scope in scopes):
        diagnostic["code"] = "missing_verified_scope"
        return False, diagnostic
    query_ids = {scope["query_evidence_id"] for scope in scopes}
    if len(query_ids) != 1:
        diagnostic["code"] = "incompatible_query_populations"
        return False, diagnostic
    source = store.get(next(iter(query_ids)))
    if source is None:
        diagnostic["code"] = "missing_query_population"
        return False, diagnostic
    rows = source.payload()["data"]
    expected = {json.dumps(group_key(row, binding), sort_keys=True) for row in rows}
    if not rows and requirement.operation == "count":
        expected = {"[]"}
    observed = [
        json.dumps(group, sort_keys=True)
        for scope in scopes
        for group in scope["groups"]
    ]
    counts = Counter(observed)
    diagnostic.update(
        code="incomplete_group_coverage",
        query_evidence_id=source.evidence_id,
        missing_groups=[json.loads(key) for key in sorted(expected - set(observed))],
        duplicate_groups=[json.loads(key) for key in sorted(counts) if counts[key] > 1],
        unexpected_groups=[json.loads(key) for key in sorted(set(observed) - expected)],
    )
    return len(observed) == len(expected) and set(observed) == expected, diagnostic


def covers_requirement(
    store: EvidenceStore,
    task_id: str,
    requirement: EvidenceRequirement,
    selected: Sequence[EvidenceRecord],
) -> bool:
    """Require every declared query group, not merely one valid group result."""
    return requirement_coverage(store, task_id, requirement, selected)[0]


def completion_requirement_diagnostics(
    store: EvidenceStore,
    task_id: str,
    requirements: Sequence[EvidenceRequirement],
    selected: Sequence[EvidenceRecord],
) -> str:
    """Name unmet requirements without interpreting business text or relations."""
    import json

    results = [
        requirement_coverage(store, task_id, req, selected) for req in requirements
    ]
    return json.dumps(
        {"requirements": [detail for covered, detail in results if not covered]}
    )
