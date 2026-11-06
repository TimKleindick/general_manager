"""Authorize query operands before they influence membership or ordering."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, cast

from graphql import GraphQLError

from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.request_bucket import RequestBucket
from general_manager.manager.general_manager import GeneralManager

if TYPE_CHECKING:
    from graphene import ResolveInfo


class QueryFieldPermissionError(GraphQLError):
    """A requested operand is not authorized on the candidate scope."""

    def __init__(self, field: str) -> None:
        super().__init__(f"Permission denied to use query field '{field}'.")


_LIMIT_ERROR = "Query field authorization candidate limit exceeded."
_AMBIGUOUS_ERROR = "Cannot authorize ambiguous query relation."
_RELATION_ERROR = "Permission denied to traverse query relation."
INCOMPLETE_ERROR = "Query field authorization requires a complete candidate set."
CUSTOM_PROJECTION_ERROR = (
    "Cannot authorize fields of a custom search document projection."
)

# Bound field/path decisions; bucket materialization and row checks precede this.
MAX_QUERY_PERMISSION_CANDIDATES = 1000
_LOOKUPS = {
    "exact",
    "iexact",
    "in",
    "isnull",
    "lt",
    "lte",
    "gt",
    "gte",
    "contains",
    "icontains",
    "startswith",
    "istartswith",
    "endswith",
    "iendswith",
    "range",
}


def authorize_query_fields(
    candidates: Iterable[GeneralManager],
    info: ResolveInfo,
    paths: Iterable[str],
    *,
    native_paths: bool = False,
) -> None:
    """Check all operands against every row-visible candidate, never just hits.

    Related rows require their own row permission as well as field permission.
    Failure leaves the original candidate set unchanged and rejects the query.
    ``native_paths`` checks exact projection sources without backend aliases or
    lookup operators. Callers validate those paths statically before iteration.
    """
    from general_manager.api.graphql_relations import (
        resolve_general_manager_type,
        get_graphql_manager_registry,
    )
    from general_manager.api.graphql_resolvers import (
        can_read_instance,
        check_read_permission,
        relation_id_alias_field,
    )

    normalized = set()
    for path in paths:
        parts = path.split("__")
        if parts:
            normalized.add(tuple(parts))
    if not normalized:
        return
    decisions = 0

    def check(instance: GeneralManager, parts: tuple[str, ...]) -> None:
        """Authorize one exact operand path, charging each visited field decision."""
        nonlocal decisions
        decisions += 1
        if decisions > MAX_QUERY_PERMISSION_CANDIDATES:
            raise GraphQLError(_LIMIT_ERROR)
        field, *rest = parts
        attributes = instance.Interface.get_attribute_types()
        # Backend collection lookup names can differ from public manager names.
        aliases = (
            []
            if native_paths
            else [
                name
                for name, metadata in attributes.items()
                if metadata.get("filter_lookup", name) == field
            ]
        )
        names = set(aliases or [field])
        native_relations = set()
        for name in tuple(names):
            relation = relation_id_alias_field(
                type(instance), name, native_source=native_paths
            )
            if not native_paths and relation is None and name.endswith("_id"):
                alias = name.removesuffix("_id")
                metadata: dict[str, object] = dict(attributes.get(alias, {}))
                if (
                    resolve_general_manager_type(
                        metadata.get("type"), get_graphql_manager_registry()
                    )
                    is not None
                ):
                    relation = alias
            if relation is not None:
                names.add(relation)
                if native_paths:
                    native_relations.add(relation)
        known_fields = set(attributes) | set(instance.identification)
        known_fields.update(instance.Interface.get_graph_ql_properties())
        known_fields.update(instance.Interface.get_attributes())
        known_fields.update(instance.Interface.input_fields)
        if not names.intersection(known_fields):
            raise QueryFieldPermissionError(field)
        if any(not check_read_permission(instance, info, name) for name in names):
            raise QueryFieldPermissionError(field)
        for relation in sorted(native_relations):
            # An ORM attname is a scalar spelling of a related field. Give it
            # the same target row/leaf gates as an explicit relation path.
            value = getattr(instance, relation)
            if value is None:
                continue
            related_type = resolve_general_manager_type(
                attributes[relation].get("type"), get_graphql_manager_registry()
            )
            if (
                not isinstance(value, GeneralManager)
                or related_type is None
                or not isinstance(value, related_type)
                or not can_read_instance(value, info)
            ):
                raise GraphQLError(_RELATION_ERROR)
            from django.db import models
            from general_manager.interface.orm_interface import OrmInterfaceBase

            orm_interface = cast(
                type[OrmInterfaceBase[models.Model]], instance.Interface
            )
            foreign_key = orm_interface._model._meta.get_field(relation)
            target_field = getattr(foreign_key, "target_field", None)
            if not isinstance(target_field, models.Field):
                raise GraphQLError(_RELATION_ERROR)
            check(value, ("id" if target_field.primary_key else target_field.name,))
        if not rest:
            return
        if len(aliases) > 1:
            raise GraphQLError(_AMBIGUOUS_ERROR)
        field_metadata: dict[str, object] = dict(
            attributes.get(aliases[0] if aliases else field, {})
        )
        if native_paths:
            from general_manager.search.projection import source_field_annotation

            _, annotation = source_field_annotation(type(instance), field)
            field_metadata["type"] = annotation
        if (
            not native_paths
            and len(rest) == 1
            and rest[0] in _LOOKUPS
            and (
                (field.endswith("_id") and len(names) > 1)
                or (
                    field_metadata.get("type") is not None
                    and resolve_general_manager_type(
                        field_metadata.get("type"), get_graphql_manager_registry()
                    )
                    is None
                    and field_metadata.get("relation_kind")
                    not in {"direct", "collection"}
                )
            )
        ):
            return
        related_type = resolve_general_manager_type(
            field_metadata.get("type"), get_graphql_manager_registry()
        )
        if (
            not native_paths
            and len(rest) == 1
            and rest[0] in _LOOKUPS
            and related_type is not None
        ):
            related_fields = set(related_type.Interface.get_attribute_types())
            related_fields.update(related_type.Interface.get_attributes())
            related_fields.update(related_type.Interface.get_graph_ql_properties())
            if rest[0] not in related_fields:
                return
        value = getattr(instance, aliases[0] if aliases else field)
        if value is None:
            return
        related = value if isinstance(value, (Bucket, list, tuple, set)) else [value]
        for member in related:
            if not isinstance(member, GeneralManager):
                # A lookup is an operator only after resolving its preceding
                # scalar field. A relation's field named "contains" is a field.
                if not native_paths and len(rest) == 1 and rest[0] in _LOOKUPS:
                    continue
                raise GraphQLError(_RELATION_ERROR)
            if (
                native_paths
                and related_type is not None
                and not isinstance(member, related_type)
            ):
                raise GraphQLError(_RELATION_ERROR)
            if not can_read_instance(member, info):
                raise GraphQLError(_RELATION_ERROR)
            check(member, tuple(rest))
        if (
            native_paths
            and isinstance(related, RequestBucket)
            and not related.response_is_complete
        ):
            raise GraphQLError(INCOMPLETE_ERROR)

    for candidate in candidates:
        for normalized_parts in sorted(normalized):
            check(candidate, normalized_parts)
    if isinstance(candidates, RequestBucket) and not candidates.response_is_complete:
        raise GraphQLError(INCOMPLETE_ERROR)
