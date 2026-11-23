"""Logical Excel reads backed by the existing cross-operation workbook mirror."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from inspect import getattr_static
from pathlib import Path
from threading import get_ident
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from general_manager.interface.excel import ExcelMeta, ExcelRowSnapshot
    from general_manager.interface.excel_workbook import WorkbookFingerprint
    from general_manager.interface.interfaces.excel import ExcelInterface


@dataclass(frozen=True)
class ExcelReadGeneration:
    source: ExcelMeta
    rows: Mapping[Any, ExcelRowSnapshot]
    fingerprint: WorkbookFingerprint


@dataclass
class _Refresh:
    interface: type[ExcelInterface]
    checks: dict[str, WorkbookFingerprint]
    generation: ExcelReadGeneration | None = None


@dataclass
class _ReadScope:
    owner: int
    parent: _ReadScope | None = None
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
    owner = scope.owner
    scope.owner = get_ident()
    try:
        yield
    finally:
        scope.owner = owner


def constrain_excel_dependency_generation(computation_generation: int) -> int:
    """Prevent a derived result from persisting stale pinned workbook inputs."""
    scope = _scope()
    generation = scope.dependency_generation if scope is not None else None
    return (
        computation_generation
        if generation is None
        else min(computation_generation, generation)
    )


def _record_dependency_generation(scope: _ReadScope) -> None:
    if scope.dependency_generation is not None:
        return
    from general_manager.cache.dependency_index import get_dependency_generation

    generation = get_dependency_generation()
    current: _ReadScope | None = scope
    while current is not None and current.active:
        if current.dependency_generation is None:
            current.dependency_generation = generation
        current = current.parent


def _scope() -> _ReadScope | None:
    scope = _SCOPE.get()
    if scope is None or not scope.active or scope.owner != get_ident():
        return None
    return scope


def peek_read_generation(interface: type[ExcelInterface]) -> ExcelReadGeneration | None:
    scope = _scope()
    if scope is None or scope.refreshing is not None:
        return None
    generation = scope.generations.get(interface)
    if generation is not None and generation.source is interface.excel_meta:
        return generation
    return None


def read_generation(interface: type[ExcelInterface]) -> ExcelReadGeneration | None:
    """Synchronize and pin an eligible interface; leave failed reads unpinned."""
    scope = _scope()
    if scope is None or scope.refreshing is not None:
        return None
    current = peek_read_generation(interface)
    if current is not None:
        return current

    from general_manager.interface.base_interface import InterfaceBase
    from general_manager.interface.capabilities.excel import (
        ExcelReadCapability,
        ExcelSyncCapability,
    )
    from general_manager.interface.interfaces.excel import ExcelInterface

    if not issubclass(interface, ExcelInterface):
        return None
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
    if not (explicitly_enabled or ordinary):
        return None

    _record_dependency_generation(scope)
    refresh = _Refresh(interface, scope.checks)
    scope.refreshing = refresh
    try:
        interface.sync_from_excel()
    except BaseException:
        # Don't reuse an observation from failed validation or publication.
        scope.checks.clear()
        raise
    finally:
        scope.refreshing = None
    generation = refresh.generation
    if generation is not None and generation.source is interface.excel_meta:
        scope.generations[interface] = generation
        return generation
    scope.checks.clear()
    return None


def remember_read_generation(
    interface: type[ExcelInterface],
    rows: Mapping[Any, ExcelRowSnapshot],
    fingerprint: WorkbookFingerprint | None,
) -> None:
    """Capture the exact generation selected by regular sync, under its lock."""
    scope = _scope()
    refresh = scope.refreshing if scope is not None else None
    if (
        refresh is not None
        and refresh.interface is interface
        and fingerprint is not None
    ):
        refresh.generation = ExcelReadGeneration(
            interface.excel_meta, rows, fingerprint
        )
        # The existing parser independently verified the now-published bytes.
        refresh.checks[_workbook_key(interface.excel_meta.workbook)] = fingerprint


def read_scope_fingerprint(
    path: str, compute: Callable[[str], WorkbookFingerprint]
) -> WorkbookFingerprint:
    """Memoize only the warm comparison during an eligible ordinary refresh."""
    scope = _scope()
    refresh = scope.refreshing if scope is not None else None
    if refresh is None:
        return compute(path)
    key = _workbook_key(path)
    if key not in refresh.checks:
        refresh.checks[key] = compute(path)
    return refresh.checks[key]


def _workbook_key(path: str) -> str:
    return str(Path(path).resolve())


def discard_read_generation(workbook: str) -> None:
    """Drop operation pins when explicit forced synchronization starts."""
    scope = _scope()
    if scope is None:
        return
    key = _workbook_key(workbook)
    while scope is not None and scope.active:
        scope.checks.pop(key, None)
        for interface, generation in tuple(scope.generations.items()):
            if _workbook_key(generation.source.workbook) == key:
                del scope.generations[interface]
        if scope.refreshing is not None:
            scope.refreshing.generation = None
        scope = scope.parent
