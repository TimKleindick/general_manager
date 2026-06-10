"""Run-scoped cache context for calculation workloads."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import TypeVar

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


def current_calculation_run_context() -> CalculationRunContext | None:
    """Return the active calculation run context, if any."""
    return _active_context.get()


class ensure_calculation_run_context:
    """Use the current run context or create one for the wrapped block."""

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
