from __future__ import annotations

from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from typing import Any, cast

import pytest

from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.group_bucket import GroupBucket
from general_manager.manager.group_manager import GroupManager
from tests.perf.support import Counter, PerfBudgets, capture_diagnostics

pytestmark = pytest.mark.perf

ROW_COUNT = 10_000
PERF_NAMES = {
    10: (
        "GROUP_10_YIELDS",
        "GROUP_10_CONSTRUCTORS",
        "GROUP_10_FILTER_CALLS",
    ),
    1_000: (
        "GROUP_1000_YIELDS",
        "GROUP_1000_CONSTRUCTORS",
        "GROUP_1000_FILTER_CALLS",
    ),
    10_000: (
        "GROUP_10000_YIELDS",
        "GROUP_10000_CONSTRUCTORS",
        "GROUP_10000_FILTER_CALLS",
    ),
}


class GroupPerfInterfaceDouble:
    @classmethod
    def get_attributes(cls) -> dict[str, object]:
        return {"group_key": None}

    @classmethod
    def get_attribute_types(cls) -> dict[str, dict[str, object]]:
        return {"group_key": {"type": int}}


class GroupPerfManager:
    Interface = GroupPerfInterfaceDouble

    def __init__(self, row_index: int, group_key: int) -> None:
        self.row_index = row_index
        self.group_key = group_key


@dataclass
class GroupCounters:
    source_yields: Counter
    constructors: Counter
    filter_calls: Counter

    @classmethod
    def create(cls) -> GroupCounters:
        return cls(Counter(), Counter(), Counter())

    def reset(self) -> None:
        self.source_yields.reset()
        self.constructors.reset()
        self.filter_calls.reset()

    def snapshot(self) -> tuple[int, int, int]:
        return (
            self.source_yields.value,
            self.constructors.value,
            self.filter_calls.value,
        )


class CountingGroupBucket(Bucket[Any]):
    def __init__(
        self,
        values: Sequence[GroupPerfManager],
        source_yields: Counter,
        filter_calls: Counter,
        *,
        count_source_yields: bool,
        group_index: dict[int, tuple[GroupPerfManager, ...]] | None = None,
    ) -> None:
        super().__init__(cast(Any, GroupPerfManager))
        self._values = tuple(values)
        self._source_yields = source_yields
        self._filter_calls = filter_calls
        self._count_source_yields = count_source_yields
        self._group_index = (
            self._build_group_index(self._values)
            if group_index is None
            else group_index
        )

    @staticmethod
    def _build_group_index(
        values: Sequence[GroupPerfManager],
    ) -> dict[int, tuple[GroupPerfManager, ...]]:
        mutable_index: dict[int, list[GroupPerfManager]] = {}
        for value in values:
            mutable_index.setdefault(value.group_key, []).append(value)
        return {key: tuple(group) for key, group in mutable_index.items()}

    def _derive(
        self,
        values: Sequence[GroupPerfManager],
    ) -> CountingGroupBucket:
        return CountingGroupBucket(
            values,
            self._source_yields,
            self._filter_calls,
            count_source_yields=False,
            group_index=self._group_index,
        )

    def __or__(self, other: Bucket[Any] | Any) -> CountingGroupBucket:
        if isinstance(other, CountingGroupBucket):
            return self._derive((*self._values, *other._values))
        if isinstance(other, GroupPerfManager):
            return self._derive((*self._values, other))
        raise TypeError

    def __iter__(self) -> Generator[GroupPerfManager, None, None]:
        for value in self._values:
            if self._count_source_yields:
                self._source_yields.increment()
            yield value

    def filter(self, **kwargs: object) -> CountingGroupBucket:
        self._filter_calls.increment()
        if self._count_source_yields and tuple(kwargs) == ("group_key",):
            group_key = kwargs["group_key"]
            matching = (
                self._group_index.get(group_key, ())
                if isinstance(group_key, int)
                else ()
            )
            return self._derive(matching)
        return self._derive(
            tuple(
                value
                for value in self._values
                if all(
                    getattr(value, key) == expected for key, expected in kwargs.items()
                )
            )
        )

    def exclude(self, **kwargs: object) -> CountingGroupBucket:
        if not kwargs:
            return self._derive(self._values)
        return self._derive(
            tuple(
                value
                for value in self._values
                if not all(
                    getattr(value, key) == expected for key, expected in kwargs.items()
                )
            )
        )

    def first(self) -> GroupPerfManager | None:
        return self._values[0] if self._values else None

    def last(self) -> GroupPerfManager | None:
        return self._values[-1] if self._values else None

    def count(self) -> int:
        return len(self._values)

    def all(self) -> CountingGroupBucket:
        return self

    def get(self, **kwargs: object) -> GroupPerfManager:
        matching = self.filter(**kwargs)
        if matching.count() != 1:
            raise LookupError
        return matching._values[0]

    def __getitem__(self, item: int | slice) -> GroupPerfManager | CountingGroupBucket:
        if isinstance(item, slice):
            return self._derive(self._values[item])
        return self._values[item]

    def __len__(self) -> int:
        return len(self._values)

    def __contains__(self, item: Any) -> bool:
        return item in self._values

    def sort(
        self,
        key: tuple[str] | str,
        reverse: bool = False,
    ) -> CountingGroupBucket:
        keys = (key,) if isinstance(key, str) else key
        return self._derive(
            sorted(
                self._values,
                key=lambda value: tuple(getattr(value, name) for name in keys),
                reverse=reverse,
            )
        )


