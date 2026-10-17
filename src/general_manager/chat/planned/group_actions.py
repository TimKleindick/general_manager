"""Expand explicitly selected complete queries into ordinary bound group sums."""

from collections.abc import Mapping, Sequence
import json
from typing import NoReturn

from general_manager.chat.planned.calculation_scope import group_key
from general_manager.chat.planned.calculations import CalculationError
from general_manager.chat.planned.evidence import EvidenceStore
from general_manager.chat.planned.models import PlannedTask


class GroupCalculationError(CalculationError):
    """Identify the explicit request that cannot supply complete group sums."""

    def __init__(self, request_index: int, message: str) -> None:
        super().__init__(message)
        self.request_index = request_index


def _invalid(message: str) -> NoReturn:
    raise CalculationError(message)


def expand_group_calculations(
    task: PlannedTask,
    requests: Sequence[Mapping[str, object]],
    store: EvidenceStore,
) -> tuple[tuple[int, dict[str, object]], ...]:
    """Generate exact paths; the existing calculator must validate every result.

    No evidence or binding is assigned here. Distinct declared requirements
    bound the request count, and complete observed rows bound the group count.
    """
    actions: list[tuple[int, dict[str, object]]] = []
    for request_index, request in enumerate(requests):
        try:
            requirement_index = next(
                (
                    index
                    for index, item in enumerate(task.requirements)
                    if item.requirement_id == request["requirement_id"]
                ),
                None,
            )
            if requirement_index is None:
                _invalid("Select a declared calculation requirement of this task.")
            requirement = task.requirements[requirement_index]
            binding = requirement.binding
            if (
                requirement.kind != "calculation"
                or requirement.operation != "sum"
                or binding is None
                or not binding.value_path
                or len(binding.source_requirement_ids) != 1
                or binding.conversion is not None
                or binding.cross_query is not None
            ):
                _invalid(
                    "calculate_groups requires an already bound ordinary direct query sum."
                )
            source_id = binding.source_requirement_ids[0]
            if not any(
                source.requirement_id == source_id and source.kind == "query"
                for source in task.requirements[:requirement_index]
            ):
                _invalid("The sole bound source must be an earlier query requirement.")
            query_id = request["query_evidence_id"]
            record = store.get(query_id) if isinstance(query_id, str) else None
            if (
                record is None
                or record.kind != "query"
                or record.task_id != task.task_id
                or not store.is_linked_to(task.task_id, source_id, record.evidence_id)
            ):
                _invalid(
                    "Select task-owned query evidence linked to the exact bound source."
                )
            payload = record.payload()
            rows = payload.get("data") if isinstance(payload, Mapping) else None
            if (
                not isinstance(rows, list)
                or not rows
                or payload.get("complete") is not True
                or payload.get("has_more") is not False
                or type(payload.get("total_count")) is not int
                or payload["total_count"] != len(rows)
            ):
                _invalid(
                    "Group sums require a nonempty verified complete root query population."
                )
            groups: dict[str, list[int]] = {}
            for row_index, row in enumerate(rows):
                key = json.dumps(
                    group_key(row, binding), sort_keys=True, allow_nan=False
                )
                groups.setdefault(key, []).append(row_index)
            for indices in groups.values():
                actions.append(
                    (
                        request_index,
                        {
                            "action": "calculate",
                            "requirement_id": requirement.requirement_id,
                            "operation": "sum",
                            "operands": [
                                {
                                    "evidence_id": record.evidence_id,
                                    "path": ["data", row_index, *binding.value_path],
                                }
                                for row_index in indices
                            ],
                        },
                    )
                )
        except (CalculationError, KeyError, TypeError, ValueError) as exc:
            raise GroupCalculationError(request_index, str(exc)) from exc
    return tuple(actions)
