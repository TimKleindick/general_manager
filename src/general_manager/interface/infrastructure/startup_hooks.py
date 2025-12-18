"""Registry for capability-provided startup hooks."""

from __future__ import annotations

from typing import Callable, Dict, Iterator, List, Sequence, Tuple, Type, Set

StartupHook = Callable[[], None]
DependencyResolver = Callable[[Type[object]], Set[Type[object]]]
InterfaceType = Type[object]


class StartupHookEntry:
    """Startup hook registration with optional dependency resolver."""

    __slots__ = ("dependency_resolver", "hook")

    def __init__(
        self,
        hook: StartupHook,
        dependency_resolver: DependencyResolver | None,
    ) -> None:
        """
        Initialize a StartupHookEntry pairing a startup hook with an optional dependency resolver.

        Parameters:
            hook: A callable to be invoked at startup.
            dependency_resolver: Optional callable that, given an interface type, returns a set of interface types this hook depends on; may be None if no dependency information is provided.
        """
        self.hook = hook
        self.dependency_resolver = dependency_resolver


_REGISTRY: Dict[InterfaceType, List[StartupHookEntry]] = {}


def register_startup_hook(
    interface_cls: InterfaceType,
    hook: StartupHook,
    *,
    dependency_resolver: DependencyResolver | None = None,
) -> None:
    """
    Register a startup hook associated with an interface type.

    If the same `hook` with an equal `dependency_resolver` is already registered for `interface_cls`, registration is ignored.

    Parameters:
        interface_cls (InterfaceType): The interface type the hook applies to.
        hook (StartupHook): Callable invoked at startup for implementations of the interface.
        dependency_resolver (DependencyResolver | None): Optional callable that, given an interface type,
            returns a set of interface types this interface depends on (used to determine execution ordering).
    """
    entries = _REGISTRY.setdefault(interface_cls, [])
    if not any(
        entry.hook is hook and entry.dependency_resolver == dependency_resolver
        for entry in entries
    ):
        entries.append(StartupHookEntry(hook, dependency_resolver))


def iter_interface_startup_hooks() -> Iterator[Tuple[InterfaceType, StartupHook]]:
    """
    Yield pairs of registered interface types and their startup hooks.

    Returns:
        Iterator[Tuple[InterfaceType, StartupHook]]: An iterator that yields (interface_cls, hook) tuples for every startup hook registered for an interface, preserving the registration order for hooks of a given interface.
    """
    for interface_cls, entries in _REGISTRY.items():
        for entry in entries:
            yield interface_cls, entry.hook


def registered_startup_hooks() -> Dict[InterfaceType, Tuple[StartupHook, ...]]:
    """
    Return a shallow snapshot of registered startup hooks keyed by interface type.

    Returns:
        mapping (Dict[InterfaceType, Tuple[StartupHook, ...]]): A dictionary mapping each interface type to a tuple of its registered startup hook callables. The tuple preserves the hooks' registration order.
    """
    return {
        interface: tuple(entry.hook for entry in entries)
        for interface, entries in _REGISTRY.items()
    }


def registered_startup_hook_entries() -> Dict[
    InterfaceType, Tuple[StartupHookEntry, ...]
]:
    """
    Provide a shallow snapshot of registered startup hook entries keyed by interface type.

    Returns:
        A dict mapping each interface type to a tuple of its registered StartupHookEntry objects,
        preserving the registration order for each interface.
    """
    return {interface: tuple(entries) for interface, entries in _REGISTRY.items()}


def clear_startup_hooks() -> None:
    """
    Clear the internal registry of all registered startup hooks.
    """
    _REGISTRY.clear()


def order_interfaces_by_dependency(
    interfaces: Sequence[InterfaceType],
    dependency_resolver: DependencyResolver | None,
) -> List[InterfaceType]:
    """
    Produce an ordering of the given interfaces that respects dependencies when a resolver is provided.

    When `dependency_resolver` is None or falsey, the input order is preserved. When a resolver is provided, interfaces are ordered so that for any interface A that depends on B (and B is present in the input list), B appears before A when possible; interfaces involved in cycles or otherwise unresolved dependencies are appended after the ordered portion while preserving their presence.

    Parameters:
        interfaces (List[InterfaceType]): The list of interface types to order. Order among inputs is used as a stable tie-breaker.
        dependency_resolver (DependencyResolver | None): Optional callable that returns the set of interfaces an interface depends on; dependencies not present in `interfaces` are ignored.

    Returns:
        List[InterfaceType]: A list containing the same interfaces as `interfaces` arranged to respect dependencies when possible.
    """
    interface_list = list(interfaces)
    if not dependency_resolver:
        return list(interface_list)

    dependencies: Dict[InterfaceType, Set[InterfaceType]] = {
        iface: {dep for dep in dependency_resolver(iface) if dep in interface_list}
        for iface in interface_list
    }

    incoming_counts: Dict[InterfaceType, int] = {
        iface: len(dependencies[iface]) for iface in interface_list
    }
    ordered: List[InterfaceType] = []

    def _queue_items() -> List[InterfaceType]:
        """
        Collect interfaces that currently have zero incoming dependencies and are not yet ordered.

        Returns:
            List[InterfaceType]: Interfaces whose incoming dependency count is zero (or missing) and which are not present in `ordered`.
        """
        return [
            iface
            for iface in interface_list
            if incoming_counts.get(iface, 0) == 0 and iface not in ordered
        ]

    queue = _queue_items()
    while queue:
        iface = queue.pop(0)
        ordered.append(iface)
        for dep_iface, deps in dependencies.items():
            if iface in deps:
                incoming_counts[dep_iface] = incoming_counts.get(dep_iface, 0) - 1
        queue = _queue_items()

    for iface in interface_list:
        if iface not in ordered:
            ordered.append(iface)

    return ordered
