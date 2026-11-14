"""Authorize query operands before they influence membership or ordering."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from inspect import getattr_static
from typing import TYPE_CHECKING

from graphql import GraphQLError

from general_manager.api.graphql_work_limits import get_graphql_work_limit
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
MAX_QUERY_PERMISSION_CANDIDATES: int | None = None
# Attestation costs more than direct reads for tiny native projections. This
# selects an optimization, never limits candidates or skips permission checks.
_NATIVE_STRUCTURE_MIN_BATCH = 16
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


def _relation_suffix_is_lookup(parts: Sequence[str], fields: Collection[str]) -> bool:
    """A declared related field wins over a trailing lookup spelling."""
    return len(parts) == 1 and parts[0] in _LOOKUPS and parts[0] not in fields


@dataclass(slots=True)
class _NativePathNode:
    """One shared native source prefix, without storing permission decisions."""

    children: dict[str, _NativePathNode] = field(default_factory=dict)


@dataclass(slots=True)
class QueryFieldAuthorizationBudget:
    """Share only accounting across authorization calls in one operation."""

    decisions: int = 0
    limit: int | None = field(
        default_factory=lambda: get_graphql_work_limit(
            "GRAPHQL_QUERY_PERMISSION_LIMIT", MAX_QUERY_PERMISSION_CANDIDATES
        )
    )

    def charge(self) -> None:
        """Reject work exceeding the operation's field/path decision limit."""
        self.decisions += 1
        if self.limit is not None and self.decisions > self.limit:
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


def _passive_property_namespace(manager: type[GeneralManager]) -> bool:
    """Keep executable ``isinstance`` reflection on the per-visit path."""
    from types import GetSetDescriptorType, MemberDescriptorType

    from general_manager.api.property import GraphQLProperty

    passive_types = (
        str,
        bytes,
        int,
        float,
        bool,
        dict,
        list,
        tuple,
        set,
        frozenset,
        property,
        GetSetDescriptorType,
        MemberDescriptorType,
        type(None),
    )
    class_descriptor = vars(object)["__class__"]
    mro_descriptor = vars(type)["__mro__"]
    for value in vars(manager).values():
        value_type = type(value)
        # Real property descriptors satisfy isinstance before any __class__
        # fallback. Inspect actual ancestry without invoking the value's hooks.
        if any(value_type is passive for passive in passive_types) or any(
            owner is GraphQLProperty for owner in mro_descriptor.__get__(value_type)
        ):
            continue
        accessor = getattr_static(value_type, "__getattribute__", None)
        if (
            accessor is not object.__getattribute__
            and accessor is not type.__getattribute__
        ) or getattr_static(value, "__class__", None) is not class_descriptor:
            return False
    return True


@dataclass(frozen=True, slots=True)
class _FlatQueryField:
    """Structure only: runtime decisions still run in the original order."""

    names: tuple[str, ...]
    additional: bool


type _OperandShape = tuple[tuple[str, ...], frozenset[str], frozenset[str]]


