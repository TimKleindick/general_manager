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
ORM_BUCKET_MANAGER_RESULT_PREFIX = "orm_bucket_manager_result"
ORM_BUCKET_FIRST_ROW_PREFIX = "orm_bucket_first_row"
ORM_BUCKET_COUNT_PREFIX = "orm_bucket_count"
ORM_BUCKET_LAST_ROW_PREFIX = "orm_bucket_last_row"
ORM_BUCKET_GET_PREFIX = "orm_bucket_get"
ORM_BUCKET_INDEX_PREFIX = "orm_bucket_index"
ORM_BUCKET_MEMBERSHIP_PREFIX = "orm_bucket_membership"
ORM_MODEL_ROW_INDEX_PREFIX = "orm_model_row_index"
ORM_MODEL_RELATION_PREFETCH_PREFIX = "orm_model_relation_prefetch"
ORM_RELATION_MANAGER_PREFIX = "orm_relation_manager"
ORM_QUERY_BUCKET_PREFIX = "orm_query_bucket"
ORM_BUCKET_EXISTS_PREFIX = "orm_bucket_exists"
CALCULATION_BUCKET_RESULT_PREFIX = "calculation_bucket_result"
BUCKET_INDEX_PREFIX = "bucket_index"
TRUSTED_ORM_MANAGER_PREFIX = "trusted_orm_manager"
DEFAULT_DEPENDENCY_CACHE_PUBLISH_BATCH_SIZE = 1000
logger = get_logger("cache.run_context")
OrmModelRowKey = tuple[Hashable, Hashable | None]
CALCULATION_BUCKET_RESULT_MISSING = object()


@dataclass(frozen=True)
class BucketIndexRunCacheEntry:
    """Run-cache payload for a bucket index plus dependencies to replay on hits."""

    value: object
    dependencies: frozenset["Dependency"]


@dataclass(frozen=True)
class OrmBucketManagersRunCacheEntry:
    """Run-cache payload for cached ORM managers plus dependencies to replay."""

    value: object
    dependencies: frozenset["Dependency"]


