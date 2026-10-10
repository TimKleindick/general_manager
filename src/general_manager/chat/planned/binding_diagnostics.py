"""Describe rejected bindings from the executor's existing linked query rows."""

from collections.abc import Mapping
import json
from typing import Any

from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import CalculationBinding, PlannedTask

_MAX_SCALAR_PATHS = 64
_MAX_PATH_DEPTH = 16
_MAX_OBSERVED_NODES = 4096


def _row_scalar_paths(row: object) -> tuple[set[tuple[str, ...]], bool]:
    paths: set[tuple[str, ...]] = set()
    pending: list[tuple[tuple[str, ...], object]] = [((), row)]
    visited = 0
    truncated = False
    while pending:
        path, value = pending.pop()
        visited += 1
        if visited > _MAX_OBSERVED_NODES or len(paths) >= _MAX_SCALAR_PATHS:
            truncated = True
            break
        if len(path) > _MAX_PATH_DEPTH:
            truncated = True
            continue
        if isinstance(value, Mapping):
            for key in reversed(sorted(key for key in value if isinstance(key, str))):
                pending.append(((*path, key), value[key]))
        elif path and isinstance(value, (str, int, float, bool)):
            paths.add(path)
        # Collections and nulls supply no scalar path; never flatten their items.
    return paths, truncated


def _observed_scalar_paths(payload: object) -> dict[str, Any]:
    rows = payload.get("data") if isinstance(payload, Mapping) else None
    uniform: set[tuple[str, ...]] | None = None
    observed: set[tuple[str, ...]] = set()
    truncated = False
    for row in rows if isinstance(rows, list) else ():
        paths, clipped = _row_scalar_paths(row)
        uniform = paths if uniform is None else uniform & paths
        observed.update(paths)
        truncated = truncated or clipped
    uniform = uniform or set()
    return {
        "non_uniform_path_count": len(observed - uniform),
        "paths_truncated": truncated,
        "paths": [list(path) for path in sorted(uniform)],
    }


def binding_rejection_diagnostics(
    task: PlannedTask,
    requirement_id: str,
    binding: CalculationBinding,
    store: EvidenceStore,
    message: str,
) -> str:
    """Reproduce source errors without selecting fields or changing admission."""
    from general_manager.chat.planned.calculation_scope import (
        validate_calculation_binding_evidence,
    )
    from general_manager.chat.planned.calculations import CalculationError
    from general_manager.chat.planned.validation import (
        PlanValidationError,
        bind_calculation_requirement,
    )

    try:
        candidate = bind_calculation_requirement(task, requirement_id, binding)
    except (PlanValidationError, ValueError):
        return message
    if binding.value_path is None or len(binding.source_requirement_ids) != 1:
        return message
    source = next(
        req
        for req in task.requirements
        if req.requirement_id == binding.source_requirement_ids[0]
    )
    if source.kind != "query":
        return message
    errors: list[dict[str, str]] = []
    observations: dict[str, object] = {}
    for record in store.for_task(task.task_id):
        if record.kind != "query" or not store.is_linked_to(
            task.task_id, source.requirement_id, record.evidence_id
        ):
            continue
        individual = EvidenceStore()
        individual.add(record, requirement=source)
        try:
            validate_calculation_binding_evidence(
                candidate, requirement_id, binding, individual
            )
        except CalculationError as exc:
            errors.append({"evidence_id": record.evidence_id, "error": str(exc)})
            observations[record.evidence_id] = _observed_scalar_paths(record.payload())
        else:
            # A non-reproduced rejection cannot justify a new diagnostic claim.
            return message
    if not errors:
        return message
    return json.dumps(
        {
            "binding_paths": {
                "value_path": list(binding.value_path),
                "group_by": [list(path) for path in binding.group_by],
                "unit_path": list(binding.unit_path)
                if binding.unit_path is not None
                else None,
                "utc_year_by": [list(path) for path in binding.utc_year_by],
            },
            "source_errors": errors,
            "observed_scalar_paths": observations,
        },
        separators=(",", ":"),
    )
