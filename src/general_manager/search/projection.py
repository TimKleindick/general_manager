"""Explicit native-field provenance for custom search document projections."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType, UnionType
from typing import TYPE_CHECKING, Annotated, Literal, Union, get_args, get_origin
from uuid import UUID

from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement import Measurement
from general_manager.search.identity import get_search_identity_fields, identity_lookup
from general_manager.utils.filter_parser import LookupName
from general_manager.utils.type_checks import safe_issubclass

if TYPE_CHECKING:
    from general_manager.search.config import IndexConfig, SearchConfigSpec


class SearchProjectionError(ValueError):
    """A projection cannot establish complete native permission provenance."""

    @classmethod
    def invalid_declaration(cls) -> SearchProjectionError:
        return cls(
            "Search field dependencies must map configured document fields "
            "to nonempty sequences of native paths."
        )

    @classmethod
    def invalid_source(cls, path: str) -> SearchProjectionError:
        return cls(f"Invalid search source path {path!r}.")

    @classmethod
    def missing_declaration(cls) -> SearchProjectionError:
        return cls(
            "Cannot authorize fields of a custom search document projection. "
            "Declare complete field_dependencies to enable GraphQL search."
        )

    @classmethod
    def missing_field(cls, field: str) -> SearchProjectionError:
        return cls(f"Missing search field dependencies for {field!r}.")

    @classmethod
    def undeclared_fallback(cls, field: str) -> SearchProjectionError:
        return cls(f"Undeclared native fallback for search field {field!r}.")

    @classmethod
    def requires_projection(cls) -> SearchProjectionError:
        return cls("Search field dependencies require a custom to_document projection.")

    @classmethod
    def invalid_filter(cls, field: str) -> SearchProjectionError:
        return cls(f"Cannot authorize search document filter {field!r}.")


def normalize_field_dependencies(
    indexes: Sequence[IndexConfig], declaration: object
) -> Mapping[str, tuple[str, ...]] | None:
    """Freeze a structurally valid, developer-authored dependency assertion."""
    if declaration is None:
        return None
    if not isinstance(declaration, Mapping):
        raise SearchProjectionError.invalid_declaration()
    configured = {
        name
        for index in indexes
        for name in (
            *(entry.name for entry in index.iter_fields()),
            *index.filters,
            *index.sorts,
        )
    }
    normalized: dict[str, tuple[str, ...]] = {}
    for name, paths in declaration.items():
        if not isinstance(name, str) or not name or name not in configured:
            raise SearchProjectionError.invalid_declaration()
        if (
            not isinstance(paths, Sequence)
            or isinstance(paths, (str, bytes, bytearray))
            or not paths
            or any(
                not isinstance(path, str)
                or not all(part.isidentifier() for part in path.split("__"))
                for path in paths
            )
        ):
            raise SearchProjectionError.invalid_declaration()
        normalized[name] = tuple(dict.fromkeys(paths))
    return MappingProxyType(normalized)


def source_field_annotation(
    manager_class: type[GeneralManager], field: str
) -> tuple[bool, object]:
    """Inspect one exact native name without reading a manager instance."""
    interface = manager_class.Interface
    attributes = interface.get_attribute_types()
    properties = interface.get_graph_ql_properties()
    if field in properties:
        return True, properties[field].graphql_type_hint
    if field in attributes:
        return True, attributes[field].get("type")
    if field in interface.input_fields:
        return True, interface.input_fields[field].type
    return field in interface.get_attributes(), None


def _is_scalar_source(annotation: object) -> bool:
    """Prove a scalar leaf rather than treating an unresolved relation as one.

    The legacy GraphQL scalar mapper falls back to String for opaque types;
    that fallback cannot establish permission provenance for a projection.
    """
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin is Annotated:
        return bool(arguments) and _is_scalar_source(arguments[0])
    if origin in {Union, UnionType}:
        members = [member for member in arguments if member is not type(None)]
        return bool(members) and all(_is_scalar_source(member) for member in members)
    if origin is Literal:
        return bool(arguments) and all(
            _is_scalar_source(type(value)) for value in arguments if value is not None
        )
    if origin in {list, tuple, set, frozenset, dict}:
        members = [member for member in arguments if member is not Ellipsis]
        return bool(members) and all(_is_scalar_source(member) for member in members)
    return safe_issubclass(
        annotation,
        (
            str,
            bool,
            int,
            float,
            Decimal,
            date,
            datetime,
            time,
            timedelta,
            UUID,
            bytes,
            Measurement,
        ),
    )


def validate_source_paths(
    manager_class: type[GeneralManager], paths: Iterable[str]
) -> None:
    """Validate exact native paths even for empty or nullable candidate scopes."""
    from general_manager.api.graphql_relations import (
        get_graphql_manager_registry,
        resolve_general_manager_type,
    )

    registry = get_graphql_manager_registry()
    for path in paths:
        parts = path.split("__")
        current = manager_class
        for position, part in enumerate(parts):
            known, annotation = source_field_annotation(current, part)
            related = resolve_general_manager_type(annotation, registry)
            if (
                not known
                or not part.isidentifier()
                or (position < len(parts) - 1 and related is None)
                or (position == len(parts) - 1 and not _is_scalar_source(annotation))
            ):
                raise SearchProjectionError.invalid_source(path)
            if related is not None:
                current = related


def projection_source_paths(
    manager_class: type[GeneralManager],
    config: SearchConfigSpec,
    document_fields: Iterable[str],
) -> tuple[str, ...]:
    """Resolve every used alias to its complete, exact native source paths.

    The application assertion must cover all serializer branches. It declares
    provenance, not value equality, and cannot prove arbitrary Python accesses.
    """
    dependencies = config.field_dependencies
    if dependencies is None:
        raise SearchProjectionError.missing_declaration()
    paths: set[str] = set()
    for field in document_fields:
        if field not in dependencies:
            raise SearchProjectionError.missing_field(field)
        paths.update(dependencies[field])
    # Invalid declared sources remain errors even when the index is empty or a
    # particular optional filter was not requested.
    validate_source_paths(
        manager_class, (path for values in dependencies.values() for path in values)
    )
    return tuple(sorted(paths))


def projection_filter_fields(
    manager_class: type[GeneralManager],
    index_config: IndexConfig | None,
    lookups: Iterable[str],
) -> set[str]:
    """Resolve document filters with the existing scalar/identity grammar.

    Identity contracts can name fields containing ``__``. Ordinary adapter
    filters still split at the first separator; do not invent nested grammar.
    """
    allowed = set(index_config.filters) if index_config is not None else set()
    identities = (
        get_search_identity_fields(manager_class, index_config)
        if index_config is not None
        else {}
    )
    fields: set[str] = set()
    for key in lookups:
        identity = identity_lookup(key, identities)
        if identity is not None and identity[0] in allowed:
            fields.add(identity[0])
            continue
        field, separator, operator = key.partition("__")
        if field not in allowed or (separator and operator not in get_args(LookupName)):
            raise SearchProjectionError.invalid_filter(key)
        fields.add(field)
    return fields


def validate_projection_fallback(config: SearchConfigSpec, field: str) -> None:
    """Prevent an opted-in projection from reading an undeclared native fallback."""
    dependencies = config.field_dependencies
    if dependencies is not None and field not in dependencies.get(field, ()):
        raise SearchProjectionError.undeclared_fallback(field)
