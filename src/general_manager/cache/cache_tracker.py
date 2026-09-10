"""Context manager utilities for tracking cache dependencies per thread."""

from __future__ import annotations

from collections.abc import Iterable
import threading
from types import TracebackType

from general_manager.cache.dependency_index import (
    Dependency,
    filter_type,
    general_manager_name,
)
from general_manager.cache._dependency_graph import (
    DependencyCapture,
    DependencySnapshot,
    materialize_dependencies,
    snapshot_from_dependencies,
)

_SUPPORTED_OPERATIONS: frozenset[filter_type] = frozenset(
    {"filter", "exclude", "identification", "request_query", "all"}
)


class _InvalidDependencyTrackerValueError(TypeError):
    """Internal error for malformed dependency tracker tuple values."""

    def __init__(self) -> None:
        """Build the malformed dependency tuple value error."""
        super().__init__("DependencyTracker values must be strings.")


class _InvalidDependencyTrackerOperationError(ValueError):
    """Internal error for unsupported dependency tracker operations."""

    def __init__(self, operation: object) -> None:
        """Build the unsupported operation error."""
        super().__init__(f"Unsupported dependency tracker operation: {operation!r}")


class _TrackedDependencySet(set[Dependency]):
    """Dependency set returned by DependencyTracker for framework-captured deps."""


class _CapturedDependencySet(_TrackedDependencySet):
    """Private materialization of a trusted immutable dependency graph."""


class _DependencyStorage(threading.local):
    """Thread-local dependency tracking stack."""

    def __init__(self) -> None:
        """Initialize an inactive dependency stack for one thread."""
        self.dependencies: list[_TrackedDependencySet] = []
        self.captures: list[DependencyCapture] = []
        self.depth = -1
        self.generation = 0
        self.stack_version = 0
        self.last_dependency: Dependency | None = None
        self.last_dependency_stack_version = -1
        self.seen_dependencies: set[Dependency] = set()
        self.seen_dependencies_stack_version = -1

    def reset(self) -> None:
        """Clear all active dependency scopes for the current thread."""
        # Capture scopes retain direct references to their old parent builders.
        # Detaching this stack therefore prevents later ordinary reads from
        # reaching it while still allowing each scope to finalize its own result.
        self.dependencies.clear()
        self.captures.clear()
        self.depth = -1
        self.generation += 1
        self.stack_version += 1
        self.last_dependency = None
        self.last_dependency_stack_version = -1
        self.seen_dependencies.clear()
        self.seen_dependencies_stack_version = -1


_dependency_storage = _DependencyStorage()


def _clear_duplicate_state(storage: _DependencyStorage) -> None:
    """Release duplicate-suppression references after the final scope exits."""
    storage.last_dependency = None
    storage.last_dependency_stack_version = -1
    storage.seen_dependencies.clear()
    storage.seen_dependencies_stack_version = -1


class _DependencyCaptureScope:
    """Private compact dependency capture for one decorated computation."""

    def __init__(self) -> None:
        self._capture: DependencyCapture | None = None
        self._parent_capture: DependencyCapture | None = None
        self._generation = -1
        self.snapshot: DependencySnapshot | None = None

    def __enter__(self) -> "_DependencyCaptureScope":
        storage = _dependency_storage
        self._generation = storage.generation
        self.snapshot = None
        capture = DependencyCapture()
        self._capture = capture
        self._parent_capture = storage.captures[-1] if storage.captures else None
        storage.captures.append(capture)
        storage.stack_version += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        storage = _dependency_storage
        capture = self._capture
        if capture is None:
            return
        parent_capture = self._parent_capture
        self._capture = None
        self._parent_capture = None
        snapshot = capture.freeze()
        self.snapshot = snapshot
        if (
            storage.generation == self._generation
            and storage.captures
            and storage.captures[-1] is capture
        ):
            storage.captures.pop()
            storage.stack_version += 1
            storage.last_dependency = None
            storage.last_dependency_stack_version = -1
            if not storage.dependencies and not storage.captures:
                _clear_duplicate_state(storage)
        if parent_capture is not None:
            parent_capture.attach(snapshot)

    def _record_argument_dependency(self, dependency: Dependency) -> bool:
        """Record a decorator-derived argument after this scope was detached."""
        capture = self._capture
        storage = _dependency_storage
        if capture is not None and (
            storage.generation != self._generation
            or not storage.captures
            or storage.captures[-1] is not capture
        ):
            capture.add(dependency)
            return True
        return False


