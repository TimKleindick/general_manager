"""Authorize query operands before they influence membership or ordering."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from inspect import getattr_static
from typing import TYPE_CHECKING, cast

from graphql import GraphQLError

from general_manager.bucket.base_bucket import Bucket
from general_manager.bucket.request_bucket import RequestBucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta

if TYPE_CHECKING:
    from graphene import ResolveInfo
    from general_manager.interface.base_interface import (
        AttributeTypedDict,
        InterfaceBase,
    )


class QueryFieldPermissionError(GraphQLError):
    """A requested operand is not authorized on the candidate scope."""

    def __init__(self, field: str) -> None:
        super().__init__(f"Permission denied to use query field '{field}'.")


_LIMIT_ERROR = "Query field authorization candidate limit exceeded."
_AMBIGUOUS_ERROR = "Cannot authorize ambiguous query relation."
_RELATION_ERROR = "Permission denied to traverse query relation."


class QueryRelationPermissionError(GraphQLError):
    """A search candidate contains a related row the actor cannot read."""

    def __init__(self) -> None:
        super().__init__(_RELATION_ERROR)


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


@dataclass(slots=True)
class _NativePathNode:
    """One shared native source prefix, without storing permission decisions."""

    children: dict[str, _NativePathNode] = field(default_factory=dict)


@dataclass(slots=True)
class QueryFieldAuthorizationBudget:
    """Share only accounting across authorization calls in one operation."""

    decisions: int = 0

    def charge(self) -> None:
        """Reject work exceeding the operation's field/path decision limit."""
        self.decisions += 1
        if self.decisions > MAX_QUERY_PERMISSION_CANDIDATES:
            raise GraphQLError(_LIMIT_ERROR)


def _operand_field_names(
    manager: type[GeneralManager],
    field_name: str,
    attributes: dict[str, AttributeTypedDict],
    *,
    native_paths: bool,
) -> tuple[list[str], set[str], set[str]]:
    """Resolve the existing field/relationship spellings for one operand."""
    from general_manager.api.graphql_relations import (
        get_graphql_manager_registry,
        resolve_general_manager_type,
    )
    from general_manager.api.graphql_resolvers import relation_id_alias_field

    aliases = (
        []
        if native_paths
        else [
            name
            for name, metadata in attributes.items()
            if metadata.get("filter_lookup", name) == field_name
        ]
    )
    names = set(aliases or [field_name])
    native_relations: set[str] = set()
    for name in tuple(names):
        relation = relation_id_alias_field(manager, name, native_source=native_paths)
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
    return aliases, names, native_relations


def _unchanged_metadata_methods(
    owner: object, bindings: Iterable[tuple[str, object, object]]
) -> bool:
    """Check the actual metadata owners, including instance and code overrides."""
    from general_manager.utils.executable_contract import executable_matches

    for name, original, signature in bindings:
        if getattr_static(owner, name, None) is not original:
            return False
        executable = (
            original.__func__ if isinstance(original, classmethod) else original
        )
        if not executable_matches(executable, signature):
            return False
    return True


@dataclass(frozen=True, slots=True)
class _FlatQueryField:
    """Structure only: runtime decisions still run in the original order."""

    names: tuple[str, ...]
    additional: bool


