"""Registry for capability-provided startup hooks."""

from __future__ import annotations

from typing import Callable, Dict, Iterator, List, Tuple, Type, Set

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
    Register a startup hook for an interface class.

    If the same `hook` and dependency_resolver are already registered for `interface_cls`, it will not be added again.

    Parameters:
        interface_cls (InterfaceType): The interface class that the hook is associated with.
        hook (StartupHook): A callable to be invoked at startup for implementations of the interface.
        dependency_resolver (Callable | None): Optional resolver returning interface dependencies.
    """
    entries = _REGISTRY.setdefault(interface_cls, [])
    if not any(
        entry.hook is hook and entry.dependency_resolver == dependency_resolver
        for entry in entries
    ):
        entries.append(StartupHookEntry(hook, dependency_resolver))


def iter_interface_startup_hooks() -> Iterator[Tuple[InterfaceType, StartupHook]]:
    """
    Iterate over all registered startup hooks paired with their interface classes.

    Returns:
        iterator of tuples (InterfaceType, StartupHook): Each yielded tuple contains an interface class and one of its registered startup hooks.
    """
    for interface_cls, entries in _REGISTRY.items():
        for entry in entries:
            yield interface_cls, entry.hook


def registered_startup_hooks() -> Dict[InterfaceType, Tuple[StartupHook, ...]]:
    """
    Provide a shallow snapshot of currently registered startup hooks keyed by interface type (callables only).
    """
    return {
        interface: tuple(entry.hook for entry in entries)
        for interface, entries in _REGISTRY.items()
    }


def registered_startup_hook_entries() -> Dict[
    InterfaceType, Tuple[StartupHookEntry, ...]
]:
    """
    Provide startup hook entries (including dependency resolvers) keyed by interface type.
    """
    return {interface: tuple(entries) for interface, entries in _REGISTRY.items()}


def clear_startup_hooks() -> None:
    """
    Clear the internal registry of all registered startup hooks.
    """
    _REGISTRY.clear()


def order_interfaces_by_dependency(
    interfaces: List[InterfaceType],
    dependency_resolver: DependencyResolver | None,
) -> List[InterfaceType]:
    """
    Topologically order interfaces using the provided dependency_resolver; fall back to preserving the input order.
    """
    if not dependency_resolver:
        return list(interfaces)

    dependencies: Dict[InterfaceType, Set[InterfaceType]] = {
        iface: {dep for dep in dependency_resolver(iface) if dep in interfaces}
        for iface in interfaces
    }

    incoming_counts: Dict[InterfaceType, int] = {
        iface: len(dependencies[iface]) for iface in interfaces
    }
    ordered: List[InterfaceType] = []

    def _queue_items() -> List[InterfaceType]:
        return [
            iface
            for iface in interfaces
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

    for iface in interfaces:
        if iface not in ordered:
            ordered.append(iface)

    return ordered