@pytest.fixture(scope="module")
def source_managers() -> list[GroupPerfManager]:
    return [
        GroupPerfManager(row_index=index, group_key=0) for index in range(ROW_COUNT)
    ]


@pytest.mark.parametrize("expected_groups", [10, 1_000, 10_000])
def test_group_bucket_construction_work(
    expected_groups: int,
    source_managers: list[GroupPerfManager],
    monkeypatch: pytest.MonkeyPatch,
    perf_budgets: PerfBudgets,
    pytestconfig: pytest.Config,
) -> None:
    for manager in source_managers:
        manager.group_key = (
            manager.row_index
            if expected_groups == ROW_COUNT
            else manager.row_index % expected_groups
        )

    counters = GroupCounters.create()
    source_bucket = CountingGroupBucket(
        source_managers,
        counters.source_yields,
        counters.filter_calls,
        count_source_yields=True,
    )
    original_group_manager_init = cast(
        Callable[..., None], GroupManager.__dict__["__init__"]
    )

    def counted_group_manager_init(
        self: GroupManager[Any], *args: object, **kwargs: object
    ) -> None:
        counters.constructors.increment()
        original_group_manager_init(self, *args, **kwargs)

    monkeypatch.setattr(GroupManager, "__init__", counted_group_manager_init)
    manager_class = cast(Any, GroupPerfManager)

    def construct_group_bucket() -> GroupBucket[Any]:
        return GroupBucket(manager_class, ("group_key",), source_bucket)

    counters.reset()
    if expected_groups == ROW_COUNT and pytestconfig.getoption("verbose") >= 2:
        diagnostic = capture_diagnostics(construct_group_bucket)
        bucket = diagnostic.result
        observations = counters.snapshot()
        print(
            "GROUP_10000_DIAGNOSTIC "
            f"elapsed={diagnostic.elapsed_seconds:.6f}s "
            f"peak={diagnostic.peak_bytes}B"
        )
    else:
        bucket = construct_group_bucket()
        observations = counters.snapshot()

    assert bucket.count() == expected_groups
    assert sum(group._data.count() for group in bucket) == ROW_COUNT
    first = bucket.first()
    last = bucket.last()
    assert first is not None
    assert last is not None
    assert first.group_key == 0
    assert last.group_key == expected_groups - 1
    assert source_managers[5_000] in bucket
    rows_per_group = ROW_COUNT // expected_groups
    assert all(group._data.count() == rows_per_group for group in bucket)
    assert counters.snapshot() == observations

    for name, observed in zip(PERF_NAMES[expected_groups], observations, strict=True):
        perf_budgets.assert_observation(name, observed)


def test_default_verbosity_skips_group_diagnostics(
    source_managers: list[GroupPerfManager],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DefaultVerbosityConfig:
        @staticmethod
        def getoption(name: str) -> int:
            assert name == "verbose"
            return 0

    def fail_capture_diagnostics(callback: Callable[[], object]) -> Any:
        pytest.fail("capture_diagnostics ran at default verbosity")

    monkeypatch.setattr(
        "tests.perf.test_group_bucket_perf.capture_diagnostics",
        fail_capture_diagnostics,
    )
    names = PERF_NAMES[ROW_COUNT]
    budgets = PerfBudgets(
        dict(zip(names, (ROW_COUNT, ROW_COUNT, ROW_COUNT), strict=True))
    )

    test_group_bucket_construction_work(
        ROW_COUNT,
        source_managers,
        monkeypatch,
        budgets,
        cast(pytest.Config, DefaultVerbosityConfig()),
    )