@dataclass(slots=True)
class _NativeQueryStructure:
    """One call's stable standard ORM schema and declarative policy structure."""

    manager: type[GeneralManager]
    interface: type[InterfaceBase]
    permission: object
    attributes: dict[str, AttributeTypedDict]
    properties: frozenset[str]
    fields: dict[tuple[str, ...], _FlatQueryField | None] = field(default_factory=dict)
    additional: dict[str, bool | None] = field(default_factory=dict)

    def flat_field(self, parts: tuple[str, ...]) -> _FlatQueryField | None:
        """Prepare only a visited, known flat operand; keep general paths dynamic."""
        if parts in self.fields:
            return self.fields[parts]
        from general_manager.api.graphql_relations import (
            get_graphql_manager_registry,
            resolve_general_manager_type,
        )
        from general_manager.permission.manager_based_permission import (
            additional_local_read_permission,
        )

        self.fields[parts] = None
        field_name, *rest = parts
        if len(rest) > 1 or (rest and rest[0] not in _LOOKUPS):
            return None
        aliases, names, _ = _operand_field_names(
            self.manager, field_name, self.attributes, native_paths=False
        )
        if (
            len(aliases) > 1
            or not names.intersection(self.attributes)
            or names.intersection(self.properties)
        ):
            return None
        metadata: dict[str, object] = dict(
            self.attributes.get(aliases[0] if aliases else field_name, {})
        )
        annotation = metadata.get("type")
        identity_alias = field_name.endswith("_id") and len(names) > 1
        if not identity_alias and (
            type(annotation) is not type
            or (
                (
                    back_reference := getattr_static(
                        annotation, "_general_manager_class", None
                    )
                )
                is not None
                and type(back_reference) is not GeneralManagerMeta
            )
            or resolve_general_manager_type(annotation, get_graphql_manager_registry())
            is not None
            or metadata.get("relation_kind") in {"direct", "collection"}
        ):
            return None
        # Rule definitions, including their lists/dicts, are stable for this
        # call. No actor/object result or Permission instance is retained.
        for name in names:
            if name not in self.additional:
                self.additional[name] = additional_local_read_permission(
                    self.permission, name
                )
            if self.additional[name] is None:
                return None
        prepared = _FlatQueryField(
            tuple(names), any(self.additional[name] for name in names)
        )
        self.fields[parts] = prepared
        return prepared


def _prepare_native_query_structure(
    manager: type[GeneralManager],
) -> _NativeQueryStructure | None:
    """Attest canonical metadata providers without evaluating a permission."""
    from general_manager.interface.base_interface import _NATIVE_QUERY_METADATA_METHODS
    from general_manager.interface.capabilities.orm import support as orm_support
    from general_manager.interface.capabilities.orm_utils.field_descriptors import (
        FieldDescriptor,
    )
    from general_manager.interface.manifests.capability_models import (
        CapabilitySelection,
    )
    from general_manager.interface.orm_interface import OrmInterfaceBase
    from general_manager.permission.manager_based_permission import (
        _can_prepare_local_read_definitions,
    )
    from general_manager.utils.executable_contract import executable_matches

    interface = getattr_static(manager, "Interface", None)
    permission = getattr_static(manager, "Permission", None)
    if (
        type(interface) is not type(OrmInterfaceBase)
        or not issubclass(interface, OrmInterfaceBase)
        or not _unchanged_metadata_methods(interface, _NATIVE_QUERY_METADATA_METHODS)
        or getattr_static(permission, "__based_on__", None) is not None
        or not _can_prepare_local_read_definitions(permission)
    ):
        return None
    # Canonical wrappers may still read custom descriptors or mappings. Those
    # are dynamic providers and must retain their original per-visit behavior.
    if (
        type(getattr_static(interface, "_capability_handlers", None)) is not dict
        or type(getattr_static(interface, "_capability_selection", None))
        is not CapabilitySelection
        or getattr_static(interface, "_configured_capabilities_applied", None)
        is not True
        or getattr_static(interface, "_parent_class", None) is not manager
    ):
        return None
    descriptors = getattr_static(interface, "_field_descriptors", None)
    if type(descriptors) is not dict:
        return None
    if any(
        type(name) is not str
        or type(descriptor) is not FieldDescriptor
        or type(descriptor.metadata) is not dict
        for name, descriptor in descriptors.items()
    ):
        return None
    resolver, signature = orm_support._NATIVE_QUERY_SUPPORT_RESOLVER
    if orm_support.get_support_capability is not resolver or not executable_matches(
        resolver, signature
    ):
        return None
    for handler, capability in (
        (interface.get_capability_handler("read"), orm_support.OrmReadCapability),
        (
            interface.get_capability_handler("orm_support"),
            orm_support.OrmPersistenceSupportCapability,
        ),
    ):
        if (
            type(handler) is not capability
            or getattr_static(type(handler), "__getattribute__", None)
            is not object.__getattribute__
            or not _unchanged_metadata_methods(
                handler, orm_support._NATIVE_QUERY_METADATA_METHODS[capability]
            )
        ):
            return None
    return _NativeQueryStructure(
        manager,
        interface,
        permission,
        interface.get_attribute_types(),
        frozenset(interface.get_graph_ql_properties()),
    )