class DependencyTracker:
    """Capture dependencies touched inside a read or cache-computation scope.

    Tracking is thread-local, not async task-local; same-thread async tasks share
    tracker state if their execution is interleaved inside one active context.
    """

    def __init__(self) -> None:
        """Create per-thread scope storage before the tracker can be shared."""
        self._scope_storage = threading.local()

    def __enter__(
        self,
    ) -> set[Dependency]:
        """Enter a dependency tracking context and return the current collector.

        Returns:
            Mutable set capturing dependencies discovered inside this context.
            A `Dependency` is `tuple[str, Literal["filter", "exclude",
            "identification", "request_query", "all"], str]`.
            Nested contexts receive their own set, and `track(...)` records each
            dependency in every active enclosing context. The returned set
            remains a usable snapshot after the context exits; clearing
            thread-local storage does not mutate sets that were already returned.
        """
        storage = _dependency_storage
        scope_storage = self._scope_storage
        if not hasattr(scope_storage, "scopes"):
            scope_storage.scopes = []
        scopes: list[tuple[int, _TrackedDependencySet]] = scope_storage.scopes
        generation = storage.generation
        collector = _TrackedDependencySet()
        scopes.append((generation, collector))
        storage.dependencies.append(collector)
        storage.depth = len(storage.dependencies) - 1
        storage.stack_version += 1
        return collector

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Leave the dependency tracking context and clean up this thread.

        Args:
            exc_type: Exception type raised within the context, if any.
            exc_val: Exception instance raised within the context, if any.
            exc_tb: Traceback generated by the exception, if any.

        The tracker does not suppress exceptions. Exiting the outermost context
        clears all thread-local tracking state; exiting a nested context removes
        only that nested collector. Calling `__exit__` after
        `reset_thread_local_storage()` or otherwise without an active context is
        a no-op.
        """
        storage = _dependency_storage
        scope_storage = getattr(self, "_scope_storage", None)
        scopes = getattr(scope_storage, "scopes", None)
        if not scopes:
            return
        generation, collector = scopes.pop()
        if (
            generation != storage.generation
            or not storage.dependencies
            or storage.dependencies[-1] is not collector
        ):
            return
        storage.dependencies.pop()
        storage.depth = len(storage.dependencies) - 1
        storage.stack_version += 1
        storage.last_dependency = None
        storage.last_dependency_stack_version = -1
        if not storage.dependencies and not storage.captures:
            _clear_duplicate_state(storage)

    @staticmethod
    def track(
        class_name: general_manager_name,
        operation: filter_type,
        identifier: str,
    ) -> None:
        """Record a dependency tuple in all active tracking scopes.

        Args:
            class_name: Name of the GeneralManager subclass.
            operation: Operation being tracked, such as `filter`, `exclude`,
                `identification`, `request_query`, or `all`.
            identifier: String representation of the lookup parameters.

        Calling this method without an active `DependencyTracker` context is a
        no-op after validating the supplied values. Duplicate tuples collapse
        naturally because each collector is a set.

        Raises:
            TypeError: If `class_name`, `operation`, or `identifier` is not a
                string.
            ValueError: If `operation` is not one of `filter`, `exclude`,
                `identification`, `request_query`, or `all`.

        Concrete exception subclasses and messages are internal implementation
        details; callers should rely on the public base `TypeError` and
        `ValueError` contract.
        """
        if (
            not isinstance(class_name, str)
            or not isinstance(operation, str)
            or not isinstance(identifier, str)
        ):
            raise _InvalidDependencyTrackerValueError
        if operation not in _SUPPORTED_OPERATIONS:
            raise _InvalidDependencyTrackerOperationError(operation)
        DependencyTracker._track_validated(class_name, operation, identifier)

    @staticmethod
    def _track_validated(
        class_name: general_manager_name,
        operation: filter_type,
        identifier: str,
    ) -> None:
        """Record an already-validated dependency tuple in active collectors."""
        storage = _dependency_storage
        if not storage.dependencies and not storage.captures:
            return
        if storage.last_dependency_stack_version == storage.stack_version:
            last_dependency = storage.last_dependency
            if (
                last_dependency is not None
                and last_dependency[0] == class_name
                and last_dependency[1] == operation
                and last_dependency[2] == identifier
            ):
                return
        dependency = (class_name, operation, identifier)
        if storage.seen_dependencies_stack_version != storage.stack_version:
            storage.seen_dependencies.clear()
            storage.seen_dependencies_stack_version = storage.stack_version
        elif dependency in storage.seen_dependencies:
            return
        for dep_set in storage.dependencies:
            dep_set.add(dependency)
        if storage.captures:
            storage.captures[-1].add(dependency)
        storage.last_dependency = dependency
        storage.last_dependency_stack_version = storage.stack_version
        storage.seen_dependencies.add(dependency)

    @staticmethod
    def _track_many_validated(dependencies: Iterable[Dependency]) -> None:
        """Record already-validated dependency tuples in active collectors."""
        storage = _dependency_storage
        if not storage.dependencies and not storage.captures:
            return
        if type(dependencies) in (tuple, frozenset):
            if storage.dependencies:
                for dep_set in storage.dependencies:
                    dep_set.update(dependencies)
            if storage.captures:
                storage.captures[-1].attach(snapshot_from_dependencies(dependencies))
            return
        frozen_dependencies = tuple(dependencies)
        for dep_set in storage.dependencies:
            dep_set.update(frozen_dependencies)
        if storage.captures:
            storage.captures[-1].attach(snapshot_from_dependencies(frozen_dependencies))

    @staticmethod
    def _capture() -> _DependencyCaptureScope:
        """Create a private graph capture scope for cache implementations."""
        return _DependencyCaptureScope()

    @staticmethod
    def _attach_snapshot(snapshot: DependencySnapshot) -> None:
        """Attach cached graph metadata without flattening private parents."""
        storage = _dependency_storage
        if storage.captures:
            storage.captures[-1].attach(snapshot)
        if storage.dependencies:
            dependencies = materialize_dependencies(snapshot)
            for dep_set in storage.dependencies:
                dep_set.update(dependencies)

    @staticmethod
    def _materialize_snapshot(snapshot: DependencySnapshot) -> set[Dependency]:
        """Expose one trusted mutable publication set for a snapshot boundary."""
        return _CapturedDependencySet(materialize_dependencies(snapshot))

    @staticmethod
    def _dependencies_are_tracker_captured(dependencies: object) -> bool:
        """Return whether dependencies came from this tracker implementation."""
        return isinstance(dependencies, _TrackedDependencySet)

    @staticmethod
    def is_active() -> bool:
        """Return whether dependency tracking is active for this execution context."""
        return bool(_dependency_storage.dependencies or _dependency_storage.captures)

    @staticmethod
    def reset_thread_local_storage() -> None:
        """Clear all dependency tracking data for the current thread.

        It is safe to call with no active context or inside an active context.
        Already returned collector sets keep their current contents, later
        `track(...)` calls are ignored until a new context is entered, and the
        eventual `__exit__` for the reset context is a no-op.
        """
        _dependency_storage.reset()
