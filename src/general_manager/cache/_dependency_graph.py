"""Private immutable dependency snapshots shared by cache computations."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from general_manager.cache.dependency_index import Dependency


@dataclass(frozen=True, slots=True, eq=False)
class _DependencyBlock:
    """One immutable collection of directly observed dependency tuples.

    Equality deliberately remains identity based.  A block can be referenced by
    several snapshot leaves (notably prefetched dependency-cache hits), and a
    materialization must visit that shared block only once.
    """

    dependencies: tuple[Dependency, ...] | frozenset[Dependency]


@dataclass(frozen=True, slots=True, eq=False)
class DependencySnapshot:
    """An immutable node containing direct dependencies and child snapshots."""

    block: _DependencyBlock | None
    children: tuple["DependencySnapshot", ...]


_EMPTY_SNAPSHOT = DependencySnapshot(block=None, children=())


def empty_dependency_snapshot() -> DependencySnapshot:
    """Return the shared empty dependency root."""
    return _EMPTY_SNAPSHOT


def snapshot_from_dependencies(
    dependencies: Iterable[Dependency],
) -> DependencySnapshot:
    """Freeze direct dependencies at a boundary into a standalone leaf."""
    # Hits already own an immutable dependency container.  Retaining that
    # container by identity is what lets independent leaf wrappers share its
    # storage (and lets materialization/accounting charge it once).  Mutable
    # inputs must still be copied at this boundary.
    if type(dependencies) in (tuple, frozenset):
        frozen_dependencies = cast(
            tuple[Dependency, ...] | frozenset[Dependency], dependencies
        )
    else:
        frozen_dependencies = tuple(dependencies)
    if not frozen_dependencies:
        return _EMPTY_SNAPSHOT
    return DependencySnapshot(_DependencyBlock(frozen_dependencies), ())


class DependencyCapture:
    """Mutable builder used only while one cache calculation is executing."""

    __slots__ = ("_child_ids", "_children", "_dependencies")

    def __init__(self) -> None:
        """Start an empty per-calculation builder."""
        self._dependencies: set[Dependency] = set()
        self._children: list[DependencySnapshot] = []
        self._child_ids: set[int] = set()

    def add(self, dependency: Dependency) -> None:
        """Record one direct dependency in this calculation frame."""
        self._dependencies.add(dependency)

    def attach(self, snapshot: DependencySnapshot) -> None:
        """Attach a child snapshot once, using node identity for deduplication."""
        if snapshot is _EMPTY_SNAPSHOT:
            return
        snapshot_id = id(snapshot)
        if snapshot_id not in self._child_ids:
            self._child_ids.add(snapshot_id)
            self._children.append(snapshot)

    def freeze(self) -> DependencySnapshot:
        """Build the immutable root, reusing simple child-only snapshots."""
        if not self._dependencies:
            if not self._children:
                return _EMPTY_SNAPSHOT
            if len(self._children) == 1:
                return self._children[0]
            return DependencySnapshot(None, tuple(self._children))
        return DependencySnapshot(
            _DependencyBlock(tuple(self._dependencies)),
            tuple(self._children),
        )


def materialize_dependencies(snapshot: DependencySnapshot) -> set[Dependency]:
    """Iteratively flatten a graph, visiting nodes and direct blocks by identity."""
    dependencies: set[Dependency] = set()
    pending = [snapshot]
    seen_nodes: set[int] = set()
    seen_dependency_containers: set[int] = set()
    while pending:
        node = pending.pop()
        node_id = id(node)
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        block = node.block
        if block is not None:
            dependency_container_id = id(block.dependencies)
            if dependency_container_id not in seen_dependency_containers:
                seen_dependency_containers.add(dependency_container_id)
                dependencies.update(block.dependencies)
        pending.extend(node.children)
    return dependencies
