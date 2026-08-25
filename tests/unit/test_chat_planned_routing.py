"""Contract tests for deterministic planned-chat executor routing."""

from __future__ import annotations

from dataclasses import replace

import pytest

from general_manager.chat.planned.models import EvidenceRequirement, PlannedTask
from general_manager.chat.planned.routing import select_executor_role


def simple_task() -> PlannedTask:
    return PlannedTask(
        task_id="task-1",
        objective="Find parts.",
        depends_on=(),
        requirements=(EvidenceRequirement("query-1", "query", "Find parts.", None),),
        completion_criteria=("query-1",),
        routing_features=(),
    )


def test_simple_routing_requires_every_simple_condition() -> None:
    task = simple_task()

    assert (
        select_executor_role(
            task, unique_manager=True, path_depth=1, prior_failure=False
        )
        == "simple_executor"
    )
    assert (
        select_executor_role(
            task, unique_manager=False, path_depth=1, prior_failure=False
        )
        == "complex_executor"
    )


@pytest.mark.parametrize(
    "task",
    [
        replace(
            simple_task(), depends_on=("parent",), routing_features=("has_dependency",)
        ),
        replace(
            simple_task(),
            requirements=(
                EvidenceRequirement("calc-1", "calculation", "Compute it.", "sum"),
            ),
            completion_criteria=("calc-1",),
            routing_features=("requires_calculation",),
        ),
        replace(
            simple_task(),
            requirements=(
                EvidenceRequirement("query-1", "query", "First.", None),
                EvidenceRequirement("query-2", "query", "Second.", None),
            ),
            completion_criteria=("query-1", "query-2"),
            routing_features=("multiple_queries",),
        ),
    ],
)
def test_dependency_calculation_and_multiple_queries_require_complex_role(
    task: PlannedTask,
) -> None:
    assert (
        select_executor_role(
            task, unique_manager=True, path_depth=0, prior_failure=False
        )
        == "complex_executor"
    )


@pytest.mark.parametrize("path_depth", [-1, 2, 3])
def test_deep_or_invalid_relationship_path_requires_complex_role(
    path_depth: int,
) -> None:
    assert (
        select_executor_role(
            simple_task(),
            unique_manager=True,
            path_depth=path_depth,
            prior_failure=False,
        )
        == "complex_executor"
    )


def test_unknown_path_is_not_a_reason_to_reject_simple_task() -> None:
    assert (
        select_executor_role(
            simple_task(), unique_manager=True, path_depth=None, prior_failure=False
        )
        == "simple_executor"
    )


def test_prior_provider_or_no_progress_failure_requires_complex_role() -> None:
    assert (
        select_executor_role(
            simple_task(), unique_manager=True, path_depth=0, prior_failure=True
        )
        == "complex_executor"
    )
