"""Run-scoped cache context for calculation workloads."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Optional, TypeVar

K = TypeVar("K", bound=Hashable)
T = TypeVar("T")

_active_context: ContextVar["CalculationRunContext | None"] = ContextVar(
    "general_manager_calculation_run_context",
    default=None,
)


class CalculationRunContext:
    """Cache calculation work for one request, graph, bulk operation, or task."""

    def __init__(self) -> None:
        self._values: dict[Hashable, object] = {}
        self._token: Token[CalculationRunContext | None] | None = None

    def __enter__(self) -> "CalculationRunContext":
        self._token = _active_context.set(self)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._token is not None:
            _active_context.reset(self._token)
            self._token = None
        self._values.clear()

    def get_or_set(self, key: Hashable, loader: Callable[[], T]) -> T:
        """Return a cached value for key, loading it once per active context."""
        if key not in self._values:
            self._values[key] = loader()
        return self._values[key]  # type: ignore[return-value]

    def get(self, key: Hashable, default: object = None) -> object:
        """Return the stored value for key, or default when key is absent."""
        return self._values.get(key, default)

    def set(self, key: Hashable, value: object) -> None:
        """Store a value for the active run."""
        self._values[key] = value

    def has(self, key: Hashable) -> bool:
        """Return whether key has a value in the active run."""
        return key in self._values

    def __contains__(self, key: Hashable) -> bool:
        """Return whether key has a value in the active run."""
        return self.has(key)

    def index(
        self,
        *,
        key: Hashable,
        loader: Callable[[], Iterable[T]],
        index_by: Callable[[T], K],
    ) -> dict[K, T]:
        """Load a working set once and index it by the supplied key function."""
        return self.get_or_set(
            ("index", key),
            lambda: {index_by(row): row for row in loader()},
        )

    def group_by(
        self,
        *,
        key: Hashable,
        loader: Callable[[], Iterable[T]],
        group_by: Callable[[T], K],
    ) -> dict[K, list[T]]:
        """Load a working set once and group it by the supplied key function."""

        def load_groups() -> dict[K, list[T]]:
            grouped: defaultdict[K, list[T]] = defaultdict(list)
            for row in loader():
                grouped[group_by(row)].append(row)
            return dict(grouped)

        return self.get_or_set(("group_by", key), load_groups)

    def index_many(
        self,
        *,
        key: Hashable,
        loader: Callable[[], Iterable[T]],
        index_by: Callable[[T], K],
    ) -> dict[K, list[T]]:
        """Load a working set once and group rows sharing the same index key."""
        return self.group_by(key=key, loader=loader, group_by=index_by)


def current_calculation_run_context() -> CalculationRunContext | None:
    """Return the active calculation run context, if any."""
    return _active_context.get()


class ensure_calculation_run_context:
    """Use the current run context or create one for the wrapped block."""

    def __init__(self) -> None:
        self._owned_context: Optional[CalculationRunContext] = None

    def __enter__(self) -> CalculationRunContext:
        current = current_calculation_run_context()
        if current is not None:
            self._owned_context = None
            return current
        self._owned_context = CalculationRunContext()
        return self._owned_context.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._owned_context is not None:
            self._owned_context.__exit__(exc_type, exc_val, exc_tb)
