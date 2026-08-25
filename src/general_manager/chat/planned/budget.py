"""One hard global and per-root round ledger for planned chat."""

from __future__ import annotations

from collections.abc import Collection
from typing import NoReturn


class RoundBudgetExhausted(RuntimeError):
    """Raised when a provider request would exceed a hard round limit."""

    def __init__(self, message: str, *, root_id: str | None = None) -> None:
        self.root_id = root_id
        super().__init__(message)


BudgetExhaustedError = RoundBudgetExhausted


def _budget_error(message: str, *, root_id: str | None = None) -> NoReturn:
    raise RoundBudgetExhausted(message, root_id=root_id)


def _type_error(message: str) -> NoReturn:
    raise TypeError(message)


def _value_error(message: str) -> NoReturn:
    raise ValueError(message)


def _key_error(message: str) -> NoReturn:
    raise KeyError(message)


class _RemainingView(dict[str, int]):
    """Mapping view that also supports convenient ``view(root_id)`` access."""

    def __call__(self, root_id: str) -> int:
        return self[root_id]


class RoundBudget:
    """Track every planned provider request in one global ledger.

    Global-only calls (planner and synthesizer) use :meth:`consume_global`.
    Executor calls use :meth:`consume_subtree`, which atomically charges both
    the global and owning-root ledgers.
    """

    subtree_limit = 15

    def __init__(self, root_ids: Collection[str]) -> None:
        if isinstance(root_ids, (str, bytes, bytearray)):
            _type_error("root_ids must be a collection of root IDs.")
        roots = tuple(root_ids)
        if any(
            not isinstance(root_id, str) or not root_id.strip() for root_id in roots
        ):
            _value_error("root IDs must be non-empty strings.")
        if len(set(roots)) != len(roots):
            _value_error("root IDs must be unique.")
        self.root_ids = roots
        self.global_limit = min(5 + 13 * len(roots), 80)
        self._global_count = 0
        self._subtree_counts = {root_id: 0 for root_id in roots}

    @property
    def global_count(self) -> int:
        return self._global_count

    @property
    def global_used(self) -> int:
        return self._global_count

    @property
    def global_remaining(self) -> int:
        return self.global_limit - self._global_count

    @property
    def remaining_global(self) -> int:
        return self.global_remaining

    @property
    def subtree_counts(self) -> dict[str, int]:
        return dict(self._subtree_counts)

    @property
    def subtree_used(self) -> dict[str, int]:
        return self.subtree_counts

    @property
    def subtree_remaining(self) -> _RemainingView:
        return _RemainingView(
            {
                root_id: self.subtree_limit - count
                for root_id, count in self._subtree_counts.items()
            }
        )

    @property
    def remaining(self) -> dict[str, int]:
        return {"global": self.global_remaining, **self.subtree_remaining}

    def subtree_count(self, root_id: str) -> int:
        self._ensure_root(root_id)
        return self._subtree_counts[root_id]

    def count(self, root_id: str | None = None) -> int:
        if root_id is None:
            return self.global_count
        return self.subtree_count(root_id)

    def consume_global(self) -> None:
        """Charge one planner/synthesizer request to the global budget."""
        if self.global_remaining <= 0:
            _budget_error("global planned-chat round budget is exhausted.")
        self._global_count += 1

    def consume_subtree(self, root_id: str) -> None:
        """Charge one executor request globally and to its owning root."""
        self._ensure_root(root_id)
        if self.global_remaining <= 0:
            _budget_error(
                "global planned-chat round budget is exhausted.", root_id=root_id
            )
        if self.subtree_remaining[root_id] <= 0:
            _budget_error(
                f"planned-chat subtree round budget for {root_id!r} is exhausted.",
                root_id=root_id,
            )
        self._global_count += 1
        self._subtree_counts[root_id] += 1

    def consume(self, root_id: str | None = None) -> None:
        """Charge one request, using global-only or executor semantics."""
        if root_id is None:
            self.consume_global()
        else:
            self.consume_subtree(root_id)

    def _ensure_root(self, root_id: str) -> None:
        if root_id not in self._subtree_counts:
            _key_error(f"unknown planned-chat root {root_id!r}.")


__all__ = ["BudgetExhaustedError", "RoundBudget", "RoundBudgetExhausted"]
