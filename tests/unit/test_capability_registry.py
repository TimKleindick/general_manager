"""Comprehensive tests for the capability registry."""

from __future__ import annotations

from types import MappingProxyType
from typing import ClassVar

import pytest

import general_manager.interface.capabilities.registry as capability_registry
from general_manager.interface.capabilities.registry import CapabilityRegistry
from general_manager.interface.base_interface import InterfaceBase


class CapabilityIterationError(RuntimeError):
    """Raised when a test capability iterable fails."""


class MockCapability:
    """Mock capability for testing."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.setup_called = False
        self.teardown_called = False

    def setup(self, interface_cls: type[InterfaceBase]) -> None:
        self.setup_called = True

    def teardown(self, interface_cls: type[InterfaceBase]) -> None:
        self.teardown_called = True


class DummyInterface(InterfaceBase):
    """Test interface."""

    _interface_type = "test"
    input_fields: ClassVar[dict[str, object]] = {}


class AnotherInterface(InterfaceBase):
    """Another test interface."""

    _interface_type = "test2"
    input_fields: ClassVar[dict[str, object]] = {}


def broken_capability_names():
    yield "history"
    raise CapabilityIterationError


def broken_capability_instances(capability: MockCapability):
    yield capability
    raise CapabilityIterationError


def test_capability_registry_public_exports() -> None:
    """The registry module exposes its public registry type explicitly."""
    assert capability_registry.__all__ == ["CapabilityRegistry"]


def test_registry_initialization() -> None:
    """Test that registry initializes with empty state."""
    registry = CapabilityRegistry()

    assert registry.get(DummyInterface) == frozenset()
    assert registry.instances(DummyInterface) == tuple()


def test_register_single_capability() -> None:
    """Test registering a single capability for an interface."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read"])

    assert registry.get(DummyInterface) == frozenset(["read"])


def test_register_multiple_capabilities() -> None:
    """Test registering multiple capabilities at once."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read", "write", "delete"])

    result = registry.get(DummyInterface)
    assert result == frozenset(["read", "write", "delete"])


def test_register_incremental_merge() -> None:
    """Test that subsequent registrations merge by default."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read"])
    registry.register(DummyInterface, ["write"])

    assert registry.get(DummyInterface) == frozenset(["read", "write"])


def test_register_with_replace() -> None:
    """Test that replace=True overwrites previous registrations."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read", "write", "delete"])
    registry.register(DummyInterface, ["query"], replace=True)

    assert registry.get(DummyInterface) == frozenset(["query"])


def test_register_empty_capabilities() -> None:
    """Test registering an empty set of capabilities."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, [])

    # Empty registration should create an entry
    assert registry.get(DummyInterface) == frozenset()


def test_register_replace_with_empty_capabilities_clears_binding() -> None:
    """Replacing with an empty iterable should preserve an empty binding."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read", "query"])

    registry.register(DummyInterface, [], replace=True)

    assert registry.get(DummyInterface) == frozenset()
    assert DummyInterface in registry.snapshot()


def test_register_duplicate_names_collapse() -> None:
    """Declared capability names are stored as a set."""
    registry = CapabilityRegistry()

    registry.register(DummyInterface, ["read", "read", "query"])

    assert registry.get(DummyInterface) == frozenset(["read", "query"])


def test_register_merge_consumes_iterable_before_mutating_existing_binding() -> None:
    """Merge mode should not partially update existing state on iteration errors."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read"])

    with pytest.raises(CapabilityIterationError):
        registry.register(DummyInterface, broken_capability_names())

    assert registry.get(DummyInterface) == frozenset(["read"])


def test_register_replace_consumes_iterable_before_replacing_existing_binding() -> None:
    """Replace mode should leave existing state unchanged on iteration errors."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read"])

    with pytest.raises(CapabilityIterationError):
        registry.register(DummyInterface, broken_capability_names(), replace=True)

    assert registry.get(DummyInterface) == frozenset(["read"])


def test_get_returns_defensive_frozenset() -> None:
    """Callers should not be able to mutate internal declared-name state."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read"])

    result = registry.get(DummyInterface)
    assert isinstance(result, frozenset)
    assert result | frozenset(["query"]) == frozenset(["read", "query"])
    assert registry.get(DummyInterface) == frozenset(["read"])


def test_get_unregistered_interface() -> None:
    """Test getting capabilities for an unregistered interface."""
    registry = CapabilityRegistry()
    result = registry.get(DummyInterface)

    assert result == frozenset()
    assert isinstance(result, frozenset)


def test_bind_instances() -> None:
    """Test binding capability instances to an interface."""
    registry = CapabilityRegistry()
    cap1 = MockCapability("read")
    cap2 = MockCapability("write")

    registry.bind_instances(DummyInterface, [cap1, cap2])

    instances = registry.instances(DummyInterface)
    assert len(instances) == 2
    assert cap1 in instances
    assert cap2 in instances


