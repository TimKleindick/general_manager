from __future__ import annotations

import sys
import tracemalloc
from collections.abc import Callable, Iterable, Iterator, Mapping, Set as AbstractSet
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from types import CodeType, FrameType
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Counter:
    value: int = 0

    def increment(self, amount: int = 1) -> None:
        self.value += amount

    def reset(self) -> None:
        self.value = 0


class CountingIterable(Generic[T]):
    def __init__(self, values: Iterable[T], counter: Counter) -> None:
        self._values = values
        self.counter = counter

    def __iter__(self) -> Iterator[T]:
        for value in self._values:
            self.counter.increment()
            yield value


@contextmanager
def count_profiled_calls(
    target_code: CodeType,
    self_predicate: Callable[[object], bool],
) -> Iterator[Counter]:
    """Count matching Python calls without replacing the callable descriptor."""
    counter = Counter()
    previous_profiler = sys.getprofile()

    def profiler(frame: FrameType, event: str, arg: object) -> None:
        if (
            event == "call"
            and frame.f_code is target_code
            and "self" in frame.f_locals
            and self_predicate(frame.f_locals["self"])
        ):
            counter.increment()
        if previous_profiler is not None:
            previous_profiler(frame, event, arg)

    sys.setprofile(profiler)
    try:
        yield counter
    finally:
        sys.setprofile(previous_profiler)


@dataclass(frozen=True)
class DiagnosticObservation(Generic[T]):
    result: T
    elapsed_seconds: float
    peak_bytes: int


def capture_diagnostics(callback: Callable[[], T]) -> DiagnosticObservation[T]:
    owns_tracing = not tracemalloc.is_tracing()
    if owns_tracing:
        tracemalloc.start()
    started = perf_counter()
    try:
        result = callback()
        elapsed = perf_counter() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        if owns_tracing:
            tracemalloc.stop()
    return DiagnosticObservation(result, elapsed, peak_bytes)


class PerfBudgets:
    def __init__(self, ceilings: Mapping[str, object], *, record: bool = False) -> None:
        self._ceilings = dict(ceilings)
        self._record = record
        self.observations: dict[str, int] = {}

    def assert_observation(self, name: str, observed: int) -> None:
        assert name in self._ceilings, f"missing performance budget: {name}"
        ceiling = self._ceilings[name]
        assert type(ceiling) is int and ceiling >= 0, (
            f"invalid performance budget: {name}={ceiling!r}"
        )
        assert name not in self.observations, (
            f"duplicate performance observation: {name}"
        )
        self.observations[name] = observed
        if self._record:
            print(f"PERF_OBSERVATION {name}={observed}")
            return
        assert observed <= ceiling, (
            f"{name}: observed={observed} exceeded ceiling={ceiling}"
        )

    def validate_manifest(self, expected_names: AbstractSet[str]) -> None:
        actual_names = set(self._ceilings)
        missing = sorted(expected_names - actual_names)
        unused = sorted(actual_names - expected_names)
        invalid = sorted(
            name
            for name, value in self._ceilings.items()
            if type(value) is not int or value < 0
        )
        assert not (missing or unused or invalid), (
            f"missing={missing}; unused={unused}; invalid={invalid}"
        )
