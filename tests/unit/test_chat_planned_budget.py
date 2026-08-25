"""Contract tests for planned-chat round accounting."""

from __future__ import annotations

import pytest

from general_manager.chat.planned.budget import RoundBudget, RoundBudgetExhausted


def test_round_budget_enforces_subtree_15_and_global_formula() -> None:
    budget = RoundBudget(["task_1", "task_2"])

    assert budget.global_limit == 31
    assert budget.subtree_limit == 15
    for _ in range(15):
        budget.consume_subtree("task_1")
    with pytest.raises(RoundBudgetExhausted):
        budget.consume_subtree("task_1")
    assert budget.global_count == 15
    assert budget.subtree_count("task_1") == 15
    assert budget.global_remaining == 16
    assert budget.subtree_remaining("task_1") == 0


def test_subtree_consumption_is_atomic_when_global_budget_is_exhausted() -> None:
    budget = RoundBudget(["task_1", "task_2", "task_3", "task_4", "task_5", "task_6"])
    for _ in range(80):
        budget.consume_global()
    assert budget.global_limit == 80

    with pytest.raises(RoundBudgetExhausted):
        budget.consume_subtree("task_1")
    assert budget.global_count == 80
    assert budget.subtree_count("task_1") == 0


def test_global_consumption_is_for_planner_and_synthesis_without_root() -> None:
    budget = RoundBudget(["task_1"])

    budget.consume_global()
    budget.consume(root_id=None)

    assert budget.global_count == 2
    assert budget.subtree_count("task_1") == 0


def test_unknown_root_and_invalid_root_collections_are_rejected() -> None:
    with pytest.raises(ValueError):
        RoundBudget(["task_1", "task_1"])
    with pytest.raises(ValueError):
        RoundBudget([""])
    budget = RoundBudget(["task_1"])
    with pytest.raises(KeyError):
        budget.consume_subtree("missing")


def test_budget_has_no_reserve_or_borrowing_and_reports_per_root_remaining() -> None:
    budget = RoundBudget(["task_1", "task_2"])

    assert not hasattr(budget, "reserve")
    assert not hasattr(budget, "borrow")
    assert budget.remaining == {"global": 31, "task_1": 15, "task_2": 15}
    budget.consume_subtree("task_2")
    assert budget.remaining["global"] == 30
    assert budget.remaining["task_2"] == 14