class _NativeQueryStructures:
    """Lazily prepare a call's ordinary manager classes; never cache decisions."""

    def __init__(self) -> None:
        self.managers: dict[type[GeneralManager], _NativeQueryStructure | None] = {}

    def flat_field(
        self, instance: GeneralManager, parts: tuple[str, ...]
    ) -> _FlatQueryField | None:
        """Return a reusable flat-field plan only for unchanged native manager contracts."""
        manager = type(instance)
        if type(manager) is not GeneralManagerMeta:
            return None
        if manager not in self.managers:
            from general_manager.interface.orm_interface import OrmInterfaceBase
            from general_manager.manager.general_manager import (
                _COMMON_MANAGER_EXECUTABLES,
                _COMMON_MANAGER_GETATTR,
                _COMMON_MANAGER_IDENTIFICATION,
            )
            from general_manager.permission.manager_based_permission import (
                AdditiveManagerPermission,
            )
            from general_manager.utils.executable_contract import executable_matches

            identification, identification_signature = _COMMON_MANAGER_IDENTIFICATION
            if (
                getattr_static(manager, "__getattribute__")
                is not object.__getattribute__
                or getattr_static(manager, "__getattr__") is not _COMMON_MANAGER_GETATTR
                or getattr_static(manager, "identification") is not identification
                or not executable_matches(identification.fget, identification_signature)
                or not executable_matches(
                    _COMMON_MANAGER_GETATTR, _COMMON_MANAGER_EXECUTABLES[2][1]
                )
            ):
                self.managers[manager] = None
                return None
            interface = getattr_static(manager, "Interface", None)
            permission = getattr_static(manager, "Permission", None)
            if (
                type(interface) is not type(OrmInterfaceBase)
                or getattr_static(type(interface), "__get__", None) is not None
                or (
                    permission is not None
                    and (
                        type(permission) is not type(AdditiveManagerPermission)
                        or getattr_static(type(permission), "__get__", None) is not None
                    )
                )
            ):
                self.managers[manager] = None
                return None
            if (
                instance.Interface is not interface
                or getattr(instance, "Permission", None) is not permission
            ):
                return None
            self.managers[manager] = _prepare_native_query_structure(manager)
        structure = self.managers[manager]
        if (
            structure is None
            or instance.Interface is not structure.interface
            or getattr(instance, "Permission", None) is not structure.permission
        ):
            return None
        return structure.flat_field(parts)


