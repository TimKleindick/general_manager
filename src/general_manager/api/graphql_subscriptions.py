"""
Standalone subscription-subsystem helpers extracted from ``api/graphql.py``.

These pure helpers hold no reference to the ``GraphQL`` class.  The two
methods that mutate class state (``_add_subscription_field`` and
``_handle_data_change``) remain on the ``GraphQL`` class and call these
helpers via thin one-liner wrappers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from typing import Any, Callable, Iterable, TYPE_CHECKING, cast

from channels.layers import BaseChannelLayer, get_channel_layer  # type: ignore[import]

from graphql.language.ast import (
    FieldNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    SelectionSetNode,
)

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import (
    Dependency,
    parse_dependency_identifier,
)
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.api.graphql_errors import MissingChannelLayerError

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo

logger = get_logger("api.graphql_subscriptions")


# ---------------------------------------------------------------------------
# Channel-layer helpers
# ---------------------------------------------------------------------------


def get_channel_layer_safe(strict: bool = False) -> BaseChannelLayer | None:
    """
    Retrieve the configured channel layer for GraphQL subscriptions.

    Parameters:
        strict: When ``True``, raise :exc:`MissingChannelLayerError` if no
            channel layer is configured.

    Returns:
        The channel layer instance, or ``None`` if unavailable.

    Raises:
        MissingChannelLayerError: When *strict* is ``True`` and no layer exists.
    """
    layer = cast(BaseChannelLayer | None, get_channel_layer())
    if layer is None and strict:
        raise MissingChannelLayerError()
    return layer


def group_name(
    manager_class: type[GeneralManager], identification: dict[str, Any]
) -> str:
    """
    Build a deterministic channel-group name for a specific manager instance.

    Parameters:
        manager_class: Manager class used to namespace the group.
        identification: Identifying fields for the instance; serialised with
            sorted keys before hashing.

    Returns:
        A stable, collision-resistant group identifier string.
    """
    normalized = json.dumps(identification, sort_keys=True, default=str)
    digest = hashlib.sha256(
        f"{manager_class.__module__}.{manager_class.__name__}:{normalized}".encode(
            "utf-8"
        )
    ).hexdigest()[:32]
    return f"gm_subscriptions.{manager_class.__name__}.{digest}"


async def channel_listener(
    channel_layer: BaseChannelLayer,
    channel_name: str,
    queue: asyncio.Queue[str],
) -> None:
    """
    Listen on *channel_layer* for ``"gm.subscription.event"`` messages and
    enqueue their ``action`` values into *queue*.

    The loop exits silently when the task is cancelled.

    Parameters:
        channel_layer: Channel layer to receive messages from.
        channel_name: Channel to listen on.
        queue: Async queue receiving action strings.
    """
    try:
        while True:
            message = cast(dict[str, Any], await channel_layer.receive(channel_name))
            if message.get("type") != "gm.subscription.event":
                continue
            action = cast(str | None, message.get("action"))
            if action is not None:
                await queue.put(action)
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Property-priming helper
# ---------------------------------------------------------------------------


def prime_graphql_properties(
    instance: GeneralManager,
    property_names: Iterable[str] | None = None,
) -> None:
    """
    Eagerly evaluate ``GraphQLProperty`` attributes on *instance* to trigger
    dependency tracking.

    Parameters:
        instance: Manager instance whose GraphQLProperty attributes are read.
        property_names: Specific property names to prime.  ``None`` primes
            every GraphQLProperty defined on the Interface.
    """
    interface_cls = getattr(instance.__class__, "Interface", None)
    if interface_cls is None:
        return
    available_properties = interface_cls.get_graph_ql_properties()
    if property_names is None:
        names = available_properties.keys()
    else:
        names = [name for name in property_names if name in available_properties]
    for prop_name in names:
        getattr(instance, prop_name)


# ---------------------------------------------------------------------------
# Dependency resolution helpers
# ---------------------------------------------------------------------------


def dependencies_from_tracker(
    dependency_records: Iterable[Dependency],
    manager_registry: dict[str, type[GeneralManager]],
) -> list[tuple[type[GeneralManager], dict[str, Any]]]:
    """
    Convert dependency-tracker records into ``(manager_class, identification)``
    pairs.

    Records whose operation is not ``"identification"`` or whose manager name
    is not in *manager_registry* are silently skipped.

    Parameters:
        dependency_records: Iterable of ``Dependency`` records.
        manager_registry: Registry mapping manager name → manager class.

    Returns:
        List of ``(manager_class, identification_dict)`` tuples.
    """
    resolved: list[tuple[type[GeneralManager], dict[str, Any]]] = []
    for manager_name, operation, identifier in dependency_records:
        if operation != "identification":
            continue
        manager_class = manager_registry.get(manager_name)
        if manager_class is None:
            continue
        parsed = parse_dependency_identifier(identifier)
        if not isinstance(parsed, dict):
            continue
        resolved.append((manager_class, parsed))
    return resolved


def resolve_subscription_dependencies(
    manager_class: type[GeneralManager],
    instance: GeneralManager,
    manager_registry: dict[str, type[GeneralManager]],
    dependency_records: Iterable[Dependency] | None = None,
) -> list[tuple[type[GeneralManager], dict[str, Any]]]:
    """
    Build deduplicated ``(manager_class, identification)`` dependency pairs for
    subscription channel wiring.

    Combines dependency-tracker records (if provided) with the manager's
    Interface ``input_fields`` that reference other ``GeneralManager`` types.

    Parameters:
        manager_class: Manager type whose dependencies are being resolved.
        instance: Instantiated manager item inspected for input-field values.
        manager_registry: Registry used by :func:`dependencies_from_tracker`.
        dependency_records: Optional dependency-tracker records to include.

    Returns:
        List of ``(dep_manager_class, identification)`` pairs, excluding the
        pair matching *instance* itself.
    """
    dep_list: list[tuple[type[GeneralManager], dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()

    if dependency_records:
        for (
            dependency_class,
            dependency_identification,
        ) in dependencies_from_tracker(dependency_records, manager_registry):
            if (
                dependency_class is manager_class
                and dependency_identification == instance.identification
            ):
                continue
            key = (
                dependency_class.__name__,
                json.dumps(dependency_identification, sort_keys=True, default=str),
            )
            if key in seen:
                continue
            seen.add(key)
            dep_list.append((dependency_class, dependency_identification))

    interface_cls = manager_class.Interface

    for input_name, input_field in interface_cls.input_fields.items():
        if not issubclass(input_field.type, GeneralManager):
            continue

        raw_value = instance._interface.identification.get(input_name)
        if raw_value is None:
            continue

        values = raw_value if isinstance(raw_value, list) else [raw_value]
        for value in values:
            if isinstance(value, GeneralManager):
                identification = deepcopy(value.identification)
                manager_type = cast(type[GeneralManager], input_field.type)
                if (
                    manager_type is manager_class
                    and identification == instance.identification
                ):
                    continue
                key = (
                    manager_type.__name__,
                    json.dumps(identification, sort_keys=True, default=str),
                )
                if key in seen:
                    continue
                seen.add(key)
                dep_list.append((manager_type, identification))
            elif isinstance(value, dict):
                identification_dict = deepcopy(cast(dict[str, Any], value))
                manager_type = cast(type[GeneralManager], input_field.type)
                if (
                    manager_type is manager_class
                    and identification_dict == instance.identification
                ):
                    continue
                key = (
                    manager_type.__name__,
                    json.dumps(identification_dict, sort_keys=True, default=str),
                )
                if key in seen:
                    continue
                seen.add(key)
                dep_list.append((manager_type, identification_dict))

    return dep_list


# ---------------------------------------------------------------------------
# Subscription-selection helpers
# ---------------------------------------------------------------------------


def subscription_property_names(
    info: GraphQLResolveInfo,
    manager_class: type[GeneralManager],
    normalize_graphql_name: Callable[[str], str],
) -> set[str]:
    """
    Return the ``GraphQLProperty`` names selected under ``item`` in the
    subscription payload.

    Parameters:
        info: GraphQL resolver info containing the parsed selection set.
        manager_class: Manager class whose Interface defines available property
            names.
        normalize_graphql_name: Callable converting a camelCase field name to
            its snake_case Python equivalent (passed to avoid circular imports).

    Returns:
        Set of selected ``GraphQLProperty`` names; empty set if none found.
    """
    interface_cls = getattr(manager_class, "Interface", None)
    if interface_cls is None:
        return set()
    available_properties = set(interface_cls.get_graph_ql_properties().keys())
    if not available_properties:
        return set()

    property_names: set[str] = set()

    def collect_from_selection(selection_set: SelectionSetNode | None) -> None:
        if selection_set is None:
            return
        for selection in selection_set.selections:
            if isinstance(selection, FieldNode):
                name = selection.name.value
                normalized = normalize_graphql_name(name)
                if normalized in available_properties:
                    property_names.add(normalized)
            elif isinstance(selection, FragmentSpreadNode):
                fragment = info.fragments.get(selection.name.value)
                if fragment is not None:
                    collect_from_selection(fragment.selection_set)
            elif isinstance(selection, InlineFragmentNode):
                collect_from_selection(selection.selection_set)

    def inspect_selection_set(selection_set: SelectionSetNode | None) -> None:
        if selection_set is None:
            return
        for selection in selection_set.selections:
            if isinstance(selection, FieldNode):
                if selection.name.value == "item":
                    collect_from_selection(selection.selection_set)
                else:
                    inspect_selection_set(selection.selection_set)
            elif isinstance(selection, FragmentSpreadNode):
                fragment = info.fragments.get(selection.name.value)
                if fragment is not None:
                    inspect_selection_set(fragment.selection_set)
            elif isinstance(selection, InlineFragmentNode):
                inspect_selection_set(selection.selection_set)

    for node in info.field_nodes:
        inspect_selection_set(node.selection_set)
    return property_names


# ---------------------------------------------------------------------------
# Manager instantiation helper
# ---------------------------------------------------------------------------


def instantiate_manager(
    manager_class: type[GeneralManager],
    identification: dict[str, Any],
    *,
    collect_dependencies: bool = False,
    property_names: Iterable[str] | None = None,
) -> tuple[GeneralManager, set[Dependency]]:
    """
    Instantiate a ``GeneralManager`` and optionally capture dependency records.

    Parameters:
        manager_class: Manager class to instantiate.
        identification: Identification field values.
        collect_dependencies: When ``True``, prime GraphQL properties inside a
            :class:`~general_manager.cache.cache_tracker.DependencyTracker` and
            return the captured records.
        property_names: Specific GraphQLProperty names to prime; ``None`` primes
            all.

    Returns:
        ``(instance, dependency_set)`` — the dependency set is empty when
        *collect_dependencies* is ``False``.
    """
    if collect_dependencies:
        with DependencyTracker() as captured_dependencies:
            instance = manager_class(**identification)
            prime_graphql_properties(instance, property_names)
        return instance, captured_dependencies

    instance = manager_class(**identification)
    return instance, set()