@dataclass(slots=True)
class _NativeQueryStructure:
    """One call's passive native schema and declarative policy structure."""

    manager: type[GeneralManager]
    interface: type[InterfaceBase]
    permission: object
    attributes: dict[str, AttributeTypedDict]
    properties: frozenset[str]
    attribute_names: frozenset[str]
    calculation: bool = False
    fields: dict[tuple[str, ...], _FlatQueryField | None] = field(default_factory=dict)
    additional: dict[str, bool | None] = field(default_factory=dict)
    source_annotations: dict[str, object] = field(default_factory=dict)
    operand_shapes: dict[str, _OperandShape] = field(default_factory=dict)

    def additional_for(self, name: str) -> bool | None:
        """Share only a reached field's declarative comparison, never a grant."""
        from general_manager.permission.manager_based_permission import (
            additional_local_read_permission,
        )

        if name not in self.additional:
            self.additional[name] = additional_local_read_permission(
                self.permission, name, manager=self.manager
            )
        return self.additional[name]

    def calculation_operands(self, name: str) -> _OperandShape | None:
        """Reuse passive input spellings; keep aliases and properties general."""
        if (
            not self.calculation
            or name.endswith("_id")
            or name not in self.attributes
            or name in self.properties
        ):
            return None
        if name not in self.operand_shapes:
            aliases, names, relations = _operand_field_names(
                self.manager, name, self.attributes, native_paths=False
            )
            self.operand_shapes[name] = (
                tuple(aliases),
                frozenset(names),
                frozenset(relations),
            )
        return self.operand_shapes[name]

    def flat_field(self, parts: tuple[str, ...]) -> _FlatQueryField | None:
        """Prepare only a visited, known flat operand; keep general paths dynamic."""
        if parts in self.fields:
            return self.fields[parts]
        from general_manager.api.graphql_relations import (
            get_graphql_manager_registry,
            resolve_general_manager_type,
        )

        self.fields[parts] = None
        field_name, *rest = parts
        if len(rest) > 1 or (rest and rest[0] not in _LOOKUPS):
            return None
        if self.calculation and field_name.endswith("_id"):
            # Calculation aliases can consult another input's annotation. Keep
            # that inference and its possible target lookups on the general path.
            return None
        operands = self.calculation_operands(field_name)
        aliases, names, _ = (
            operands
            if operands is not None
            else _operand_field_names(
                self.manager, field_name, self.attributes, native_paths=False
            )
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
        terminal_calculation_manager = (
            self.calculation
            and not rest
            and tuple(aliases) == (field_name,)
            and names == {field_name}
            and type(annotation) is GeneralManagerMeta
        )
        if (
            not identity_alias
            and not terminal_calculation_manager
            and (
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
                or resolve_general_manager_type(
                    annotation, get_graphql_manager_registry()
                )
                is not None
                or metadata.get("relation_kind") in {"direct", "collection"}
            )
        ):
            return None
        # Rule definitions, including their lists/dicts, are stable for this
        # call. No actor/object result or Permission instance is retained.
        for name in names:
            if self.additional_for(name) is None:
                return None
        prepared = _FlatQueryField(
            tuple(names), any(self.additional[name] for name in names)
        )
        self.fields[parts] = prepared
        return prepared


def _prepare_native_query_structure(
    manager: type[GeneralManager],
    *,
    allow_calculation: bool = False,
) -> _NativeQueryStructure | None:
    """Attest canonical metadata providers without evaluating a permission."""
    from general_manager.interface.base_interface import (
        InterfaceBase,
        _NATIVE_QUERY_METADATA_METHODS,
    )
    from general_manager.interface.interfaces.calculation import CalculationInterface
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
    if type(interface) is not type(InterfaceBase) or not issubclass(
        interface, InterfaceBase
    ):
        return None
    calculation = not issubclass(interface, OrmInterfaceBase)
    if calculation and (
        not allow_calculation or not issubclass(interface, CalculationInterface)
    ):
        return None
    if (
        not _unchanged_metadata_methods(interface, _NATIVE_QUERY_METADATA_METHODS)
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
        or not _passive_property_namespace(manager)
    ):
        return None
    if calculation:
        if not _calculation_query_metadata_is_canonical(interface):
            return None
    else:
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
        frozenset(interface.get_attributes()),
        calculation=calculation,
    )


