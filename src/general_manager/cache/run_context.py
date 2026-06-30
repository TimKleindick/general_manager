"""Run-scoped cache context for calculation workloads."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Optional, TypeVar, cast

from general_manager.logging import get_logger

if TYPE_CHECKING:
    from general_manager.cache.dependency_cache import DependencyCacheHit
    from general_manager.cache.dependency_index import Dependency
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
ORM_BUCKET_ROW_RESULT_PREFIX = "orm_bucket_row_result"
BUCKET_INDEX_PREFIX = "bucket_index"
DEFAULT_DEPENDENCY_CACHE_PUBLISH_BATCH_SIZE = 1000
logger = get_logger("cache.run_context")


@dataclass(frozen=True)
class BucketIndexRunCacheEntry:
    """Run-cache payload for a bucket index plus dependencies to replay on hits."""

    value: object
    dependencies: frozenset["Dependency"]


class CalculationRunContext:
    """
    Cache calculation work for one request, graph, bulk operation, or task.

    Entering the context makes it available through
    `current_calculation_run_context()`. Clean exits flush buffered
    dependency-cache publications; exceptional exits discard them. In all cases
    the active context token is reset and run-local values, dependency hits, and
    pending publications are cleared before exit returns. Active context lookup
    uses Python `contextvars`, so visibility follows normal context-variable
    propagation for async tasks and does not automatically cross unrelated
    threads. Nested `CalculationRunContext` instances replace the active context
    for their nested block and restore the previous context on exit.
    """

    def __init__(
        self,
        *,
        dependency_cache_publish_batch_size: int = (
            DEFAULT_DEPENDENCY_CACHE_PUBLISH_BATCH_SIZE
        ),
    ) -> None:
        """
        Initialize empty run-local storage.

        Parameters:
            dependency_cache_publish_batch_size: Number of pending dependency
                cache publications that triggers an automatic flush. Values
                less than or equal to zero are accepted and make every buffered
                publication flush immediately.
        """
        self._values: dict[Hashable, object] = {}
        self._dependency_cache_hits: dict[str, DependencyCacheHit] = {}
        self._dependency_cache_pending_publications: dict[
            str,
            PendingDependencyCachePublication,
        ] = {}
        self._dependency_cache_publish_batch_size = dependency_cache_publish_batch_size
        self._tokens: list[Token[CalculationRunContext | None]] = []

    def __enter__(self) -> "CalculationRunContext":
        """Activate this context and return it for the `with` block."""
        self._tokens.append(_active_context.set(self))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """
        Flush or discard buffered dependency-cache state and deactivate the context.

        Re-entering the same context instance is supported: inner exits restore
        the previous active context without flushing or clearing state, and only
        the outermost exit performs publication cleanup and storage clearing.
        Calling ``__exit__`` without a matching ``__enter__`` is a no-op.

        Parameters:
            exc_type: Exception type from the wrapped block, or `None` on a
                clean exit.
            exc_val: Exception instance from the wrapped block, or `None`.
            exc_tb: Traceback from the wrapped block, or `None`.

        Raises:
            Exception: Errors from flushing/discarding dependency-cache
                publications, lease release, or context-variable reset propagate
                unchanged.
        """
        if not self._tokens:
            return

        token = self._tokens.pop()
        is_outermost_exit = not self._tokens
        try:
            if is_outermost_exit:
                if exc_type is None:
                    self.flush_dependency_cache_publications()
                else:
                    self.discard_dependency_cache_publications()
        finally:
            _active_context.reset(token)
            if is_outermost_exit:
                self._values.clear()
                self._dependency_cache_hits.clear()
                self._dependency_cache_pending_publications.clear()

    def get_or_set(self, key: Hashable, loader: Callable[[], T]) -> T:
        """
        Return a cached value for `key`, loading it at most once per context.

        The first call for a missing key invokes `loader()` and stores its
        return value. Later calls with the same key return the stored object,
        even if they pass a different loader. Loader exceptions propagate and do
        not store a value. The key may be any hashable value. `loader` is a
        synchronous callable; coroutine objects are stored as ordinary return
        values if a loader returns one.
        """
        if key not in self._values:
            self._values[key] = loader()
        return cast(T, self._values[key])

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
        """
        Merge prefetched dependency-cache hits into the active run.

        Provided keys replace existing hits for the same cache key, while
        omitted existing keys remain available.
        """
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
        """
        Buffer a dependency-cache miss for guarded batch publication.

        The buffered result is immediately visible through
        `get_dependency_cache_hit()` in the same run. A later entry for the same
        cache key replaces the previous one and releases the previous compute
        lease when the lease token differs. The buffered hit also replaces any
        prefetched hit for the same key. Reaching the configured batch size, or
        using a batch size less than or equal to zero, flushes pending
        publications immediately. `entry` is a
        `PendingDependencyCachePublication` created by the dependency-cache
        compute path; this context reads its `cache_key`, `result`,
        `dependencies`, and `lease` fields but does not derive those values.

        Raises:
            Exception: Errors from `release_compute_lease()`,
                `publish_dependency_cache_entries()`, or cache backend lease
                operations propagate unchanged except `CachePublishAborted`,
                which is handled by `flush_dependency_cache_publications()`.
        """
        from general_manager.cache.dependency_cache import DependencyCacheHit
        from general_manager.cache.dependency_publish import release_compute_lease

        previous_entry = self._dependency_cache_pending_publications.get(
            entry.cache_key
        )
        if previous_entry is not None and previous_entry.lease != entry.lease:
            release_compute_lease(previous_entry.lease)
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
        """
        Publish buffered dependency-cache misses and release their leases.

        `CachePublishAborted` is logged and swallowed because guarded publish
        aborts are expected when backend generations changed. Leases are
        released in a `finally` block for every entry submitted to the publish
        attempt.

        Raises:
            Exception: Unexpected publish errors or lease-release errors
                propagate unchanged.
        """
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
        """
        Drop buffered dependency-cache misses and release their leases.

        Raises:
            Exception: Lease-release errors propagate unchanged.
        """
        entries = tuple(self._dependency_cache_pending_publications.values())
        if not entries:
            return
        self._dependency_cache_pending_publications.clear()

        from general_manager.cache.dependency_publish import release_compute_lease

        for entry in entries:
            release_compute_lease(entry.lease)

    def discard_dependency_cache_state(self) -> None:
        """Drop prefetched hits and buffered publications for this run."""
        try:
            self.discard_dependency_cache_publications()
        finally:
            self._dependency_cache_hits.clear()

    def discard_prefix(self, prefix: tuple[Hashable, ...]) -> None:
        """Discard cached tuple keys whose leading items equal `prefix`."""
        for key in list(self._values):
            if isinstance(key, tuple) and key[: len(prefix)] == prefix:
                del self._values[key]

    def get_orm_bucket_result(self, key: Hashable) -> object:
        """Return a cached ORM bucket result for key, or `None` when absent."""
        return self.get((ORM_BUCKET_RESULT_PREFIX, key))

    def set_orm_bucket_result(self, key: Hashable, value: object) -> None:
        """Store or overwrite an ORM bucket result for the active run."""
        self.set((ORM_BUCKET_RESULT_PREFIX, key), value)

    def get_orm_bucket_rows(self, key: Hashable) -> object:
        """Return cached ORM bucket rows for key, or `None` when absent."""
        return self.get((ORM_BUCKET_ROW_RESULT_PREFIX, key))

    def set_orm_bucket_rows(self, key: Hashable, value: object) -> None:
        """Store or overwrite ORM bucket rows for the active run."""
        self.set((ORM_BUCKET_ROW_RESULT_PREFIX, key), value)

    def clear_orm_bucket_results(self) -> None:
        """Discard all run-scoped ORM bucket result entries."""
        self.discard_prefix((ORM_BUCKET_RESULT_PREFIX,))
        self.discard_prefix((ORM_BUCKET_ROW_RESULT_PREFIX,))

    def _bucket_index_cache_key(
        self,
        source_signature: Hashable,
        key_spec: Hashable,
        many: bool,
        max_rows: int | None,
    ) -> tuple[Hashable, ...]:
        """Return the full run-cache key for one bucket index variant."""
        return (BUCKET_INDEX_PREFIX, source_signature, key_spec, many, max_rows)

    def get_bucket_index_result(
        self,
        source_signature: Hashable,
        key_spec: Hashable,
        many: bool,
        max_rows: int | None,
    ) -> object:
        """
        Return a cached bucket index and replay its source dependencies.

        Missing entries and entries under the same key that are not
        `BucketIndexRunCacheEntry` return `None`. Cache hits replay the stored
        dependencies through `DependencyTracker.track()` before returning the
        cached value.
        """
        entry = self.get(
            self._bucket_index_cache_key(source_signature, key_spec, many, max_rows)
        )
        if not isinstance(entry, BucketIndexRunCacheEntry):
            return None

        from general_manager.cache.cache_tracker import DependencyTracker

        for class_name, operation, identifier in entry.dependencies:
            DependencyTracker.track(class_name, operation, identifier)
        return entry.value

    def set_bucket_index_result(
        self,
        source_signature: Hashable,
        key_spec: Hashable,
        many: bool,
        value: object,
        dependencies: Iterable["Dependency"],
        max_rows: int | None,
    ) -> None:
        """Store a bucket index and freeze the dependencies touched while building it."""
        self.set(
            self._bucket_index_cache_key(source_signature, key_spec, many, max_rows),
            BucketIndexRunCacheEntry(
                value=value,
                dependencies=frozenset(dependencies),
            ),
        )

    def clear_bucket_indexes(self) -> None:
        """Discard all run-scoped bucket index entries."""
        self.discard_prefix((BUCKET_INDEX_PREFIX,))

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
        """
        Load a working set once and index it by the supplied key function.

        The loader is evaluated only on the first call for `("index", key)`.
        Rows sharing an index key overwrite earlier rows, matching normal
        dictionary comprehension semantics in loader iteration order. Loader or
        index-key exceptions propagate and do not store a value. Both callables
        are synchronous; coroutine objects returned by them are treated as
        ordinary row or key values.
        """
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
        """
        Load a working set once and group rows by the supplied key function.

        The loader is evaluated only on the first call for `("group_by", key)`.
        Group and row order follows the loader's iteration order. Loader or
        grouping-key exceptions propagate and do not store a value. Both
        callables are synchronous; coroutine objects returned by them are
        treated as ordinary row or key values.
        """

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
        """Alias `group_by()` for callers that think in multi-value indexes."""
        return self.group_by(key=key, loader=loader, group_by=index_by)


def current_calculation_run_context() -> CalculationRunContext | None:
    """Return the context active in the current context-variable scope, if any."""
    return _active_context.get()


class ensure_calculation_run_context:
    """
    Use the current run context or create one for the wrapped block.

    If a `CalculationRunContext` is already active, the manager returns it and
    leaves lifecycle ownership to the outer context. Otherwise it creates,
    enters, and later exits a temporary context.
    """

    def __init__(self) -> None:
        self._owned_context: Optional[CalculationRunContext] = None

    def __enter__(self) -> CalculationRunContext:
        """Return the active context, creating one when needed."""
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
        """Exit the owned temporary context, if one was created."""
        if self._owned_context is not None:
            self._owned_context.__exit__(exc_type, exc_val, exc_tb)
