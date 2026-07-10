from __future__ import annotations

import tracemalloc
from collections.abc import Callable, Iterable, Iterator, Mapping, Set as AbstractSet
from dataclasses import dataclass
from time import perf_counter
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


@dataclass(frozen=True)
class DiagnosticObservation(Generic[T]):
    result: T
    elapsed_seconds: float
    peak_bytes: int


def capture_diagnostics(callback: Callable[[], T]) -> DiagnosticObservation[T]:
    tracemalloc.start()
    started = perf_counter()
    try:
        result = callback()
        elapsed = perf_counter() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return DiagnosticObservation(result, elapsed, peak_bytes)


class PerfBudgets:
    def __init__(self, ceilings: Mapping[str, object], *, record: bool = False) -> None:
        self._ceilings = dict(ceilings)
        self._record = record
        self.observations: dict[str, int] = {}

    def assert_observation(self, name: str, observed: int) -> None:
        assert name in self._ceilings, f"missing performance budget: {name}"
        self.observations[name] = observed
        if self._record:
            print(f"PERF_OBSERVATION {name}={observed}")
            return
        ceiling = self._ceilings[name]
        assert type(ceiling) is int and ceiling >= 0, (
            f"invalid performance budget: {name}={ceiling!r}"
        )
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
