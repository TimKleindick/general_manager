"""Deterministic provider-role selection for planned task executors."""

from __future__ import annotations

from typing import Literal, NoReturn

from general_manager.chat.planned.models import PlannedTask


ExecutorRole = Literal["simple_executor", "complex_executor"]


def _type_error(message: str) -> NoReturn:
    raise TypeError(message)


def select_executor_role(
    task: PlannedTask,
    *,
    unique_manager: bool,
    path_depth: int | None,
    prior_failure: bool,
) -> ExecutorRole:
    """Choose the simple executor only when every approved fact is simple."""
    if not isinstance(task, PlannedTask):
        _type_error("task must be a PlannedTask.")
    has_dependency = bool(task.depends_on)
    requires_calculation = any(
        requirement.kind == "calculation" for requirement in task.requirements
    )
    query_count = sum(requirement.kind == "query" for requirement in task.requirements)
    relationship_is_shallow = path_depth is None or path_depth in (0, 1)
    simple = (
        not has_dependency
        and not requires_calculation
        and query_count <= 1
        and unique_manager is True
        and relationship_is_shallow
        and prior_failure is False
    )
    return "simple_executor" if simple else "complex_executor"


__all__ = ["ExecutorRole", "select_executor_role"]
