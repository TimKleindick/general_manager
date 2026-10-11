"""Explicit native identity provenance for search documents and filters."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Annotated, cast, get_args, get_origin
from uuid import UUID

from graphql import GraphQLError, GraphQLID

from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.search.config import IndexConfig

SearchFilters = Mapping[str, object] | Sequence[Mapping[str, object]] | None
NATIVE_IDENTITY_TYPES = (int, str, UUID)
IDENTITY_TRANSPORT_VERSION = 1


class SearchIdentityConfigurationError(ValueError):
    """Raised when an explicit search identity declaration is invalid."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"Invalid search identity field {name!r}: {reason}")


class SearchIdentityFilterGroup(dict[str, object]):
    """A native-valued AND group with explicit identity and owner metadata.

    Existing mapping consumers see the original keys and values. Builtin
    adapters honor the metadata for exact/in identity comparisons. ``type_label``
    scopes a group when multiple manager contracts share an index.
    """

    def __init__(
        self,
        values: Mapping[str, object],
        *,
        identity_fields: Mapping[str, type],
        type_label: str | None = None,
    ) -> None:
        super().__init__(values)
        for name, native_type in identity_fields.items():
            if native_type not in NATIVE_IDENTITY_TYPES:
                raise SearchIdentityConfigurationError(
                    name, "expected int, str or UUID"
                )
        self.identity_fields = dict(identity_fields)
        self.type_label = type_label


def identity_lookup(
    key: str, identity_fields: Mapping[str, type]
) -> tuple[str, str, type] | None:
    """Resolve only declared identity equality/membership lookup keys."""
    if key in identity_fields:
        return key, "exact", identity_fields[key]
    field_name, separator, lookup = key.rpartition("__")
    if separator and lookup in {"exact", "in"} and field_name in identity_fields:
        return field_name, lookup, identity_fields[field_name]
    return None


def canonical_identity(native_type: type, value: object) -> object:
    """Use the existing ID validation and native Input conversion contract."""
    if value is None:
        return None
    if not isinstance(value, UUID):
        GraphQLID.parse_value(value)
    return Input(native_type).cast(value)


