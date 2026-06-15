"""Run-scoped cache context for calculation workloads."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Mapping
from contextvars import ContextVar, Token
from types import TracebackType
from typing import TYPE_CHECKING, Optional, TypeVar

from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.cache.dependency_cache import DependencyCacheHit
    from general_manager.cache.dependency_publish import (
        PendingDependencyCachePublication,
    )

K = TypeVar("K", bound=Hashable)
T = TypeVar("T")

_active_context: ContextVar["CalculationRunContext | None"] = ContextVar(
    "general_manager_calculation_run_context",
    default=None,
)
ORM_BUCKET_RESULT_PREFIX = "orm_bucket_result"
DEFAULT_DEPENDENCY_CACHE_PUBLISH_BATCH_SIZE = 1000
logger = get_logger("cache.run_context")


class CalculationRunContext:
    """Cache calculation work for one request, graph, bulk operation, or task."""

    def __init__(
        self,
        *,
        dependency_cache_publish_batch_size: int = (
            DEFAULT_DEPENDENCY_CACHE_PUBLISH_BATCH_SIZE
        ),
    ) -> None:
        self._values: dict[Hashable, object] = {}
        self._dependency_cache_hits: dict[str, DependencyCacheHit] = {}
        self._dependency_cache_pending_publications: dict[
            str,
            PendingDependencyCachePublication,
        ] = {}
        self._dependency_cache_publish_batch_size = dependency_cache_publish_batch_size
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
        try:
            if exc_type is None:
                self.flush_dependency_cache_publications()
            else:
                self.discard_dependency_cache_publications()
        finally:
            if self._token is not None:
                _active_context.reset(self._token)
                self._token = None
            self._values.clear()
            self._dependency_cache_hits.clear()
            self._dependency_cache_pending_publications.clear()

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

    def set_dependency_cache_hits(
        self,
        hits: Mapping[str, DependencyCacheHit],
    ) -> None:
        """Store dependency-cache hits prefetched for the active run."""
        self._dependency_cache_hits.update(hits)

    def get_dependency_cache_hit(
        self,
        key: str,
        default: object = None,
    ) -> DependencyCacheHit | object:
        """Return a prefetched dependency-cache hit, or default when absent."""
        return self._dependency_cache_hits.get(key, default)

    def buffer_dependency_cache_publication(
        self,
        entry: PendingDependencyCachePublication,
    ) -> None:
        """Buffer a dependency-cache miss for guarded batch publication."""
        from general_manager.cache.dependency_cache import DependencyCacheHit

        self._dependency_cache_pending_publications[entry.cache_key] = entry
        self._dependency_cache_hits[entry.cache_key] = DependencyCacheHit(
            value=entry.result,
            dependencies=entry.dependencies,
        )
        if (
            len(self._dependency_cache_pending_publications)
            >= self._dependency_cache_publish_batch_size
        ):
            self.flush_dependency_cache_publications()

    def flush_dependency_cache_publications(self) -> None:
        """Publish buffered dependency-cache misses and release their leases."""
        entries = tuple(self._dependency_cache_pending_publications.values())
        if not entries:
            return
        self._dependency_cache_pending_publications.clear()

        from general_manager.cache.dependency_publish import (
            CachePublishAborted,
            publish_dependency_cache_entries,
            release_compute_lease,
        )

        try:
            publish_dependency_cache_entries(entries)
        except CachePublishAborted:
            logger.debug(
                "dependency cache batch publish aborted",
                context={"entry_count": len(entries)},
            )
        finally:
            for entry in entries:
                release_compute_lease(entry.lease)

    def discard_dependency_cache_publications(self) -> None:
        """Drop buffered dependency-cache misses and release their leases."""
        entries = tuple(self._dependency_cache_pending_publications.values())
        if not entries:
            return
        self._dependency_cache_pending_publications.clear()

        from general_manager.cache.dependency_publish import release_compute_lease

        for entry in entries:
            release_compute_lease(entry.lease)

    def discard_dependency_cache_state(self) -> None:
        """Drop dependency-cache hits and buffered publications for this run."""
        try:
            self.discard_dependency_cache_publications()
        finally:
            self._dependency_cache_hits.clear()

    def discard_prefix(self, prefix: tuple[Hashable, ...]) -> None:
        """Discard tuple keys that start with the supplied prefix."""
        for key in list(self._values):
            if isinstance(key, tuple) and key[: len(prefix)] == prefix:
                del self._values[key]

    def get_orm_bucket_result(self, key: Hashable) -> object:
        """Return a cached ORM bucket result for key, or None when absent."""
        return self.get((ORM_BUCKET_RESULT_PREFIX, key))

    def set_orm_bucket_result(self, key: Hashable, value: object) -> None:
        """Store an ORM bucket result for the active run."""
        self.set((ORM_BUCKET_RESULT_PREFIX, key), value)

    def clear_orm_bucket_results(self) -> None:
        """Discard all run-scoped ORM bucket result entries."""
        self.discard_prefix((ORM_BUCKET_RESULT_PREFIX,))

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
