"""Static search projection analysis; never cache user or object decisions."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping

from general_manager.manager.general_manager import GeneralManager
from general_manager.search.config import IndexConfig, SearchConfigSpec
from general_manager.search.projection import (
    SearchProjectionError,
    projection_source_paths,
    native_search_source_paths,
    source_field_annotation,
)


@dataclass(frozen=True)
class SearchProjectionAuthorization:
    """Schema-lifetime classification of one manager/index, with native sources."""

    fast: bool
    paths: tuple[str, ...]
    reasons: tuple[str, ...]
    error: str | None = None
    has_relations: bool = False
    document_fields: frozenset[str] = frozenset()


def analyze_search_projection(
    manager: type[GeneralManager],
    config: SearchConfigSpec,
    index: IndexConfig,
    registry: Mapping[str, type[GeneralManager]],
) -> SearchProjectionAuthorization:
    """Inspect definitions once, without evaluating a permission or property."""
    from general_manager.api.graphql_relations import resolve_general_manager_type
    from general_manager.api.graphql_resolvers import relation_id_alias_field
    from general_manager.permission.manager_based_permission import (
        additional_local_read_permission,
    )

    document_fields = {
        *(field.name for field in index.iter_fields()),
        *index.filters,
        *index.sorts,
    }
    reasons: set[str] = set()
    has_relations = False

    def inspect_permission(
        owner: type[GeneralManager],
        field: str,
        visited: frozenset[type[GeneralManager]],
    ) -> None:
        """Collect reasons delegated or custom field policies require runtime checks."""
        if owner in visited:
            reasons.add("cyclic delegated permission")
            return
        permission = getattr(owner, "Permission", None)
        if additional_local_read_permission(permission, field) is not False:
            reasons.add("additional or custom field permission")
        based_on = getattr(permission, "__based_on__", None)
        if based_on is not None:
            known, annotation = source_field_annotation(owner, based_on)
            target = resolve_general_manager_type(annotation, registry)
            if not known or target is None:
                reasons.add("unknown delegated permission")
            else:
                inspect_permission(target, field, visited | {owner})

    try:
        if config.to_document is not None:
            paths = projection_source_paths(manager, config, document_fields)
        else:
            paths = native_search_source_paths(manager, config, document_fields)
    except SearchProjectionError as exc:
        return SearchProjectionAuthorization(
            False, (), ("unknown dependencies",), str(exc)
        )

    for path in paths:
        owner = manager
        for field in path.split("__"):
            inspect_permission(owner, field, frozenset())
            alias = relation_id_alias_field(owner, field, native_source=True)
            if alias is not None:
                inspect_permission(owner, alias, frozenset())
                has_relations = True
                # The alias target leaf is checked at runtime; retain the
                # thorough path until its complete field policy is known.
                reasons.add("relation identity alias")
            _, annotation = source_field_annotation(owner, field)
            target = resolve_general_manager_type(annotation, registry)
            if target is not None:
                has_relations = True
                owner = target
            elif (
                field in owner.Interface.get_graph_ql_properties()
                and config.field_dependencies is None
            ):
                reasons.add("unknown property dependencies")
    return SearchProjectionAuthorization(
        not reasons,
        paths,
        tuple(sorted(reasons)),
        has_relations=has_relations,
        document_fields=frozenset(document_fields),
    )