def authorize_query_fields(
    candidates: Iterable[GeneralManager],
    info: ResolveInfo,
    paths: Iterable[str],
    *,
    native_paths: bool = False,
    budget_additional_permissions_only: bool = False,
    budget: QueryFieldAuthorizationBudget | None = None,
    prepare_native_structure: bool = False,
) -> None:
    """Check all operands against every row-visible candidate, never just hits.

    Related rows require their own row permission as well as field permission.
    Failure leaves the original candidate set unchanged and rejects the query.
    ``native_paths`` checks exact projection sources without backend aliases or
    lookup operators. Callers validate those paths statically before iteration.
    Their shared prefixes are walked once for each candidate/member occurrence;
    all child fields and related-row gates remain required. No decision is
    reused between candidates, unrelated source branches or authorization calls.
    Native list resolvers may budget only additional or unknown field policies.
    That mode still executes every runtime field and related-row check; it
    changes accounting, never the authorization decision or candidate scope.
    Native database lists may prepare canonical flat ORM operand structure once
    per call. This assumes schema and declarative rule definitions stay fixed
    during the call; every runtime permission check remains. Custom providers
    and complex paths retain the general traversal. A new call prepares afresh.
    """
    from general_manager.api.graphql_relations import (
        resolve_general_manager_type,
        get_graphql_manager_registry,
    )
    from general_manager.api.graphql_resolvers import (
        can_read_instance,
        check_read_permission,
    )

    normalized = set()
    for path in paths:
        parts = path.split("__")
        if parts:
            normalized.add(tuple(parts))
    if not normalized:
        return
    normalized_paths = sorted(normalized)
    native_root = _NativePathNode()
    if native_paths:
        for native_parts in normalized_paths:
            node = native_root
            for part in native_parts:
                node = node.children.setdefault(part, _NativePathNode())
    budget = budget or QueryFieldAuthorizationBudget()
    structures = (
        _NativeQueryStructures()
        if prepare_native_structure
        and budget_additional_permissions_only
        and not native_paths
        else None
    )

    def needs_additional_budget(instance: GeneralManager, names: set[str]) -> bool:
        """Inspect the current policy, including subtype and instance overrides."""
        from inspect import getattr_static

        from general_manager.permission.base_permission import BasePermission
        from general_manager.permission.manager_based_permission import (
            additional_local_read_permission,
        )

        permission = getattr(instance, "Permission", None)
        if permission is None:
            return False
        if not isinstance(permission, type) or not issubclass(
            permission, BasePermission
        ):
            return True
        # A delegated object's runtime type and nullable fallback can tighten
        # the local rule. Keep that proof outside this accounting shortcut.
        if getattr_static(permission, "__based_on__", None) is not None:
            return True
        return any(
            additional_local_read_permission(permission, name) is not False
            for name in names
        )

    def check(
        instance: GeneralManager,
        parts: tuple[str, ...],
        native_node: _NativePathNode | None = None,
    ) -> None:
        """Authorize one exact operand path, charging each visited field decision."""
        prepared = structures.flat_field(instance, parts) if structures else None
        if prepared is not None:
            if prepared.additional:
                budget.charge()
            # Identification is per object. Preserve its validation/errors at
            # the original point even though the field is already known.
            set(instance.identification)
            if any(
                not check_read_permission(instance, info, name)
                for name in prepared.names
            ):
                raise QueryFieldPermissionError(parts[0])
            return
        if not budget_additional_permissions_only:
            budget.charge()
        field, *rest = parts
        attributes = instance.Interface.get_attribute_types()
        # Backend collection lookup names can differ from public manager names.
        aliases, names, native_relations = _operand_field_names(
            type(instance), field, attributes, native_paths=native_paths
        )
        if budget_additional_permissions_only and needs_additional_budget(
            instance, names
        ):
            budget.charge()
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
            ):
                raise GraphQLError(_RELATION_ERROR)
            if not can_read_instance(value, info):
                raise QueryRelationPermissionError()
            from django.db import models
            from general_manager.interface.orm_interface import OrmInterfaceBase

            orm_interface = cast(
                type[OrmInterfaceBase[models.Model]], instance.Interface
            )
            foreign_key = orm_interface._model._meta.get_field(relation)
            target_field = getattr(foreign_key, "target_field", None)
            if not isinstance(target_field, models.Field):
                raise GraphQLError(_RELATION_ERROR)
            target_names = {target_field.name}
            if target_field.primary_key:
                target_names.add("id")
            for target_name in sorted(target_names):
                check(value, (target_name,))
        if not rest and not (native_node and native_node.children):
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
                raise QueryRelationPermissionError()
            if native_node is None:
                check(member, tuple(rest))
            else:
                for child_field, child in sorted(native_node.children.items()):
                    check(member, (child_field,), child)
        if (
            native_paths
            and isinstance(related, RequestBucket)
            and not related.response_is_complete
        ):
            raise GraphQLError(INCOMPLETE_ERROR)

    for candidate in candidates:
        if native_paths:
            for native_field, native_node in sorted(native_root.children.items()):
                check(candidate, (native_field,), native_node)
        else:
            for normalized_parts in normalized_paths:
                check(candidate, normalized_parts)
    if isinstance(candidates, RequestBucket) and not candidates.response_is_complete:
        raise GraphQLError(INCOMPLETE_ERROR)