def identity_token(native_type: type, value: object) -> str:
    """Bounded versioned equality token immune to numeric/collation coercion."""
    canonical = canonical_identity(native_type, value)
    payload = json.dumps(
        [native_type.__name__, None if canonical is None else str(canonical)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        f"gm_identity_v{IDENTITY_TRANSPORT_VERSION}_"
        + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    )


def identity_field_key(name: str) -> str:
    """Use a stable field key without provider path punctuation."""
    return "f_" + hashlib.sha256(name.encode("utf-8")).hexdigest()


def _related_manager(annotation: object) -> type[GeneralManager] | None:
    if isinstance(annotation, type) and issubclass(annotation, GeneralManager):
        return annotation
    if get_origin(annotation) is Annotated:
        return _related_manager(get_args(annotation)[0])
    for argument in get_args(annotation):
        related = _related_manager(argument)
        if related is not None:
            return related
    return None


def _declared_identity_type(
    manager_class: type[GeneralManager], name: str
) -> type | None:
    """Resolve explicit terminal metadata, including configured relation paths."""
    current = manager_class
    parts = name.split("__")
    for position, part in enumerate(parts):
        interface = current.Interface
        prop = interface.get_graph_ql_properties().get(part)
        metadata: Mapping[str, object] = interface.get_attribute_types().get(part, {})
        annotation = (
            prop.graphql_type_hint if prop is not None else metadata.get("type")
        )
        if position < len(parts) - 1:
            related = _related_manager(annotation)
            if related is None:
                return None
            current = related
            continue
        if prop is not None and prop.is_identifier:
            # Import after interface/GraphQL startup to avoid a bootstrap cycle.
            from general_manager.api.graphql_identifiers import property_identifier_type

            native_type, _ = property_identifier_type(
                annotation, owner_name=current.__name__, field_name=part
            )
            return native_type
        if metadata.get("is_identifier") and annotation in NATIVE_IDENTITY_TYPES:
            return cast(type, annotation)
    return None


def get_search_identity_fields(
    manager_class: type[GeneralManager], index_config: IndexConfig
) -> dict[str, type]:
    """Collect native identity declarations for serialized configured fields."""
    fields = (
        *(field.name for field in index_config.iter_fields()),
        *index_config.filters,
        *index_config.sorts,
    )
    identities: dict[str, type] = {}
    for name in fields:
        declared = _declared_identity_type(manager_class, name)
        configured = index_config.identity_fields.get(name)
        if (
            declared is not None
            and configured is not None
            and declared is not configured
        ):
            raise SearchIdentityConfigurationError(
                name, "conflicts with manager metadata"
            )
        native_type = declared or configured
        if native_type is not None:
            identities[name] = native_type
    return identities


def build_search_filters(
    manager_class: type[GeneralManager], index_name: str, filters: SearchFilters
) -> SearchFilters:
    """Bind explicit manager/index provenance while keeping filter values native."""
    from general_manager.search.registry import get_index_config, get_type_label

    index_config = get_index_config(manager_class, index_name)
    if not filters or index_config is None:
        return filters
    identities = get_search_identity_fields(manager_class, index_config)
    groups = [filters] if isinstance(filters, Mapping) else filters
    bound = [
        SearchIdentityFilterGroup(
            group, identity_fields=identities, type_label=get_type_label(manager_class)
        )
        for group in groups
    ]
    return bound[0] if isinstance(filters, Mapping) else bound


def bind_search_filters(
    index_name: str, filters: SearchFilters, types: Sequence[str] | None
) -> SearchFilters:
    """Bind plain filters to registered contracts; explicit groups work cold.

    Only identity lookups activate binding. Shared configured indexes expand
    into manager-scoped OR alternatives so a business field in one manager
    never inherits another manager's identity semantics.
    """
    if not filters:
        return filters
    groups = [filters] if isinstance(filters, Mapping) else filters
    if not all(isinstance(group, Mapping) for group in groups):
        return filters
    if all(isinstance(group, SearchIdentityFilterGroup) for group in groups):
        return filters
    from general_manager.search.registry import get_type_label, iter_index_configs

    managers = [
        (manager, config)
        for manager, config in iter_index_configs(index_name)
        if not types or get_type_label(manager) in types
    ]
    if not any(
        identity_lookup(key, get_search_identity_fields(manager, config))
        for manager, config in managers
        for group in groups
        if not isinstance(group, SearchIdentityFilterGroup)
        for key in group
    ):
        return filters
    bound: list[Mapping[str, object]] = []
    for group in groups:
        if isinstance(group, SearchIdentityFilterGroup):
            bound.append(group)
            continue
        if not any(
            identity_lookup(key, get_search_identity_fields(manager, config))
            for manager, config in managers
            for key in group
        ):
            bound.append(group)
            continue
        for manager, _config in managers:
            prepared = build_search_filters(manager, index_name, group)
            if isinstance(prepared, SearchIdentityFilterGroup):
                for key, value in tuple(prepared.items()):
                    identity = identity_lookup(key, prepared.identity_fields)
                    if identity is None:
                        continue
                    _name, _lookup, native_type = identity
                    candidates = (
                        value if isinstance(value, (list, tuple, set)) else [value]
                    )
                    compatible: list[object] = []
                    for candidate in candidates:
                        try:
                            canonical_identity(native_type, candidate)
                        except (ValueError, TypeError, AttributeError, GraphQLError):
                            continue
                        compatible.append(candidate)
                    if len(compatible) == len(candidates):
                        continue
                    # A shared spelling can have different native kinds. Keep
                    # valid membership alternatives; all-invalid criteria must
                    # remain false rather than disappearing from the query.
                    if isinstance(value, (list, tuple, set)):
                        prepared[key] = type(value)(compatible)
                    else:
                        prepared[key] = []
            if isinstance(prepared, Mapping):
                bound.append(prepared)
    return bound


def meilisearch_identity_filters(
    group: Mapping[str, object],
) -> list[tuple[str, object]]:
    """Render identity operands into their separate Meilisearch token fields."""
    if not isinstance(group, SearchIdentityFilterGroup):
        return list(group.items())
    # Different lookup keys may target the same shadow field. Preserve every
    # criterion as a separate AND term instead of overwriting dictionary keys.
    result: list[tuple[str, object]] = []
    for key, value in group.items():
        identity = identity_lookup(key, group.identity_fields)
        if identity is None:
            result.append((key, value))
            continue
        name, _lookup, native_type = identity
        token_key = "gm_identity." + identity_field_key(name)
        # Preserve Meilisearch's scalar `in` equality grammar through tokens,
        # never through a raw numeric fallback.
        if isinstance(value, (list, tuple, set)):
            result.append(
                (
                    token_key + "__in",
                    [identity_token(native_type, item) for item in value],
                )
            )
        else:
            result.append((token_key, identity_token(native_type, value)))
    return result
