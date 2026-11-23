"""Logical Excel reads backed by the existing cross-operation workbook mirror."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from inspect import getattr_static
from pathlib import Path
from threading import RLock, get_ident
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_manager.interface.excel import ExcelMeta, ExcelRowSnapshot
    from general_manager.interface.excel_workbook import WorkbookFingerprint
    from general_manager.interface.interfaces.excel import ExcelInterface


@dataclass(frozen=True)
class ExcelReadGeneration:
    source: ExcelMeta
    workbook_key: str
    rows: Mapping[Any, ExcelRowSnapshot]
    fingerprint: WorkbookFingerprint


@dataclass
class _Refresh:
    interface: type[ExcelInterface]
    owner: int
    generation: ExcelReadGeneration | None = None
    invalidated: bool = False


@dataclass
class _ReadScope:
    owner: int
    parent: _ReadScope | None = None
    continuation_owner: int | None = None
    lock: RLock = field(default_factory=RLock)
    dependency_generation: int | None = None
    generations: dict[type[ExcelInterface], ExcelReadGeneration] = field(
        default_factory=dict
    )
    checks: dict[str, WorkbookFingerprint] = field(default_factory=dict)
    refreshing: _Refresh | None = None
    active: bool = True


_SCOPE: ContextVar[_ReadScope | None] = ContextVar(
    "_excel_workbook_read_scope", default=None
)


@contextmanager
def read_snapshot_scope() -> Iterator[None]:
    """Pin raw workbook generations for one logical, read-only operation.

    A new scope verifies freshness against workbook contents on first use.
    Permission checks still run against the raw rows for the current caller.
    Keep a scope to one operation: external edits become visible next time.
    """
    scope = _ReadScope(get_ident(), _scope())
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        scope.active = False
        _SCOPE.reset(token)


@contextmanager
def continue_read_snapshot_scope() -> Iterator[None]:
    """Continue the same GraphQL operation after its blocking async handoff."""
    scope = _SCOPE.get()
    if scope is None or not scope.active:
        with read_snapshot_scope():
            yield
        return
    # asgiref's thread-sensitive callbacks run on the original sync thread,
    # with this same context and while the caller is waiting for completion.
    previous = scope.continuation_owner
    scope.continuation_owner = get_ident()
    try:
        yield
    finally:
        scope.continuation_owner = previous


def constrain_excel_dependency_generation(computation_generation: int) -> int:
    """Prevent a derived result from persisting stale pinned workbook inputs."""
    scope = _scope()
    generation = scope.dependency_generation if scope is not None else None
    return (
        computation_generation
        if generation is None
        else min(computation_generation, generation)
    )


def excel_dependency_cache_conflicts() -> bool:
    """Check that a shared-cache hit belongs to the pinned operation's epoch."""
    scope = _scope()
    generation = scope.dependency_generation if scope is not None else None
    if generation is None:
        return False
    from general_manager.cache.dependency_index import get_dependency_generation

    return get_dependency_generation() != generation


def _record_dependency_generation(scope: _ReadScope) -> None:
    with scope.lock:
        if scope.dependency_generation is not None:
            return
    from general_manager.cache.dependency_index import get_dependency_generation

    generation = get_dependency_generation()
    current: _ReadScope | None = scope
    while current is not None:
        with current.lock:
            if not current.active:
                return
            previous = current.dependency_generation
            current.dependency_generation = (
                generation if previous is None else min(previous, generation)
            )
        current = current.parent


def _scope() -> _ReadScope | None:
    scope = _SCOPE.get()
    if scope is None or not scope.active:
        return None
    caller = get_ident()
    if caller != scope.owner and caller != scope.continuation_owner:
        return None
    return scope


def peek_read_generation(interface: type[ExcelInterface]) -> ExcelReadGeneration | None:
    scope = _scope()
    if scope is None:
        return None
    source = interface.excel_meta
    with scope.lock:
        return _pinned_generation(scope, interface, source)


def _pinned_generation(
    scope: _ReadScope, interface: type[ExcelInterface], source: ExcelMeta
) -> ExcelReadGeneration | None:
    generation = scope.generations.get(interface)
    return (
        generation if generation is not None and generation.source is source else None
    )


