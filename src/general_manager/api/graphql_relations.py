"""Resolve manager targets from GraphQL relation annotations."""

from __future__ import annotations

from collections.abc import Mapping
from types import UnionType
from typing import ForwardRef, Union, get_args, get_origin

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.type_checks import safe_issubclass


def resolve_general_manager_type(
    declared_type: object,
    manager_registry: Mapping[str, type[GeneralManager]] | None = None,
) -> type[GeneralManager] | None:
    """Return the single manager target represented by ``declared_type``.

    Concrete manager classes, supported collection annotations, optional
    unions, and exact names in ``manager_registry`` are recognized. Ambiguous
    annotations containing multiple manager targets return ``None``.
    """
    registry = manager_registry or {}
    resolved = _collect_general_manager_types(declared_type, registry, set())
    if len(resolved) != 1:
        return None
    return next(iter(resolved))


def _collect_general_manager_types(
    declared_type: object,
    manager_registry: Mapping[str, type[GeneralManager]],
    seen: set[int],
) -> set[type[GeneralManager]]:
    value_id = id(declared_type)
    if value_id in seen:
        return set()
    seen.add(value_id)

    if safe_issubclass(declared_type, GeneralManager):
        return {declared_type}

    if isinstance(declared_type, ForwardRef):
        declared_type = declared_type.__forward_arg__
    if isinstance(declared_type, str):
        manager_type = manager_registry.get(declared_type)
        return {manager_type} if manager_type is not None else set()

    origin = get_origin(declared_type)
    if origin is None:
        return set()
    if origin not in {list, tuple, set, Union, UnionType} and not safe_issubclass(
        origin, Bucket
    ):
        return set()

    manager_types: set[type[GeneralManager]] = set()
    for argument in get_args(declared_type):
        if argument is None or argument is type(None) or argument is Ellipsis:
            continue
        manager_types.update(
            _collect_general_manager_types(argument, manager_registry, seen)
        )
    return manager_types
