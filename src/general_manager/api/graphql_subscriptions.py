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
from copy import deepcopy
from typing import Callable, Iterable, TYPE_CHECKING, cast

from channels.layers import BaseChannelLayer, get_channel_layer

from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import (
    Dependency,
    parse_dependency_identifier,
    serialize_dependency_identifier,
)
from general_manager.api.graphql_prefetch import (
    collect_selected_graphql_property_names,
)
from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager
from general_manager.api.graphql_errors import MissingChannelLayerError

if TYPE_CHECKING:
    from graphene import ResolveInfo as GraphQLResolveInfo

logger = get_logger("api.graphql_subscriptions")

type Identification = dict[str, object]
"""Manager identification payload used for subscription group wiring."""

type SubscriptionMessage = dict[str, object]
"""Channel-layer subscription event payload."""


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
    manager_class: type[GeneralManager], identification: Identification
) -> str:
    """
    Build a deterministic channel-group name for a specific manager instance.

    Parameters:
        manager_class: Manager class used to namespace the group.
        identification: Identifying fields for the instance. The value is
            serialized with ``serialize_dependency_identifier()``, whose
            normalization coerces mapping keys to strings, orders mappings by
            normalized key, preserves list/tuple order, sorts sets by string
            form, serializes dates/datetimes with ``isoformat()``, keeps JSON
            scalars as JSON values, and falls back to ``repr(...)`` for
            unsupported objects before hashing.
            If mapping keys normalize to the same string, normalization keeps
            the last item after sorting by ``str(key)``; equal sort keys keep
            the input mapping's iteration order.

    Returns:
        A stable, collision-resistant group identifier string.
    """
    normalized = serialize_dependency_identifier(identification)
    digest = hashlib.sha256(
        f"{manager_class.__module__}.{manager_class.__name__}:{normalized}".encode(
            "utf-8"
        )
    ).hexdigest()[:32]
    return f"gm_subscriptions.{manager_class.__name__}.{digest}"


def class_group_name(manager_class: type[GeneralManager]) -> str:
    """
    Build a deterministic channel-group name for all instances of a manager.

    Parameters:
        manager_class: Manager class used to namespace the group.

    Returns:
        A stable group identifier for class-wide subscriptions.
    """
    return f"gm_subscriptions.{manager_class.__name__}.__class__"


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
            message = cast(
                SubscriptionMessage, await channel_layer.receive(channel_name)
            )
            if message.get("type") != "gm.subscription.event":
                continue
            action = cast(str | None, message.get("action"))
            if action is not None:
                await queue.put(action)
    except asyncio.CancelledError:
        pass


async def channel_message_listener(
    channel_layer: BaseChannelLayer,
    channel_name: str,
    queue: asyncio.Queue[SubscriptionMessage],
) -> None:
    """
    Listen for subscription event messages and enqueue complete messages.

    Class-wide subscriptions need the event identification, not only the action,
    so this listener preserves the full channel-layer payload. It only filters
    on message ``type`` and action presence; malformed manager names,
    non-string actions, and non-object identifications are filtered by the
    class-wide subscription stream after queueing.
    """
    try:
        while True:
            message = cast(
                SubscriptionMessage, await channel_layer.receive(channel_name)
            )
            if message.get("type") != "gm.subscription.event":
                continue
            if message.get("action") is not None:
                await queue.put(message)
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
) -> list[tuple[type[GeneralManager], Identification]]:
    """
    Convert dependency-tracker records into ``(manager_class, identification)``
    pairs.

    Records whose operation is not ``"identification"``, whose manager name is
    not in *manager_registry*, or whose identifier cannot be parsed into an
    identification dictionary are silently skipped. A serialized JSON ``null`` is
    therefore skipped the same way as malformed identifier JSON.

    Parameters:
        dependency_records: Iterable of ``Dependency`` records.
        manager_registry: Registry mapping manager name → manager class.

    Returns:
        List of ``(manager_class, identification_dict)`` tuples.
    """
    resolved: list[tuple[type[GeneralManager], Identification]] = []
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
) -> list[tuple[type[GeneralManager], Identification]]:
    """
    Build deduplicated ``(manager_class, identification)`` dependency pairs for
    subscription channel wiring.

    Combines dependency-tracker records (if provided) with the manager's
    Interface ``input_fields`` that reference other ``GeneralManager`` types.
    Tracker-derived dependencies are emitted first in accepted tracker-record
    order; input-field dependencies are appended afterward in
    ``Interface.input_fields`` iteration order, with list-valued fields
    preserving list order. Input-field references are detected with
    ``issubclass(input_field.type, GeneralManager)``. Input-field values may be
    manager instances, identification dictionaries, or lists containing either
    form. ``None`` values are ignored. Returned identifications are deep-copied
    before they are stored in the result. Dependencies are deduplicated by
    manager type name and serialized identification, so two different manager
    classes with the same ``__name__`` and identical serialized identification
    collide. The changed instance itself is excluded. The helper skips malformed
    tracker records as described in ``dependencies_from_tracker()``, but does
    not wrap errors from malformed interface metadata, unexpected identification
    values, ``deepcopy()``, or dependency identifier serialization.

    Parameters:
        manager_class: Manager type whose dependencies are being resolved.
        instance: Instantiated manager item inspected for input-field values.
        manager_registry: Registry used by :func:`dependencies_from_tracker`.
        dependency_records: Optional dependency-tracker records to include.

    Returns:
        List of ``(dep_manager_class, identification)`` pairs, excluding the
        pair matching *instance* itself.
    """
    dep_list: list[tuple[type[GeneralManager], Identification]] = []
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
                serialize_dependency_identifier(dependency_identification),
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
                manager_type = input_field.type
                if (
                    manager_type is manager_class
                    and identification == instance.identification
                ):
                    continue
                key = (
                    manager_type.__name__,
                    serialize_dependency_identifier(identification),
                )
                if key in seen:
                    continue
                seen.add(key)
                dep_list.append((manager_type, identification))
            elif isinstance(value, dict):
                identification_dict = deepcopy(cast(Identification, value))
                manager_type = input_field.type
                if (
                    manager_type is manager_class
                    and identification_dict == instance.identification
                ):
                    continue
                key = (
                    manager_type.__name__,
                    serialize_dependency_identifier(identification_dict),
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

    The selected-property collector walks the resolver info's field nodes and
    named fragments, descends through the ``item`` field, follows inline
    fragments and fragment spreads, compares AST ``name.value`` values rather
    than aliases, applies ``normalize_graphql_name`` to selected GraphQL field
    names, and keeps only names present in
    ``manager_class.Interface.get_graph_ql_properties()``. Aliases never replace
    or add property names; an aliased ``aliasValue: propB`` selection records
    ``prop_b``. Selections that do not include ``item`` or do not map to GraphQL
    properties return an empty set.

    Parameters:
        info: GraphQL resolver info containing the parsed selection set.
        manager_class: Manager class whose Interface defines available property
            names.
        normalize_graphql_name: Callable converting a camelCase field name to
            its snake_case Python equivalent (passed to avoid circular imports).

    Returns:
        Set of selected ``GraphQLProperty`` names; empty set if none found.
    """
    return collect_selected_graphql_property_names(
        info,
        manager_class,
        root_field="item",
        normalize_name=normalize_graphql_name,
    )


# ---------------------------------------------------------------------------
# Manager instantiation helper
# ---------------------------------------------------------------------------


def instantiate_manager(
    manager_class: type[GeneralManager],
    identification: Identification,
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
