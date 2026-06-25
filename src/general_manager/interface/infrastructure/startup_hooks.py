"""Registry for capability-provided startup hooks."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence, Set as AbstractSet
from dataclasses import dataclass

StartupHook = Callable[[], None]
DependencyResolver = Callable[[type[object]], AbstractSet[type[object]]]
InterfaceType = type[object]


@dataclass(frozen=True, slots=True)
class StartupHookEntry:
    """
    Immutable startup-hook registration record.

    Parameters:
        hook: Zero-argument callable invoked by the startup runner.
        dependency_resolver: Optional callable that receives an interface type and
            returns the interface types that interface depends on. When omitted,
            the hook participates only in registration-order execution.
    """

    hook: StartupHook
    dependency_resolver: DependencyResolver | None


_REGISTRY: dict[InterfaceType, list[StartupHookEntry]] = {}


def register_startup_hook(
    interface_cls: InterfaceType,
    hook: StartupHook,
    *,
    dependency_resolver: DependencyResolver | None = None,
) -> None:
    """
    Register a startup hook associated with an interface type.

    Registrations are kept in insertion order per interface. If the same hook
    object is already registered for the same interface with the same dependency
    resolver object, the second registration is ignored. The function only records the
    hook; exceptions from the hook or resolver can occur later when callers run
    the registered hooks.

    Parameters:
        interface_cls: The interface type the hook applies to.
        hook: Callable invoked at startup for implementations of the interface.
        dependency_resolver: Optional callable that returns the interface types
            `interface_cls` depends on. Hooks registered with the same resolver
            are grouped and ordered together by the startup runner.
    """
    entries = _REGISTRY.setdefault(interface_cls, [])
    if not any(
        entry.hook is hook and entry.dependency_resolver is dependency_resolver
        for entry in entries
    ):
        entries.append(StartupHookEntry(hook, dependency_resolver))


def iter_interface_startup_hooks() -> Iterator[tuple[InterfaceType, StartupHook]]:
    """
    Yield registered interface/hook pairs without dependency ordering.

    This iterator preserves the registry's insertion order: interfaces appear in
    the order they were first registered and hooks appear in per-interface
    registration order. It intentionally omits dependency-resolver metadata; use
    `registered_startup_hook_entries()` when runner-like dependency grouping is
    required.

    Returns:
        Iterator of `(interface_cls, hook)` tuples.
    """
    for interface_cls, entries in _REGISTRY.items():
        for entry in entries:
            yield interface_cls, entry.hook


def registered_startup_hooks() -> dict[InterfaceType, tuple[StartupHook, ...]]:
    """
    Return a detached registry snapshot keyed by interface type.

    Returns:
        Mapping from each interface class to its startup hook callables. The
        returned dictionary is detached from the registry and each hook sequence
        is a tuple preserving per-interface registration order.
    """
    return {
        interface: tuple(entry.hook for entry in entries)
        for interface, entries in _REGISTRY.items()
    }


def registered_startup_hook_entries() -> dict[
    InterfaceType, tuple[StartupHookEntry, ...]
]:
    """
    Return a detached snapshot including dependency resolvers.

    Returns:
        Mapping from each interface class to immutable `StartupHookEntry`
        records. The returned dictionary is detached from the registry and the
        tuple preserves per-interface registration order.
    """
    return {interface: tuple(entries) for interface, entries in _REGISTRY.items()}


def clear_startup_hooks() -> None:
    """
    Clear every registered startup hook.

    This is intended for tests and bootstrap reset flows. It mutates only the
    process-local registry and does not affect previously returned snapshot
    dictionaries.
    """
    _REGISTRY.clear()


def order_interfaces_by_dependency(
    interfaces: Sequence[InterfaceType],
    dependency_resolver: DependencyResolver | None,
) -> list[InterfaceType]:
    """
    Order interface classes so dependencies appear before dependents.

    When `dependency_resolver` is `None`, the input order is returned unchanged.
    Otherwise, the resolver is called once for each input interface class and
    may return any set-like collection of interface classes. Dependencies not
    present in `interfaces` are ignored. Cycles and self-dependencies do not
    raise; affected interfaces are appended after the acyclic ordered portion in
    their original relative order. Resolver exceptions propagate to the caller.

    Parameters:
        interfaces: Interface classes to order. The original order is used as a
            stable tie-breaker and as the fallback order for unresolved cycles.
        dependency_resolver: Optional callable that returns the interface
            classes each input interface depends on.

    Returns:
        Ordered list of interface classes. With a resolver, repeated interface
        classes collapse to the first ordered occurrence because dependency
        tracking is keyed by interface class.
    """
    interface_list = list(interfaces)
    if not dependency_resolver:
        return list(interface_list)

    dependencies: dict[InterfaceType, set[InterfaceType]] = {
        iface: {dep for dep in dependency_resolver(iface) if dep in interface_list}
        for iface in interface_list
    }

    incoming_counts: dict[InterfaceType, int] = {
        iface: len(dependencies[iface]) for iface in interface_list
    }
    ordered: list[InterfaceType] = []

    def _queue_items() -> list[InterfaceType]:
        """
        Collect unordered interfaces with no remaining in-list dependencies.

        Returns:
            Interfaces whose incoming dependency count is zero and which are
            not already present in `ordered`.
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
