"""Resolve manager targets from GraphQL relation annotations."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from types import UnionType
from typing import ForwardRef, Union, get_args, get_origin

from general_manager.bucket.base_bucket import Bucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.type_checks import safe_issubclass


def get_graphql_manager_registry() -> dict[str, type[GeneralManager]]:
    """Return the live GraphQL manager registry without an import-time cycle."""
    from general_manager.api.graphql import GraphQL

    return GraphQL.manager_registry


def resolve_general_manager_type(
    declared_type: object,
    manager_registry: Mapping[str, type[GeneralManager]] | None = None,
) -> type[GeneralManager] | None:
    """Return the single manager target represented by ``declared_type``.

    Concrete manager classes, interface model classes carrying a manager
    back-reference, supported collection annotations, optional unions, and
    postponed string forms of those annotations are recognized. Ambiguous
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

    related_manager_type = getattr(declared_type, "_general_manager_class", None)
    if safe_issubclass(related_manager_type, GeneralManager):
        return {related_manager_type}

    if isinstance(declared_type, ForwardRef):
        declared_type = declared_type.__forward_arg__
    if isinstance(declared_type, str):
        manager_type = manager_registry.get(declared_type)
        if manager_type is not None:
            return {manager_type}
        return _collect_string_general_manager_types(
            declared_type,
            manager_registry,
            set(),
        )

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


_SUPPORTED_STRING_WRAPPERS = {
    "Bucket",
    "List",
    "Optional",
    "Set",
    "Tuple",
    "Union",
    "list",
    "set",
    "tuple",
}


def _collect_string_general_manager_types(
    annotation: str,
    manager_registry: Mapping[str, type[GeneralManager]],
    seen: set[str],
) -> set[type[GeneralManager]]:
    expression = annotation.strip()
    if not expression or expression in seen:
        return set()
    seen.add(expression)

    try:
        node = ast.parse(expression, mode="eval").body
    except (SyntaxError, ValueError):
        return set()
    return _collect_annotation_node_manager_types(node, manager_registry, seen)


def _collect_annotation_node_manager_types(
    node: ast.expr,
    manager_registry: Mapping[str, type[GeneralManager]],
    seen: set[str],
) -> set[type[GeneralManager]]:
    if isinstance(node, ast.Name):
        manager_type = manager_registry.get(node.id)
        return {manager_type} if manager_type is not None else set()

    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return _collect_string_general_manager_types(
                node.value,
                manager_registry,
                seen,
            )
        return set()

    if isinstance(node, ast.Subscript):
        wrapper_name = _annotation_node_name(node.value)
        if wrapper_name is None or wrapper_name.rsplit(".", 1)[-1] not in (
            _SUPPORTED_STRING_WRAPPERS
        ):
            return set()
        return _collect_annotation_node_manager_types(
            node.slice,
            manager_registry,
            seen,
        )

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _collect_annotation_node_manager_types(
            node.left,
            manager_registry,
            seen,
        ) | _collect_annotation_node_manager_types(
            node.right,
            manager_registry,
            seen,
        )

    if isinstance(node, ast.Tuple):
        manager_types: set[type[GeneralManager]] = set()
        for element in node.elts:
            manager_types.update(
                _collect_annotation_node_manager_types(
                    element,
                    manager_registry,
                    seen,
                )
            )
        return manager_types

    return set()


def _annotation_node_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent_name = _annotation_node_name(node.value)
        if parent_name is not None:
            return f"{parent_name}.{node.attr}"
    return None
