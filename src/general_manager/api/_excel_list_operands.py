"""Snapshot-backed predicate and order evaluation for flat Excel list queries."""

from __future__ import annotations

from inspect import getattr_static
from typing import TYPE_CHECKING, cast

from general_manager.api.graphql_query_permissions import (
    _LOOKUPS,
    _operand_field_names,
    _unchanged_metadata_methods,
)
from general_manager.bucket._materialized_bucket import MaterializedBucket
from general_manager.bucket._ordering import sort_items, validate_ordering_fields
from general_manager.bucket.excel_bucket import ExcelBucket
from general_manager.interface._excel_query_snapshot import (
    collect_excel_operand_rows,
    excel_operand_revision_is_current,
    read_excel_operand,
)
from general_manager.interface.capabilities.excel import (
    ExcelReadCapability,
    ExcelSyncCapability,
)
from general_manager.interface.excel_store import DEFAULT_EXCEL_STORE
from general_manager.interface.interfaces.excel import ExcelInterface
from general_manager.interface.base_interface import (
    InterfaceBase,
    _NATIVE_QUERY_METADATA_METHODS,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta, common_attribute_descriptor
from general_manager.utils.executable_contract import executable_signature
from general_manager.utils.filter_parser import create_filter_function

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from graphene import ResolveInfo

    from general_manager.api.graphql_resolvers import QueryParameterPlan
    from general_manager.bucket._ordering import SortTerm
    from general_manager.bucket.base_bucket import Bucket
    from general_manager.interface.excel import ExcelRowSnapshot


_SCALARS = frozenset({str, int, bool, float})
_PASSIVE_EXPECTED = frozenset(map(id, (*_SCALARS, type(None))))
_PLAIN_COLLECTIONS = frozenset(map(id, (list, tuple, set, frozenset)))


def _passive_expected(value: object) -> bool:
    """Reject application values that can execute code during comparisons."""
    if id(type(value)) in _PASSIVE_EXPECTED:
        return True
    if id(type(value)) in _PLAIN_COLLECTIONS:
        return all(
            id(type(item)) in _PASSIVE_EXPECTED
            for item in cast("Iterable[object]", value)
        )
    return False


def _passive_operand_paths(
    plan: QueryParameterPlan, ordering: tuple[SortTerm, ...]
) -> set[str] | None:
    """Return the operand names only when comparison options are passive."""
    paths: set[str] = set()
    for predicates in (plan.filters, plan.excludes, plan.normalized_excludes):
        # A revision mismatch can repeat the pure selection through ordinary
        # getters. A custom normalizer's executable values must run only there.
        if type(predicates) is not dict or not all(
            type(path) is str and _passive_expected(value)
            for path, value in predicates.items()
        ):
            return None
        paths.update(predicates)
    paths.update(term.field for term in ordering)
    return paths


def apply_excel_list_operands(
    candidates: Bucket[GeneralManager],
    info: ResolveInfo,
    plan: QueryParameterPlan,
    ordering: tuple[SortTerm, ...],
) -> Bucket[GeneralManager] | None:
    """Return an exact filtered and ordered result or leave the normal path alone."""
    if not isinstance(candidates, ExcelBucket):
        return None
    from general_manager.api.graphql_contributors import filter_contributors
    from general_manager.api.graphql_resolvers import apply_query_parameter_plan

    paths = _passive_operand_paths(plan, ordering)
    if not paths:
        return None
    interface = getattr_static(candidates._manager_class, "Interface", None)
    if not isinstance(interface, type) or not issubclass(interface, ExcelInterface):
        return None
    if interface is not candidates._interface_cls:
        return None
    manager = candidates._manager_class
    original_binding = _snapshot_operand_binding(manager, interface, paths)
    if original_binding is None:
        return None
    operand_fields, _, _ = original_binding

    # Re-use only the same generation seen before contributor callbacks, which
    # may explicitly change the workbook. A changed source retains ordinary reads.
    source = interface.excel_meta
    mirror = DEFAULT_EXCEL_STORE.mirror_for(interface)
    fingerprint = mirror.fingerprint
    if fingerprint is None:
        return None

    # All decisions and callbacks still run on their exact source instances.
    authorized = filter_contributors(
        candidates, info, paths, rows_authorized=True, skip_unbounded_accounting=True
    )

    def apply() -> Bucket[GeneralManager]:
        filtered = apply_query_parameter_plan(authorized, plan, None)
        return (
            filtered.sort(*(term.signed_field for term in ordering))
            if ordering
            else filtered
        )

    if type(authorized) is not MaterializedBucket or not authorized:
        return apply()
    if interface.excel_meta is not source or (
        _snapshot_operand_binding(manager, interface, paths) != original_binding
    ):
        return apply()
    rows = collect_excel_operand_rows(
        interface, authorized, source, fingerprint, operand_fields
    )
    # An ordinary policy or sync callback can hold and edit a normalizer's
    # containers, so reattest the same paths and current values after callbacks.
    if rows is None or _passive_operand_paths(plan, ordering) != paths:
        return apply()

    selected = _apply_snapshot_operands(authorized, plan, ordering, rows)
    return (
        selected
        if excel_operand_revision_is_current(interface, source, fingerprint)
        else apply()
    )


def _snapshot_operand_binding(
    manager: type[GeneralManager],
    interface: type[ExcelInterface],
    paths: set[str],
) -> tuple[frozenset[str], object, object] | None:
    """Revalidate all bindings after runtime callbacks may have changed them."""
    if (
        type(manager) is not GeneralManagerMeta
        or getattr_static(manager, "__getattribute__") is not object.__getattribute__
        or getattr_static(manager, "__getattr__") is not _MANAGER_GETATTR
        or getattr_static(manager, "_ensure_as_of_compatible") is not _MANAGER_AS_OF
        or getattr_static(interface, "__getattribute__") is not object.__getattribute__
        or getattr_static(interface, "__getattr__", None) is not None
        or not _unchanged_metadata_methods(interface, _NATIVE_QUERY_METADATA_METHODS)
        or not _unchanged_metadata_methods(interface, _STOCK_READ_DISPATCH)
        or getattr_static(interface, "_execute_with_observability") is not _OBSERVE
        or interface.get_capability_handler("observability") is not None
    ):
        return None
    reader = interface.get_capability_handler("read")
    fields: Collection[str]
    if type(reader) is ExcelReadCapability:
        if (
            type(interface.get_capability_handler("excel_sync"))
            is not ExcelSyncCapability
            or getattr_static(interface, "sync_from_excel")
            is not getattr_static(ExcelInterface, "sync_from_excel")
            or not (_unchanged_metadata_methods(reader, _STOCK_READER))
        ):
            return None
        fields = interface.excel_fields.keys()
    else:
        # Custom code explicitly attests passive scalar getters and metadata;
        # inheriting a capable base alone does not attest an overriding reader.
        opted_in = vars(type(reader)).get("query_operand_snapshot_fields")
        if type(opted_in) is not frozenset or any(
            type(name) is not str for name in opted_in
        ):
            return None
        fields = opted_in
    attributes = interface.get_attribute_types()
    properties = interface.get_graph_ql_properties()
    operand_fields: set[str] = set()
    for path in paths:
        field, *tail = path.split("__")
        metadata = attributes.get(field)
        aliases, names, relations = _operand_field_names(
            manager, field, attributes, native_paths=False
        )
        if (
            field.startswith("_")
            or field not in fields
            or (not tail and field in _LOOKUPS)
            or field in properties
            or metadata is None
            or not any(metadata.get("type") is scalar for scalar in _SCALARS)
            or aliases not in ([], [field])
            or names != {field}
            or relations
            or metadata.get("filter_lookup", field) != field
            or metadata.get("relation_kind") in {"direct", "collection"}
            or (tail and (len(tail) != 1 or tail[0] not in _LOOKUPS))
            or not common_attribute_descriptor(
                manager, field, getattr_static(manager, field, None)
            )
        ):
            return None
        operand_fields.add(field)

    return (
        frozenset(operand_fields),
        id(reader),
        id(interface.get_capability_handler("excel_sync")),
    )


_MANAGER_GETATTR = GeneralManager.__dict__["__getattr__"]
_MANAGER_AS_OF = GeneralManager.__dict__["_ensure_as_of_compatible"]
_OBSERVE = InterfaceBase.__dict__["_execute_with_observability"]
_STOCK_READ_DISPATCH = tuple(
    (name, binding, executable_signature(binding))
    for name in ("get_data", "_require_capability")
    for binding in (InterfaceBase.__dict__[name],)
)
_STOCK_READER = tuple(
    (name, binding, executable_signature(binding))
    for name in ("get_data", "get_attributes", "get_attribute_types")
    for binding in (ExcelReadCapability.__dict__[name],)
)


class _ScalarOperandView:
    """Expose only this manager's ordinary getter with a one-read snapshot."""

    def __init__(self, manager: GeneralManager, snapshot: ExcelRowSnapshot) -> None:
        self._manager = manager
        self._snapshot = snapshot

    def __getattr__(self, field: str) -> object:
        return read_excel_operand(self._manager, field, self._snapshot)


def _apply_snapshot_operands(
    authorized: MaterializedBucket[GeneralManager],
    plan: QueryParameterPlan,
    ordering: tuple[SortTerm, ...],
    rows: dict[int, ExcelRowSnapshot],
) -> Bucket[GeneralManager]:
    items = list(authorized)
    for negative, predicates in (
        (False, plan.filters),
        (True, plan.excludes),
        (True, plan.normalized_excludes),
    ):
        if not predicates:
            continue
        matchers = [
            create_filter_function(path, value) for path, value in predicates.items()
        ]
        kept = []
        for item in items:
            view = _ScalarOperandView(item, rows[id(item)])
            if all(matcher(view) for matcher in matchers) != negative:
                kept.append(item)
        items = kept

    if ordering:
        validate_ordering_fields(authorized._manager_class, ordering)
        items = sort_items(
            items,
            ordering,
            value_for=lambda item, field: read_excel_operand(
                item, field, rows[id(item)]
            ),
        )
    return authorized.with_instances(items)