def _calculation_query_metadata_is_canonical(interface: type[InterfaceBase]) -> bool:
    """Prove passive input metadata without calling domain or casting providers."""
    from general_manager.interface.capabilities.calculation import lifecycle
    from general_manager.interface.interfaces.request import RequestInterface

    if issubclass(interface, RequestInterface):
        return False
    handler = interface.get_capability_handler("read")
    if (
        type(handler) is not lifecycle.CalculationReadCapability
        or getattr_static(type(handler), "__getattribute__", None)
        is not object.__getattribute__
        or not _unchanged_metadata_methods(
            handler, lifecycle._NATIVE_QUERY_METADATA_METHODS
        )
    ):
        return False
    inputs = getattr_static(interface, "input_fields", None)
    input_type = lifecycle._NATIVE_QUERY_INPUT_TYPE
    missing = object()
    if (
        type(inputs) is not dict
        or type(input_type) is not type
        or getattr_static(input_type, "__getattribute__", None)
        is not object.__getattribute__
        or getattr_static(input_type, "__getattr__", None) is not None
        or getattr_static(input_type, "__dict__", None)
        is not lifecycle._NATIVE_QUERY_INPUT_DICT
        or any(
            getattr_static(input_type, name, missing) is not missing
            for name in ("type", "required")
        )
    ):
        return False
    for name, item in inputs.items():
        if type(name) is not str or type(item) is not input_type:
            return False
        state = vars(item)
        if (
            type(state) is not dict
            or type(state.get("required")) is not bool
            or any(
                key not in state or getattr_static(item, key, missing) is not state[key]
                for key in ("type", "required")
            )
        ):
            return False
    return True


