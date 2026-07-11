from __future__ import annotations

from collections.abc import Generator

import pytest

from general_manager.manager.group_manager import GroupManager
from tests.perf.support import Counter, PerfBudgets

pytestmark = pytest.mark.perf

ROW_COUNT = 10_000


class AggregateInterface:
    @classmethod
    def get_attributes(cls) -> dict[str, object]:
        return {"label": {}, "amount": {}}

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        return {"label": {"type": str}, "amount": {"type": int}}


class AggregateManager:
    Interface = AggregateInterface

    def __init__(self, label: str, amount: int) -> None:
        self.label = label
        self.amount = amount


class CountingMaterializedBucket(list[AggregateManager]):
    _group_materialization_safe = True

    def __init__(
        self,
        values: list[AggregateManager],
        source_yields: Counter,
    ) -> None:
        super().__init__(values)
        self._source_yields = source_yields

    def __iter__(self) -> Generator[AggregateManager, None, None]:
        self._source_yields.increment()
        yield from list.__iter__(self)


def test_group_manager_reuses_snapshot_for_aggregates_and_identity(
    perf_budgets: PerfBudgets,
) -> None:
    source_yields = Counter()
    source = CountingMaterializedBucket(
        [AggregateManager(f"label-{index % 100}", index) for index in range(ROW_COUNT)],
        source_yields,
    )
    manager = GroupManager(AggregateManager, {}, source)

    labels = manager.label
    amount = manager.amount
    hash(manager)
    hash(manager)

    assert isinstance(labels, str)
    assert labels.startswith("label-0")
    assert amount == sum(range(ROW_COUNT))
    assert source_yields.value == 1
    perf_budgets.assert_observation(
        "GROUP_MANAGER_10000_SOURCE_YIELDS", source_yields.value
    )