def test_bind_instances_replaces_previous() -> None:
    """Test that bind_instances replaces previous bindings."""
    registry = CapabilityRegistry()
    cap1 = MockCapability("read")
    cap2 = MockCapability("write")
    cap3 = MockCapability("delete")

    registry.bind_instances(DummyInterface, [cap1, cap2])
    registry.bind_instances(DummyInterface, [cap3])

    instances = registry.instances(DummyInterface)
    assert len(instances) == 1
    assert cap3 in instances
    assert cap1 not in instances


def test_bind_instances_consumes_iterable_before_replacing_existing_binding() -> None:
    """Instance replacement should be atomic with respect to iterable failures."""
    registry = CapabilityRegistry()
    cap1 = MockCapability("read")
    cap2 = MockCapability("query")
    registry.bind_instances(DummyInterface, [cap1])

    with pytest.raises(CapabilityIterationError):
        registry.bind_instances(DummyInterface, broken_capability_instances(cap2))

    assert registry.instances(DummyInterface) == (cap1,)


def test_bind_instances_does_not_register_declared_names() -> None:
    """Instance bindings and declared-name bindings are independent registries."""
    registry = CapabilityRegistry()
    cap1 = MockCapability("read")

    registry.bind_instances(DummyInterface, [cap1])

    assert registry.instances(DummyInterface) == (cap1,)
    assert registry.get(DummyInterface) == frozenset()


def test_register_does_not_replace_bound_instances() -> None:
    """Changing declared names should not clear concrete capability instances."""
    registry = CapabilityRegistry()
    cap1 = MockCapability("read")
    registry.bind_instances(DummyInterface, [cap1])

    registry.register(DummyInterface, ["query"], replace=True)

    assert registry.get(DummyInterface) == frozenset(["query"])
    assert registry.instances(DummyInterface) == (cap1,)


def test_instances_unregistered_interface() -> None:
    """Test retrieving instances for an unregistered interface."""
    registry = CapabilityRegistry()
    instances = registry.instances(DummyInterface)

    assert instances == tuple()
    assert isinstance(instances, tuple)


def test_snapshot_read_only() -> None:
    """Test that snapshot returns an immutable mapping."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read", "write"])
    registry.register(AnotherInterface, ["query"])

    snapshot = registry.snapshot()

    assert isinstance(snapshot, MappingProxyType)
    assert DummyInterface in snapshot
    assert AnotherInterface in snapshot
    assert snapshot[DummyInterface] == frozenset(["read", "write"])
    assert snapshot[AnotherInterface] == frozenset(["query"])
    with pytest.raises(TypeError):
        snapshot[DummyInterface] = frozenset(["delete"])  # type: ignore[index]


def test_snapshot_values_are_immutable() -> None:
    """Snapshot values should be immutable declared-name sets."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read"])

    snapshot = registry.snapshot()

    assert isinstance(snapshot[DummyInterface], frozenset)
    assert snapshot[DummyInterface] | frozenset(["query"]) == frozenset(
        ["read", "query"]
    )
    assert snapshot[DummyInterface] == frozenset(["read"])


def test_snapshot_reflects_current_state() -> None:
    """Test that snapshot captures the current registry state."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read"])

    snapshot1 = registry.snapshot()
    assert snapshot1[DummyInterface] == frozenset(["read"])

    registry.register(DummyInterface, ["write"])
    snapshot2 = registry.snapshot()

    # Old snapshot unchanged
    assert snapshot1[DummyInterface] == frozenset(["read"])
    # New snapshot has updated data
    assert snapshot2[DummyInterface] == frozenset(["read", "write"])


def test_registry_with_multiple_interfaces() -> None:
    """Test that registry properly isolates different interfaces."""
    registry = CapabilityRegistry()

    registry.register(DummyInterface, ["read", "write"])
    registry.register(AnotherInterface, ["query", "delete"])

    assert registry.get(DummyInterface) == frozenset(["read", "write"])
    assert registry.get(AnotherInterface) == frozenset(["query", "delete"])

    # Modifying one shouldn't affect the other
    registry.register(DummyInterface, ["history"], replace=True)
    assert registry.get(DummyInterface) == frozenset(["history"])
    assert registry.get(AnotherInterface) == frozenset(["query", "delete"])


def test_bind_instances_preserves_order() -> None:
    """Test that bind_instances preserves the order of capabilities."""
    registry = CapabilityRegistry()
    caps = [MockCapability(f"cap{i}") for i in range(5)]

    registry.bind_instances(DummyInterface, caps)

    instances = registry.instances(DummyInterface)
    assert list(instances) == caps


def test_register_with_generator() -> None:
    """Test that register works with generator expressions."""
    registry = CapabilityRegistry()
    names = (f"cap{i}" for i in range(3))

    registry.register(DummyInterface, names)

    result = registry.get(DummyInterface)
    assert "cap0" in result
    assert "cap1" in result
    assert "cap2" in result


def test_snapshot_empty_registry() -> None:
    """Test snapshot of an empty registry."""
    registry = CapabilityRegistry()
    snapshot = registry.snapshot()

    assert len(snapshot) == 0
    assert isinstance(snapshot, dict) or hasattr(snapshot, "__getitem__")