def read_generation(interface: type[ExcelInterface]) -> ExcelReadGeneration | None:
    """Synchronize and pin an eligible interface; leave failed reads unpinned."""
    scope = _scope()
    if scope is None:
        return None
    current = peek_read_generation(interface)
    if current is not None:
        return current
    if not _supports_snapshot(interface):
        return None

    _record_dependency_generation(scope)
    source = interface.excel_meta
    refresh = _Refresh(interface, get_ident())
    with scope.lock:
        current = _pinned_generation(scope, interface, source)
        if current is not None:
            return current
        # Do not wait under the operation lock. The peer or a custom callback
        # can hold a workbook lock; a contested read uses the ordinary path.
        if scope.refreshing is not None:
            return None
        scope.refreshing = refresh

    completed_source = None
    selected = None
    try:
        interface.sync_from_excel()
        completed_source = interface.excel_meta
    finally:
        with scope.lock:
            generation = refresh.generation
            if (
                scope.active
                and not refresh.invalidated
                and generation is not None
                and generation.source is completed_source
            ):
                scope.generations[interface] = generation
                selected = generation
            else:
                # Don't keep observations from failed or superseded refreshes.
                scope.checks.clear()
            scope.refreshing = None
    return selected


def _supports_snapshot(interface: type[ExcelInterface]) -> bool:
    from general_manager.interface.base_interface import InterfaceBase
    from general_manager.interface.capabilities.excel import (
        ExcelReadCapability,
        ExcelSyncCapability,
    )
    from general_manager.interface.interfaces.excel import ExcelInterface

    if not issubclass(interface, ExcelInterface):
        return False
    reader = interface.get_capability_handler("read")
    explicitly_enabled = isinstance(reader, ExcelReadCapability) and (
        vars(type(reader)).get("query_read_snapshots") is True
    )
    ordinary = (
        type(reader) is ExcelReadCapability
        and getattr_static(interface, "sync_from_excel")
        is getattr_static(ExcelInterface, "sync_from_excel")
        and getattr_static(interface, "require_capability")
        is getattr_static(InterfaceBase, "require_capability")
        and getattr_static(interface, "get_capability_handler")
        is getattr_static(InterfaceBase, "get_capability_handler")
        and type(interface.get_capability_handler("excel_sync")) is ExcelSyncCapability
        and interface.get_capability_handler("observability") is None
    )
    return explicitly_enabled or ordinary


def remember_read_generation(
    interface: type[ExcelInterface],
    rows: Mapping[Any, ExcelRowSnapshot],
    fingerprint: WorkbookFingerprint | None,
) -> None:
    """Capture the exact generation selected by regular sync, under its lock."""
    scope = _scope()
    if scope is None or fingerprint is None:
        return
    caller = get_ident()
    source = interface.excel_meta
    key = _workbook_key(source.workbook)
    with scope.lock:
        refresh = scope.refreshing
        if (
            refresh is not None
            and refresh.owner == caller
            and refresh.interface is interface
            and not refresh.invalidated
        ):
            refresh.generation = ExcelReadGeneration(source, key, rows, fingerprint)
            # The existing parser independently verified the now-published bytes.
            scope.checks[key] = fingerprint


def read_scope_fingerprint(
    path: str, compute: Callable[[str], WorkbookFingerprint]
) -> WorkbookFingerprint:
    """Memoize only the warm comparison during an eligible ordinary refresh."""
    scope = _scope()
    if scope is None:
        return compute(path)
    caller = get_ident()
    key = _workbook_key(path)
    with scope.lock:
        refresh = scope.refreshing
        eligible = (
            refresh is not None and refresh.owner == caller and not refresh.invalidated
        )
        if eligible:
            cached = scope.checks.get(key)
            if cached is not None:
                return cached

    fingerprint = compute(path)
    if eligible and refresh is not None:
        with scope.lock:
            if scope.refreshing is refresh and not refresh.invalidated:
                scope.checks[key] = fingerprint
    return fingerprint


def _workbook_key(path: str) -> str:
    return str(Path(path).resolve())


def discard_read_generation(workbook: str) -> None:
    """Drop operation pins when explicit forced synchronization starts."""
    scope = _scope()
    if scope is None:
        return
    key = _workbook_key(workbook)
    while scope is not None and scope.active:
        with scope.lock:
            scope.checks.pop(key, None)
            for interface, generation in tuple(scope.generations.items()):
                if generation.workbook_key == key:
                    del scope.generations[interface]
            if scope.refreshing is not None:
                scope.refreshing.invalidated = True
        scope = scope.parent