@dataclass(frozen=True)
class CalculationBucketResultRunCacheEntry:
    """Immutable run-cache payload for one calculation bucket result."""

    snapshots: tuple[object, ...]
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
        try:
            return cast(T, self._values[key])
        except KeyError:
            value = loader()
            self._values[key] = value
            return value

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
        self._index_orm_model_rows(value)

    @staticmethod
    def _orm_model_row_key(row: object) -> OrmModelRowKey | None:
        """Return a stable per-run key for a Django ORM row, if possible."""
        pk = getattr(row, "pk", None)
        try:
            hash(pk)
        except TypeError:
            return None
        state = getattr(row, "_state", None)
        db = getattr(state, "db", None)
        try:
            hash(db)
        except TypeError:
            return None
        return (cast(Hashable, pk), cast(Hashable | None, db))

    @staticmethod
    def _orm_model_index_classes(row: object) -> tuple[type[object], ...]:
        """Return model classes under which an ORM row should be indexed."""
        meta = getattr(row, "_meta", None)
        if meta is None:
            return ()
        row_class = row.__class__
        concrete_model = getattr(meta, "concrete_model", None)
        if (
            isinstance(concrete_model, type)
            and issubclass(row_class, concrete_model)
            and concrete_model is not row_class
        ):
            return (row_class, concrete_model)
        return (row_class,)

    def _index_orm_model_rows(self, rows: object) -> None:
        """Index cached ORM rows by model/database/primary key for relation reuse."""
        if not isinstance(rows, tuple):
            return
        for row in rows:
            row_key = self._orm_model_row_key(row)
            if row_key is None:
                continue
            for model_class in self._orm_model_index_classes(row):
                cache_key = (ORM_MODEL_ROW_INDEX_PREFIX, model_class)
                index = self.get(cache_key)
                if not isinstance(index, dict):
                    index = {}
                    self.set(cache_key, index)
                index[row_key] = row

    def get_orm_model_row(
        self,
        model: type[object],
        primary_key: Hashable,
        database_alias: Hashable | None,
        default: object = None,
    ) -> object:
        """Return an indexed ORM row by model/database/primary key, if present."""
        index = self.get((ORM_MODEL_ROW_INDEX_PREFIX, model))
        if not isinstance(index, dict):
            return default
        return index.get((primary_key, database_alias), default)

    def get_orm_model_row_items(
        self,
        model: type[object],
    ) -> tuple[tuple[OrmModelRowKey, object], ...]:
        """Return indexed ORM row items for `model` in insertion order."""
        index = self.get((ORM_MODEL_ROW_INDEX_PREFIX, model))
        if not isinstance(index, dict):
            return ()
        return tuple(cast("dict[OrmModelRowKey, object]", index).items())

    def get_orm_model_relation_prefetched_keys(
        self,
        model: type[object],
        database_alias: Hashable | None,
        accessor_name: str,
    ) -> frozenset[OrmModelRowKey]:
        """Return row keys already prefetched for one model relation."""
        prefetched = self.get(
            (
                ORM_MODEL_RELATION_PREFETCH_PREFIX,
                model,
                database_alias,
                accessor_name,
            )
        )
        if isinstance(prefetched, frozenset):
            return cast(frozenset[OrmModelRowKey], prefetched)
        return frozenset()

    def add_orm_model_relation_prefetched_keys(
        self,
        model: type[object],
        database_alias: Hashable | None,
        accessor_name: str,
        row_keys: Iterable[OrmModelRowKey],
    ) -> None:
        """Record row keys whose relation has been prefetched in this run."""
        cache_key = (
            ORM_MODEL_RELATION_PREFETCH_PREFIX,
            model,
            database_alias,
            accessor_name,
        )
        prefetched = self.get_orm_model_relation_prefetched_keys(
            model,
            database_alias,
            accessor_name,
        )
        self.set(cache_key, prefetched | frozenset(row_keys))

    def get_orm_relation_manager(self, key: Hashable) -> object:
        """Return a cached relation manager for key, or `None` when absent."""
        return self.get((ORM_RELATION_MANAGER_PREFIX, key))

    def set_orm_relation_manager(self, key: Hashable, value: object) -> None:
        """Store a manager created by a generated ORM relation accessor."""
        self.set((ORM_RELATION_MANAGER_PREFIX, key), value)

    def get_orm_bucket_managers(self, key: Hashable) -> object:
        """Return cached ORM bucket managers for key, or `None` when absent."""
        entry = self.get((ORM_BUCKET_MANAGER_RESULT_PREFIX, key))
        if isinstance(entry, OrmBucketManagersRunCacheEntry):
            return entry.value
        return entry

    def get_orm_bucket_manager_dependencies(
        self,
        key: Hashable,
    ) -> frozenset["Dependency"] | None:
        """Return cached ORM manager dependencies for key, when available."""
        entry = self.get((ORM_BUCKET_MANAGER_RESULT_PREFIX, key))
        if isinstance(entry, OrmBucketManagersRunCacheEntry):
            return entry.dependencies
        return None

    def set_orm_bucket_managers(
        self,
        key: Hashable,
        value: object,
        dependencies: Iterable["Dependency"] | None = None,
    ) -> None:
        """Store or overwrite ORM bucket managers for the active run."""
        if dependencies is None:
            self.set((ORM_BUCKET_MANAGER_RESULT_PREFIX, key), value)
            return
        self.set(
            (ORM_BUCKET_MANAGER_RESULT_PREFIX, key),
            OrmBucketManagersRunCacheEntry(
                value=value,
                dependencies=frozenset(dependencies),
            ),
        )

    def get_orm_bucket_first_row(
        self,
        key: Hashable,
        default: object = None,
    ) -> object:
        """Return a cached ORM first-row result, or default when absent."""
        return self.get((ORM_BUCKET_FIRST_ROW_PREFIX, key), default)

    def set_orm_bucket_first_row(self, key: Hashable, value: object) -> None:
        """Store or overwrite an ORM first-row result for the active run."""
        self.set((ORM_BUCKET_FIRST_ROW_PREFIX, key), value)

    def get_orm_bucket_count(self, key: Hashable) -> object:
        """Return a cached ORM bucket count, or ``None`` when absent."""
        return self.get((ORM_BUCKET_COUNT_PREFIX, key))

    def set_orm_bucket_count(self, key: Hashable, value: int) -> None:
        """Store a scalar ORM bucket count for the active run."""
        self.set((ORM_BUCKET_COUNT_PREFIX, key), value)

    def get_orm_bucket_last_row(self, key: Hashable, default: object = None) -> object:
        """Return a cached ORM bucket last-row result, or ``default``."""
        return self.get((ORM_BUCKET_LAST_ROW_PREFIX, key), default)

    def set_orm_bucket_last_row(self, key: Hashable, value: object) -> None:
        """Store an ORM bucket last-row result for the active run."""
        self.set((ORM_BUCKET_LAST_ROW_PREFIX, key), value)

    def get_orm_bucket_get(self, key: Hashable, default: object = None) -> object:
        """Return a cached safe ORM bucket ``get`` result, or ``default``."""
        return self.get((ORM_BUCKET_GET_PREFIX, key), default)

    def set_orm_bucket_get(self, key: Hashable, value: object) -> None:
        """Store a safe ORM bucket ``get`` result for the active run."""
        self.set((ORM_BUCKET_GET_PREFIX, key), value)

    def get_orm_bucket_index(self, key: Hashable, default: object = None) -> object:
        """Return a cached ORM bucket scalar-index result, or ``default``."""
        return self.get((ORM_BUCKET_INDEX_PREFIX, key), default)

    def set_orm_bucket_index(self, key: Hashable, value: object) -> None:
        """Store an ORM bucket scalar-index result for the active run."""
        self.set((ORM_BUCKET_INDEX_PREFIX, key), value)

    def get_orm_bucket_membership(
        self,
        key: Hashable,
        default: object = None,
    ) -> object:
        """Return a cached ORM bucket membership result, or ``default``."""
        return self.get((ORM_BUCKET_MEMBERSHIP_PREFIX, key), default)

    def set_orm_bucket_membership(self, key: Hashable, value: bool) -> None:
        """Store an ORM bucket primary-key membership result for the active run."""
        self.set((ORM_BUCKET_MEMBERSHIP_PREFIX, key), value)

    def get_orm_query_bucket(self, key: Hashable) -> object:
        """Return a cached constructed ORM query bucket, or `None` when absent."""
        return self.get((ORM_QUERY_BUCKET_PREFIX, key))

    def set_orm_query_bucket(self, key: Hashable, value: object) -> None:
        """Store a constructed ORM query bucket for the active run."""
        self.set((ORM_QUERY_BUCKET_PREFIX, key), value)

    def get_orm_bucket_exists(self, key: Hashable) -> object:
        """Return cached ORM bucket existence for key, or `None` when absent."""
        return self.get((ORM_BUCKET_EXISTS_PREFIX, key))

    def set_orm_bucket_exists(self, key: Hashable, value: bool) -> None:
        """Store an ORM bucket existence result for the active run."""
        self.set((ORM_BUCKET_EXISTS_PREFIX, key), value)

    def clear_orm_bucket_results(self) -> None:
        """Discard all run-scoped ORM bucket result entries."""
        self.discard_prefix((ORM_BUCKET_RESULT_PREFIX,))
        self.discard_prefix((ORM_BUCKET_ROW_RESULT_PREFIX,))
        self.discard_prefix((ORM_BUCKET_MANAGER_RESULT_PREFIX,))
        self.discard_prefix((ORM_BUCKET_FIRST_ROW_PREFIX,))
        self.discard_prefix((ORM_BUCKET_COUNT_PREFIX,))
        self.discard_prefix((ORM_BUCKET_LAST_ROW_PREFIX,))
        self.discard_prefix((ORM_BUCKET_GET_PREFIX,))
        self.discard_prefix((ORM_BUCKET_INDEX_PREFIX,))
        self.discard_prefix((ORM_BUCKET_MEMBERSHIP_PREFIX,))
        self.discard_prefix((ORM_MODEL_ROW_INDEX_PREFIX,))
        self.discard_prefix((ORM_MODEL_RELATION_PREFETCH_PREFIX,))
        self.discard_prefix((ORM_RELATION_MANAGER_PREFIX,))
        self.discard_prefix((ORM_QUERY_BUCKET_PREFIX,))
        self.discard_prefix((ORM_BUCKET_EXISTS_PREFIX,))

    def get_calculation_bucket_result(
        self,
        signature: Hashable,
    ) -> CalculationBucketResultRunCacheEntry | object:
        """Return a calculation result entry and replay its dependencies.

        ``CALCULATION_BUCKET_RESULT_MISSING`` is returned for an absent entry;
        an entry with an empty ``snapshots`` tuple is therefore an ordinary hit.
        """
        entry = self.get(
            (CALCULATION_BUCKET_RESULT_PREFIX, signature),
            CALCULATION_BUCKET_RESULT_MISSING,
        )
        if not isinstance(entry, CalculationBucketResultRunCacheEntry):
            return CALCULATION_BUCKET_RESULT_MISSING

        from general_manager.cache.cache_tracker import DependencyTracker

        DependencyTracker._track_many_validated(entry.dependencies)
        return entry

    def set_calculation_bucket_result(
        self,
        signature: Hashable,
        snapshots: Iterable[object],
        dependencies: Iterable["Dependency"],
    ) -> None:
        """Store an immutable result snapshot and captured dependencies."""
        self.set(
            (CALCULATION_BUCKET_RESULT_PREFIX, signature),
            CalculationBucketResultRunCacheEntry(
                snapshots=tuple(snapshots),
                dependencies=frozenset(dependencies),
            ),
        )

    def clear_calculation_bucket_results(self) -> None:
        """Discard all run-scoped calculation bucket result entries."""
        self.discard_prefix((CALCULATION_BUCKET_RESULT_PREFIX,))

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
            DependencyTracker._track_validated(class_name, operation, identifier)
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

    def clear_trusted_orm_managers(self) -> None:
        """Discard run-scoped manager wrappers built from trusted ORM rows."""
        self.discard_prefix((TRUSTED_ORM_MANAGER_PREFIX,))

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
