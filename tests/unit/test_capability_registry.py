"""Comprehensive tests for the capability registry."""

from __future__ import annotations

from typing import ClassVar

from general_manager.interface.capabilities.registry import CapabilityRegistry
from general_manager.interface.base_interface import InterfaceBase


class MockCapability:
    """Mock capability for testing."""

    def __init__(self, name: str):
        self.name = name
        self.setup_called = False
        self.teardown_called = False

    def setup(self, interface_cls):
        self.setup_called = True

    def teardown(self, interface_cls):
        self.teardown_called = True


class DummyInterface(InterfaceBase):
    """Test interface."""

    _interface_type = "test"
    input_fields: ClassVar[dict[str, object]] = {}


class AnotherInterface(InterfaceBase):
    """Another test interface."""

    _interface_type = "test2"
    input_fields: ClassVar[dict[str, object]] = {}


def test_registry_initialization():
    """Test that registry initializes with empty state."""
    registry = CapabilityRegistry()

    assert registry.get(DummyInterface) == frozenset()
    assert registry.instances(DummyInterface) == tuple()


def test_register_single_capability():
    """Test registering a single capability for an interface."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read"])

    assert registry.get(DummyInterface) == frozenset(["read"])


def test_register_multiple_capabilities():
    """Test registering multiple capabilities at once."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read", "write", "delete"])

    result = registry.get(DummyInterface)
    assert result == frozenset(["read", "write", "delete"])


def test_register_incremental_merge():
    """Test that subsequent registrations merge by default."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read"])
    registry.register(DummyInterface, ["write"])

    assert registry.get(DummyInterface) == frozenset(["read", "write"])


def test_register_with_replace():
    """Test that replace=True overwrites previous registrations."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read", "write", "delete"])
    registry.register(DummyInterface, ["query"], replace=True)

    assert registry.get(DummyInterface) == frozenset(["query"])


def test_register_empty_capabilities():
    """Test registering an empty set of capabilities."""
    registry = CapabilityRegistry()
    registry.register(DummyInterface, [])

    # Empty registration should create an entry
    assert registry.get(DummyInterface) == frozenset()


def test_get_unregistered_interface():
    """Test getting capabilities for an unregistered interface."""
    registry = CapabilityRegistry()
    result = registry.get(DummyInterface)

    assert result == frozenset()
    assert isinstance(result, frozenset)


def test_bind_instances():
    """Test binding capability instances to an interface."""
    registry = CapabilityRegistry()
    cap1 = MockCapability("read")
    cap2 = MockCapability("write")

    registry.bind_instances(DummyInterface, [cap1, cap2])

    instances = registry.instances(DummyInterface)
    assert len(instances) == 2
    assert cap1 in instances
    assert cap2 in instances


def test_bind_instances_replaces_previous():
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


def test_instances_unregistered_interface():
    """Test retrieving instances for an unregistered interface."""
    registry = CapabilityRegistry()
    instances = registry.instances(DummyInterface)

    assert instances == tuple()
    assert isinstance(instances, tuple)


def test_snapshot_read_only():
    """Test that snapshot returns an immutable mapping."""
    from types import MappingProxyType

    registry = CapabilityRegistry()
    registry.register(DummyInterface, ["read", "write"])
    registry.register(AnotherInterface, ["query"])

    snapshot = registry.snapshot()

    assert isinstance(snapshot, MappingProxyType)
    assert DummyInterface in snapshot
    assert AnotherInterface in snapshot
    assert snapshot[DummyInterface] == frozenset(["read", "write"])
    assert snapshot[AnotherInterface] == frozenset(["query"])


def test_snapshot_reflects_current_state():
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


def test_registry_with_multiple_interfaces():
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


def test_bind_instances_preserves_order():
    """Test that bind_instances preserves the order of capabilities."""
    registry = CapabilityRegistry()
    caps = [MockCapability(f"cap{i}") for i in range(5)]

    registry.bind_instances(DummyInterface, caps)

    instances = registry.instances(DummyInterface)
    assert list(instances) == caps


def test_register_with_generator():
    """Test that register works with generator expressions."""
    registry = CapabilityRegistry()
    names = (f"cap{i}" for i in range(3))

    registry.register(DummyInterface, names)

    result = registry.get(DummyInterface)
    assert "cap0" in result
    assert "cap1" in result
    assert "cap2" in result


def test_snapshot_empty_registry():
    """Test snapshot of an empty registry."""
    registry = CapabilityRegistry()
    snapshot = registry.snapshot()

    assert len(snapshot) == 0
    assert isinstance(snapshot, dict) or hasattr(snapshot, "__getitem__")
