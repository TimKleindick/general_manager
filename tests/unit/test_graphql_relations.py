from __future__ import annotations

from typing import ForwardRef

from general_manager.api.graphql_relations import resolve_general_manager_type
from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager


class PrimaryManager(GeneralManager):
    pass


class SecondaryManager(GeneralManager):
    pass


def test_resolve_general_manager_type_handles_concrete_and_wrapped_managers() -> None:
    registry = {"PrimaryManager": PrimaryManager}

    class PrimaryModel:
        _general_manager_class = PrimaryManager

    for declared_type in (
        PrimaryManager,
        PrimaryModel,
        list[PrimaryManager],
        tuple[PrimaryManager, ...],
        set[PrimaryManager],
        Bucket[PrimaryManager],
        PrimaryManager | None,
        "PrimaryManager",
        "Bucket[PrimaryManager]",
        "Bucket['PrimaryManager']",
        "tuple[PrimaryManager, ...]",
        "typing.Optional[PrimaryManager]",
        ForwardRef("Bucket[PrimaryManager]"),
    ):
        assert resolve_general_manager_type(declared_type, registry) is PrimaryManager


def test_resolve_general_manager_type_rejects_non_manager_and_unresolved_types() -> (
    None
):
    registry = {"PrimaryManager": PrimaryManager}

    for declared_type in (
        str,
        list[str],
        dict[str, PrimaryManager],
        "",
        "Bucket[",
        "Bucket[42]",
        "Bucket[lambda: PrimaryManager]",
        "dict[str, PrimaryManager]",
        "MissingManager",
        "typing().Bucket[PrimaryManager]",
    ):
        assert resolve_general_manager_type(declared_type, registry) is None


def test_resolve_general_manager_type_rejects_ambiguous_manager_union() -> None:
    assert resolve_general_manager_type(PrimaryManager | SecondaryManager, {}) is None
    registry = {
        "PrimaryManager": PrimaryManager,
        "SecondaryManager": SecondaryManager,
    }
    assert (
        resolve_general_manager_type(
            "PrimaryManager | SecondaryManager",
            registry,
        )
        is None
    )
