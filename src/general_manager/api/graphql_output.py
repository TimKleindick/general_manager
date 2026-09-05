"""Strict annotation mapping and resolvers for GraphQL output values."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
import sys
from types import UnionType
from typing import Any, ForwardRef, Union, get_args, get_origin, get_type_hints

import graphene

from general_manager.api.graphql_resolvers import resolve_measurement_output
from general_manager.api.graphql_type import GraphQLType
from general_manager.manager.general_manager import GeneralManager
from general_manager.measurement.measurement import Measurement
from general_manager.utils.type_checks import safe_issubclass


@dataclass(frozen=True, slots=True)
class MappedGraphQLOutput:
    """Graphene field metadata and the Python value type for its resolver."""

    field: object
    resolver_type: object


class GraphQLOutputAnnotationError(TypeError):
    """Raised when an output declaration uses an unsupported annotation shape."""

    def __init__(self, owner_name: str, field_name: str, annotation: object) -> None:
        self.owner_name = owner_name
        self.field_name = field_name
        self.annotation = annotation
        super().__init__(
            f"Unsupported GraphQL field type for "
            f"{owner_name}.{field_name}: {annotation!r}"
        )


@dataclass(frozen=True, slots=True)
class _MappedAnnotation:
    """Internal Graphene type metadata before a field wrapper is mounted."""

    graphene_type: object
    resolver_type: object
    nullable: bool
    contains_measurement: bool


def _annotation_error(
    owner_name: str,
    field_name: str,
    annotation: object,
) -> GraphQLOutputAnnotationError:
    return GraphQLOutputAnnotationError(owner_name, field_name, annotation)


def _is_union(annotation: object) -> bool:
    origin = get_origin(annotation)
    return origin in (Union, UnionType)


def _unwrap_optional_annotation(
    annotation: object,
    *,
    owner_name: str,
    field_name: str,
) -> tuple[bool, object]:
    """Return nullable status and the sole non-``None`` union member."""
    if _is_union(annotation):
        members = get_args(annotation)
        if len(members) == 2 and type(None) in members:
            concrete = next(member for member in members if member is not type(None))
            return True, concrete
        raise _annotation_error(owner_name, field_name, annotation)
    if annotation is None or annotation is type(None):
        raise _annotation_error(owner_name, field_name, annotation)
    return False, annotation


def _registry_name_for_class(
    annotation: object,
    registry: Mapping[str, type[object]],
) -> str | None:
    """Find the live registry key for a declared class without copying it."""
    if not isinstance(annotation, type):
        return None
    class_name = annotation.__name__
    if registry.get(class_name) is annotation:
        return class_name
    for name, registered in registry.items():
        if registered is annotation:
            return name
    return None


def _resolve_registered_name(
    annotation: object,
    *,
    owner_name: str,
    field_name: str,
    manager_registry: Mapping[str, type[GeneralManager]],
    output_class_registry: Mapping[str, type[GraphQLType]],
) -> object:
    """Resolve a simple forward-reference name through the live registries."""
    if isinstance(annotation, ForwardRef):
        annotation = annotation.__forward_arg__
    if not isinstance(annotation, str):
        return annotation

    name = annotation.strip()
    if len(name) >= 2 and name[0] == name[-1] and name[0] in {"'", '"'}:
        name = name[1:-1].strip()
    manager_type = manager_registry.get(name)
    output_type = output_class_registry.get(name)
    if manager_type is not None and output_type is not None:
        raise _annotation_error(owner_name, field_name, annotation)
    if manager_type is not None:
        return manager_type
    if output_type is not None:
        return output_type
    return annotation


def _lazy_registry_type(
    registry: Mapping[str, type[graphene.ObjectType]],
    name: str,
) -> Callable[[], type[graphene.ObjectType]]:
    """Return a Graphene thunk that reads a generated registry at resolution time."""
    return lambda: registry[name]


def _map_collection_annotation(
    annotation: object,
    *,
    owner_name: str,
    field_name: str,
    manager_registry: Mapping[str, type[GeneralManager]],
    manager_type_registry: Mapping[str, type[graphene.ObjectType]],
    output_class_registry: Mapping[str, type[GraphQLType]],
    output_type_registry: Mapping[str, type[graphene.ObjectType]],
    measurement_type: type[graphene.ObjectType],
    scalar_mapper: Callable[[type], type],
) -> _MappedAnnotation:
    origin = get_origin(annotation)
    if origin not in (list, tuple, set):
        raise _annotation_error(owner_name, field_name, annotation)

    args = get_args(annotation)
    if origin is tuple:
        if len(args) != 2 or args[1] is not Ellipsis:
            raise _annotation_error(owner_name, field_name, annotation)
        element_annotation = args[0]
    else:
        if len(args) != 1:
            raise _annotation_error(owner_name, field_name, annotation)
        element_annotation = args[0]

    element = _map_annotation(
        element_annotation,
        owner_name=owner_name,
        field_name=field_name,
        manager_registry=manager_registry,
        manager_type_registry=manager_type_registry,
        output_class_registry=output_class_registry,
        output_type_registry=output_type_registry,
        measurement_type=measurement_type,
        scalar_mapper=scalar_mapper,
    )
    element_type = element.graphene_type
    if not element.nullable:
        element_type = graphene.NonNull(element_type)
    return _MappedAnnotation(
        graphene.List(element_type),
        element.resolver_type,
        nullable=False,
        contains_measurement=element.contains_measurement,
    )


def _map_non_optional_value(
    annotation: object,
    *,
    owner_name: str,
    field_name: str,
    manager_registry: Mapping[str, type[GeneralManager]],
    manager_type_registry: Mapping[str, type[graphene.ObjectType]],
    output_class_registry: Mapping[str, type[GraphQLType]],
    output_type_registry: Mapping[str, type[graphene.ObjectType]],
    measurement_type: type[graphene.ObjectType],
    scalar_mapper: Callable[[type], type],
) -> _MappedAnnotation:
    annotation = _resolve_registered_name(
        annotation,
        owner_name=owner_name,
        field_name=field_name,
        manager_registry=manager_registry,
        output_class_registry=output_class_registry,
    )
    if annotation is Any:
        raise _annotation_error(owner_name, field_name, annotation)

    if annotation in (list, tuple, set):
        raise _annotation_error(owner_name, field_name, annotation)

    origin = get_origin(annotation)
    if origin is not None:
        if _is_union(annotation):
            raise _annotation_error(owner_name, field_name, annotation)
        return _map_collection_annotation(
            annotation,
            owner_name=owner_name,
            field_name=field_name,
            manager_registry=manager_registry,
            manager_type_registry=manager_type_registry,
            output_class_registry=output_class_registry,
            output_type_registry=output_type_registry,
            measurement_type=measurement_type,
            scalar_mapper=scalar_mapper,
        )

    if safe_issubclass(annotation, GraphQLType):
        output_name = _registry_name_for_class(annotation, output_class_registry)
        if output_name is None:
            raise _annotation_error(owner_name, field_name, annotation)
        return _MappedAnnotation(
            _lazy_registry_type(output_type_registry, output_name),
            annotation,
            nullable=False,
            contains_measurement=False,
        )

    if safe_issubclass(annotation, GeneralManager):
        manager_name = _registry_name_for_class(annotation, manager_registry)
        if manager_name is None:
            raise _annotation_error(owner_name, field_name, annotation)
        return _MappedAnnotation(
            _lazy_registry_type(manager_type_registry, manager_name),
            annotation,
            nullable=False,
            contains_measurement=False,
        )

    if safe_issubclass(annotation, Measurement):
        return _MappedAnnotation(
            measurement_type,
            annotation,
            nullable=False,
            contains_measurement=True,
        )

    if not isinstance(annotation, type):
        raise _annotation_error(owner_name, field_name, annotation)

    # Keep this allowlist explicit.  The legacy mapper intentionally falls back
    # to String for unknown classes, which is unsafe for output declarations.
    if safe_issubclass(annotation, bool):
        scalar_type = scalar_mapper(annotation)
    elif safe_issubclass(annotation, str):
        scalar_type = scalar_mapper(annotation)
    elif safe_issubclass(annotation, int):
        scalar_type = scalar_mapper(annotation)
    elif safe_issubclass(annotation, (float, Decimal)):
        scalar_type = scalar_mapper(annotation)
    elif safe_issubclass(annotation, datetime):
        scalar_type = scalar_mapper(annotation)
    elif safe_issubclass(annotation, date):
        scalar_type = scalar_mapper(annotation)
    else:
        raise _annotation_error(owner_name, field_name, annotation)
    return _MappedAnnotation(
        scalar_type,
        annotation,
        nullable=False,
        contains_measurement=False,
    )


def _map_annotation(
    annotation: object,
    *,
    owner_name: str,
    field_name: str,
    manager_registry: Mapping[str, type[GeneralManager]],
    manager_type_registry: Mapping[str, type[graphene.ObjectType]],
    output_class_registry: Mapping[str, type[GraphQLType]],
    output_type_registry: Mapping[str, type[graphene.ObjectType]],
    measurement_type: type[graphene.ObjectType],
    scalar_mapper: Callable[[type], type],
) -> _MappedAnnotation:
    nullable, concrete = _unwrap_optional_annotation(
        annotation,
        owner_name=owner_name,
        field_name=field_name,
    )
    mapped = _map_non_optional_value(
        concrete,
        owner_name=owner_name,
        field_name=field_name,
        manager_registry=manager_registry,
        manager_type_registry=manager_type_registry,
        output_class_registry=output_class_registry,
        output_type_registry=output_type_registry,
        measurement_type=measurement_type,
        scalar_mapper=scalar_mapper,
    )
    return replace(mapped, nullable=nullable)


def _map_non_optional_annotation(
    annotation: object,
    *,
    owner_name: str,
    field_name: str,
    manager_registry: Mapping[str, type[GeneralManager]],
    manager_type_registry: Mapping[str, type[graphene.ObjectType]],
    output_class_registry: Mapping[str, type[GraphQLType]],
    output_type_registry: Mapping[str, type[graphene.ObjectType]],
    measurement_type: type[graphene.ObjectType],
    scalar_mapper: Callable[[type], type],
    required: bool,
) -> MappedGraphQLOutput:
    """Map one non-optional annotation to a Graphene field."""
    mapped = _map_non_optional_value(
        annotation,
        owner_name=owner_name,
        field_name=field_name,
        manager_registry=manager_registry,
        manager_type_registry=manager_type_registry,
        output_class_registry=output_class_registry,
        output_type_registry=output_type_registry,
        measurement_type=measurement_type,
        scalar_mapper=scalar_mapper,
    )
    field_kwargs: dict[str, object] = {}
    if mapped.contains_measurement:
        field_kwargs["target_unit"] = graphene.String()
    return MappedGraphQLOutput(
        graphene.Field(mapped.graphene_type, required=required, **field_kwargs),
        mapped.resolver_type,
    )


def map_graphql_output_annotation(
    annotation: object,
    *,
    owner_name: str,
    field_name: str,
    manager_registry: Mapping[str, type[GeneralManager]],
    manager_type_registry: Mapping[str, type[graphene.ObjectType]],
    output_class_registry: Mapping[str, type[GraphQLType]],
    output_type_registry: Mapping[str, type[graphene.ObjectType]],
    measurement_type: type[graphene.ObjectType],
    scalar_mapper: Callable[[type], type],
) -> MappedGraphQLOutput:
    """Map a strict GraphQL output annotation to a mounted Graphene field."""
    nullable, concrete_type = _unwrap_optional_annotation(
        annotation,
        owner_name=owner_name,
        field_name=field_name,
    )
    try:
        mapped = _map_non_optional_annotation(
            concrete_type,
            owner_name=owner_name,
            field_name=field_name,
            manager_registry=manager_registry,
            manager_type_registry=manager_type_registry,
            output_class_registry=output_class_registry,
            output_type_registry=output_type_registry,
            measurement_type=measurement_type,
            scalar_mapper=scalar_mapper,
            required=not nullable,
        )
    except GraphQLOutputAnnotationError as error:
        # Preserve the annotation written on the owning field in diagnostics,
        # even when an invalid collection element was the first failure.
        if error.annotation is not annotation:
            raise _annotation_error(owner_name, field_name, annotation) from error
        raise
    return mapped


def resolve_output_type_hints(
    owner: type[object] | Callable[..., object],
    *,
    manager_registry: Mapping[str, type[GeneralManager]],
    output_class_registry: Mapping[str, type[GraphQLType]],
) -> dict[str, object]:
    """Resolve output annotations using the live manager/output declarations."""
    owner_name = getattr(owner, "__name__", type(owner).__name__)
    module = sys.modules.get(getattr(owner, "__module__", ""))
    globalns = vars(module) if module is not None else {}
    localns: dict[str, object] = {
        **manager_registry,
        **output_class_registry,
    }
    localns.setdefault(owner_name, owner)
    try:
        return get_type_hints(
            owner,
            globalns=globalns,
            localns=localns,
            include_extras=True,
        )
    except (AttributeError, KeyError, NameError, TypeError, ValueError):
        raw_annotations: dict[str, object] = {}
        if isinstance(owner, type):
            for base in reversed(owner.__mro__):
                raw_annotations.update(getattr(base, "__annotations__", {}))
        else:
            raw_annotations.update(getattr(owner, "__annotations__", {}))
        if not raw_annotations:
            raise _annotation_error(owner_name, "annotations", owner) from None

        resolved: dict[str, object] = {}
        for field_name, annotation in raw_annotations.items():
            holder = type(
                "_GraphQLOutputAnnotation",
                (),
                {"__annotations__": {"value": annotation}},
            )
            try:
                resolved[field_name] = get_type_hints(
                    holder,
                    globalns=globalns,
                    localns=localns,
                    include_extras=True,
                )["value"]
            except (
                AttributeError,
                KeyError,
                NameError,
                TypeError,
                ValueError,
            ) as error:
                raise _annotation_error(owner_name, field_name, annotation) from error
        return resolved


def _resolve_output_measurement(
    value: object,
    target_unit: str | None,
) -> object:
    return resolve_measurement_output(value, target_unit)


def create_output_field_resolver(
    field_name: str,
    resolver_type: object | None = None,
) -> Callable[..., object]:
    """Build a resolver for a field on a materialized GraphQL output value.

    Unlike manager resolvers this function never checks permissions or
    historical context.  Measurement values share the manager conversion
    helper so ``target_unit`` behaves identically in both resolver paths.
    """

    def resolver(
        parent: object,
        info: object | None = None,
        target_unit: str | None = None,
        **kwargs: object,
    ) -> object:
        del info, kwargs
        value = getattr(parent, field_name)
        if safe_issubclass(resolver_type, Measurement) or isinstance(
            value, Measurement
        ):
            return _resolve_output_measurement(value, target_unit)
        return value

    return resolver
