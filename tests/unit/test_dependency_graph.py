from __future__ import annotations

from general_manager.cache._dependency_graph import (
    DependencyCapture,
    DependencySnapshot,
    _DependencyBlock,
    materialize_dependencies,
    snapshot_from_dependencies,
)


def test_capture_reuses_repeated_child_identity_without_flattening() -> None:
    child = snapshot_from_dependencies(
        ("Project", "identification", "1") for _ in range(1)
    )
    capture = DependencyCapture()

    capture.attach(child)
    capture.attach(child)
    root = capture.freeze()

    assert root is child


def test_materialization_visits_shared_block_once() -> None:
    dependency = ("Project", "identification", "1")
    block = _DependencyBlock((dependency,))
    left = DependencySnapshot(block, ())
    right = DependencySnapshot(block, ())
    root = DependencySnapshot(None, (left, right))

    assert materialize_dependencies(root) == {dependency}


def test_snapshot_preserves_existing_immutable_dependency_container_identity() -> None:
    dependencies = frozenset({("Project", "identification", "1")})
    left = snapshot_from_dependencies(dependencies)
    right = snapshot_from_dependencies(dependencies)

    assert left.block is not None
    assert right.block is not None
    assert left.block is not right.block
    assert left.block.dependencies is dependencies
    assert right.block.dependencies is dependencies
    assert materialize_dependencies(DependencySnapshot(None, (left, right))) == set(
        dependencies
    )


def test_materialization_is_iterative_for_deep_graphs() -> None:
    root = snapshot_from_dependencies((("Project", "identification", "1"),))
    for _ in range(2_000):
        root = DependencySnapshot(None, (root,))

    assert materialize_dependencies(root) == {("Project", "identification", "1")}