class _NativeQueryStructures:
    """Lazily prepare a call's ordinary manager classes; never cache decisions."""

    def __init__(self, *, allow_calculation: bool = False) -> None:
        self.managers: dict[type[GeneralManager], _NativeQueryStructure | None] = {}
        self.allow_calculation = allow_calculation

    def for_instance(self, instance: GeneralManager) -> _NativeQueryStructure | None:
        """Reuse schema structure, never instance permissions, for native managers."""
        manager = type(instance)
        if type(manager) is not GeneralManagerMeta:
            return None
        if manager not in self.managers:
            from general_manager.interface.base_interface import InterfaceBase
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
                type(interface) is not type(InterfaceBase)
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
            self.managers[manager] = _prepare_native_query_structure(
                manager, allow_calculation=self.allow_calculation
            )
        structure = self.managers[manager]
        if (
            structure is None
            or instance.Interface is not structure.interface
            or getattr(instance, "Permission", None) is not structure.permission
        ):
            return None
        return structure


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
    Native database and calculation lists may prepare canonical flat operand
    structure once per call. Calculation relation traversal may share passive
    input metadata, spellings and declarative accounting while retaining every
    runtime getter and row/leaf check. Domain, casting and validation providers
    remain owned by normal calculation execution. Exact search paths also reuse
    attested ORM metadata and native
    field annotations after a second candidate is visited, including across
    related rows. Small batches and custom iterables retain direct reads; they
    are never enumerated or probed to decide whether to prepare. This assumes schema
    and declarative rule definitions stay fixed during the call; every runtime
    permission check remains. Search properties/input-only annotations and custom
    metadata providers retain their dynamic reads. A new call prepares afresh.
    """
    from general_manager.api.graphql_relations import (
        resolve_general_manager_type,
        get_graphql_manager_registry,
    )
    from general_manager.api.graphql_resolvers import (
        can_read_instance,
        check_read_permission,
        relation_id_alias_target_fields,
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
                child = node.children.get(part)
                if child is None:
                    child = _NativePathNode()
                    node.children[part] = child
                node = child
    budget = budget or QueryFieldAuthorizationBudget()
    native_batch = (
        native_paths
        and (type(candidates) is list or type(candidates) is tuple)
        and len(candidates) >= _NATIVE_STRUCTURE_MIN_BATCH
    )
    structures = (
        _NativeQueryStructures(allow_calculation=not native_paths)
        if native_batch
        or (
            not native_paths
            and prepare_native_structure
            and budget_additional_permissions_only
        )
        else None
    )
    repeated_native_candidate = False

    def needs_additional_budget(
        instance: GeneralManager,
        names: Iterable[str],
        structure: _NativeQueryStructure | None,
    ) -> bool:
        """Inspect the current policy, including subtype and instance overrides."""
        if structure is not None and structure.calculation:
            return any(structure.additional_for(name) is not False for name in names)
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
            additional_local_read_permission(permission, name, manager=type(instance))
            is not False
            for name in names
        )

    def check(
        instance: GeneralManager,
        parts: tuple[str, ...],
        native_node: _NativePathNode | None = None,
    ) -> None:
        """Authorize one exact operand path, charging each visited field decision."""
        structure = (
            structures.for_instance(instance)
            if structures and not native_paths
            else None
        )
        prepared = structure.flat_field(parts) if structure is not None else None
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
        if native_paths:
            structure = (
                structures.for_instance(instance)
                if structures and repeated_native_candidate
                else None
            )
        elif structure is not None and not structure.calculation:
            structure = None
        attributes = (
            structure.attributes
            if structure is not None
            else instance.Interface.get_attribute_types()
        )
        # Backend collection lookup names can differ from public manager names.
        operands = (
            structure.calculation_operands(field) if structure is not None else None
        )
        aliases, names, native_relations = (
            operands
            if operands is not None
            else _operand_field_names(
                type(instance), field, attributes, native_paths=native_paths
            )
        )
        # A standard delegated policy already resolves its concrete target
        # during the normal field check. Inspect that same permission object,
        # not a second relation read or a static declared target type.
        from general_manager.permission.manager_based_permission import (
            _can_prepare_local_read_definitions,
            additional_resolved_read_permission,
        )
        from general_manager.permission.base_permission import BasePermission

        permission = getattr_static(instance, "Permission", None)
        resolved_budget = (
            budget_additional_permissions_only
            and getattr_static(permission, "__based_on__", None) is not None
            and _can_prepare_local_read_definitions(permission)
        )
        charged = False

        def charge_resolved(resolved: BasePermission, name: str) -> None:
            """Charge this operand once when its resolved policy adds or may add restrictions."""
            nonlocal charged
            if (
                not charged
                and additional_resolved_read_permission(resolved, name) is not False
            ):
                budget.charge()
                charged = True

        if (
            budget_additional_permissions_only
            and not resolved_budget
            and needs_additional_budget(instance, names, structure)
        ):
            budget.charge()
        known_fields = set(attributes) | set(instance.identification)
        if structure is not None:
            known_fields.update(structure.properties)
            known_fields.update(structure.attribute_names)
        else:
            known_fields.update(instance.Interface.get_graph_ql_properties())
            known_fields.update(instance.Interface.get_attributes())
        known_fields.update(instance.Interface.input_fields)
        if not names.intersection(known_fields):
            raise QueryFieldPermissionError(field)
        for name in names:
            if resolved_budget:

                def before_check(
                    resolved: BasePermission, field_name: str = name
                ) -> None:
                    """Account for the exact resolved permission before its normal field check."""
                    charge_resolved(resolved, field_name)

                allowed = check_read_permission(
                    instance,
                    info,
                    name,
                    before_check=before_check,
                )
            else:
                allowed = check_read_permission(instance, info, name)
            if not allowed:
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
            target_names = relation_id_alias_target_fields(instance.Interface, relation)
            if not target_names:
                raise GraphQLError(_RELATION_ERROR)
            for target_name in target_names:
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

            if (
                structure is None
                or field not in structure.attributes
                or field in structure.properties
            ):
                _, annotation = source_field_annotation(type(instance), field)
            else:
                if field not in structure.source_annotations:
                    _, annotation = source_field_annotation(type(instance), field)
                    structure.source_annotations[field] = annotation
                annotation = structure.source_annotations[field]
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
            repeated_native_candidate = True
        else:
            for normalized_parts in normalized_paths:
                check(candidate, normalized_parts)
    if isinstance(candidates, RequestBucket) and not candidates.response_is_complete:
        raise GraphQLError(INCOMPLETE_ERROR)


def _prepare_pushdown_structure(
    manager: type[GeneralManager],
) -> _NativeQueryStructure | None:
    """Attest native construction before statically interpreting field policies."""
    from general_manager.utils.executable_contract import executable_matches

    if type(manager) is not GeneralManagerMeta:
        return None
    from general_manager.manager.general_manager import (
        _QUERY_PUSHDOWN_MANAGER_METHODS,
        _COMMON_MANAGER_IDENTIFICATION,
    )
    from general_manager.interface.orm_interface import (
        _QUERY_PUSHDOWN_INTERFACE_METHODS,
    )

    if not _unchanged_metadata_methods(manager, _QUERY_PUSHDOWN_MANAGER_METHODS):
        return None
    identification, signature = _COMMON_MANAGER_IDENTIFICATION
    if getattr_static(
        manager, "identification"
    ) is not identification or not executable_matches(identification.fget, signature):
        return None
    interface = getattr_static(manager, "Interface", None)
    if not _unchanged_metadata_methods(interface, _QUERY_PUSHDOWN_INTERFACE_METHODS):
        return None
    for owner in (manager, interface):
        if any(
            getattr_static(owner, name, None) is not getattr_static(object, name)
            for name in ("__new__", "__getattribute__", "__setattr__")
        ):
            return None
    structure = _prepare_native_query_structure(manager)
    if structure is None:
        return None
    return structure


def can_filter_before_query_authorization(
    candidates: Bucket[GeneralManager],
    paths: Iterable[str],
    info: ResolveInfo | None = None,
) -> bool:
    """Prove predicates harmless once, before narrowing a native row domain.

    Permission declarations remain stable during a query. Every relation hop
    must be readable, and each traversed target must have an unconditional row
    read plan for this actor; otherwise membership could reveal hidden targets.
    Unknown/custom policies retain full-domain runtime authorization.
    """
    from general_manager.bucket.database_bucket import DatabaseBucket
    from general_manager.api.graphql_errors import get_read_permission_filter
    from general_manager.api.graphql_relations import (
        get_graphql_manager_registry,
        resolve_general_manager_type,
    )
    from general_manager.permission.manager_based_permission import (
        additional_local_read_permission,
    )

    if type(candidates) is not DatabaseBucket:
        return False
    structures: dict[type[GeneralManager], _NativeQueryStructure | None] = {}
    readable_targets: dict[type[GeneralManager], bool] = {}

    def safe_path(manager: type[GeneralManager], parts: tuple[str, ...]) -> bool:
        if manager not in structures:
            structures[manager] = _prepare_pushdown_structure(manager)
        structure = structures[manager]
        if structure is None or not parts:
            return False
        prepared = structure.flat_field(parts)
        if prepared is not None:
            return not prepared.additional
        field_name, *rest = parts
        aliases, names, _ = _operand_field_names(
            manager, field_name, structure.attributes, native_paths=False
        )
        if (
            len(aliases) > 1
            or not names.intersection(structure.attributes)
            or names.intersection(structure.properties)
            or any(
                additional_local_read_permission(
                    structure.permission, name, manager=manager
                )
                is not False
                for name in names
            )
        ):
            return False
        metadata: dict[str, object] = dict(
            structure.attributes.get(aliases[0] if aliases else field_name, {})
        )
        target = resolve_general_manager_type(
            metadata.get("type"), get_graphql_manager_registry()
        )
        if target is None or not rest:
            return False
        if target not in structures:
            structures[target] = _prepare_pushdown_structure(target)
        target_structure = structures[target]
        if target_structure is None:
            return False
        # A target field called e.g. "contains" wins over lookup spelling,
        # matching the existing runtime authorization grammar.
        if (
            len(rest) == 1
            and rest[0] in _LOOKUPS
            and rest[0]
            not in (
                set(target_structure.attributes)
                | set(target_structure.properties)
                | set(target_structure.interface.get_attributes())
            )
        ):
            return True
        if info is None:
            return False
        if target not in readable_targets:
            plan = get_read_permission_filter(target, info)
            readable_targets[target] = (
                plan.decision == "allow_all"
                and not plan.requires_instance_check
                and not plan.filters
            )
        return readable_targets[target] and safe_path(target, tuple(rest))

    return all(
        safe_path(candidates._manager_class, tuple(path.split("__"))) for path in paths
    )
