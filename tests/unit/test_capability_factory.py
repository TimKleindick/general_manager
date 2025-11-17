"""Tests for capability factory functions."""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from general_manager.interface.capabilities.factory import (
    build_capabilities,
    CAPABILITY_CLASS_MAP,
)
from general_manager.interface.capabilities.builtin import (
    ReadCapability,
    CreateCapability,
    UpdateCapability,
    DeleteCapability,
    HistoryCapability,
    ValidationCapability,
    NotificationCapability,
    SchedulingCapability,
    AccessControlCapability,
    ObservabilityCapability,
)


class DummyInterface:
    """Mock interface for testing."""
    pass


def test_build_capabilities_with_builtin_names():
    """Test building capabilities using built-in capability names."""
    names = ["read", "create", "update"]
    overrides = {}
    
    result = build_capabilities(DummyInterface, names, overrides)
    
    assert len(result) == 3
    assert isinstance(result[0], ReadCapability)
    assert isinstance(result[1], CreateCapability)
    assert isinstance(result[2], UpdateCapability)


def test_build_capabilities_all_builtins():
    """Test building all built-in capabilities."""
    names = list(CAPABILITY_CLASS_MAP.keys())
    overrides = {}
    
    result = build_capabilities(DummyInterface, names, overrides)
    
    assert len(result) == len(CAPABILITY_CLASS_MAP)
    for cap in result:
        assert hasattr(cap, "name")
        assert hasattr(cap, "setup")


def test_build_capabilities_with_override_class():
    """Test building capabilities with class override."""
    class CustomReadCapability(ReadCapability):
        custom_attr = "custom"
    
    names = ["read", "write"]
    overrides = {"read": CustomReadCapability}
    
    result = build_capabilities(DummyInterface, names, overrides)
    
    assert len(result) == 2
    assert isinstance(result[0], CustomReadCapability)
    assert hasattr(result[0], "custom_attr")


def test_build_capabilities_with_override_callable():
    """Test building capabilities with callable override."""
    custom_instance = ReadCapability()
    custom_instance.custom_flag = True
    
    names = ["read"]
    overrides = {"read": lambda: custom_instance}
    
    result = build_capabilities(DummyInterface, names, overrides)
    
    assert len(result) == 1
    assert result[0] is custom_instance
    assert result[0].custom_flag is True


def test_build_capabilities_unknown_name_raises():
    """Test that unknown capability name raises KeyError."""
    names = ["unknown_capability"]
    overrides = {}
    
    with pytest.raises(KeyError, match="Unknown capability"):
        build_capabilities(DummyInterface, names, overrides)


def test_build_capabilities_empty_names():
    """Test building capabilities with empty names list."""
    result = build_capabilities(DummyInterface, [], {})
    
    assert result == []


def test_build_capabilities_preserves_order():
    """Test that capability order is preserved."""
    names = ["delete", "create", "read", "update"]
    overrides = {}
    
    result = build_capabilities(DummyInterface, names, overrides)
    
    assert len(result) == 4
    assert isinstance(result[0], DeleteCapability)
    assert isinstance(result[1], CreateCapability)
    assert isinstance(result[2], ReadCapability)
    assert isinstance(result[3], UpdateCapability)


def test_build_capabilities_mixed_overrides():
    """Test building with some names overridden and some not."""
    class CustomCreate(CreateCapability):
        pass
    
    names = ["read", "create", "delete"]
    overrides = {"create": CustomCreate}
    
    result = build_capabilities(DummyInterface, names, overrides)
    
    assert len(result) == 3
    assert isinstance(result[0], ReadCapability)
    assert isinstance(result[1], CustomCreate)
    assert isinstance(result[2], DeleteCapability)


def test_capability_class_map_completeness():
    """Test that CAPABILITY_CLASS_MAP contains all expected entries."""
    expected_names = [
        "read", "create", "update", "delete",
        "history", "validation",
        "notification", "scheduling", "access_control", "observability"
    ]
    
    for name in expected_names:
        assert name in CAPABILITY_CLASS_MAP
        assert issubclass(CAPABILITY_CLASS_MAP[name], type)


def test_build_capabilities_duplicate_names():
    """Test building with duplicate capability names."""
    names = ["read", "read", "create"]
    overrides = {}
    
    result = build_capabilities(DummyInterface, names, overrides)
    
    # Should create separate instances even for duplicates
    assert len(result) == 3
    assert result[0] is not result[1]  # Different instances


def test_build_capabilities_override_returns_instance():
    """Test that override callable return value is used directly."""
    mock_cap = Mock()
    mock_cap.name = "custom"
    
    names = ["read"]
    overrides = {"read": lambda: mock_cap}
    
    result = build_capabilities(DummyInterface, names, overrides)
    
    assert len(result) == 1
    assert result[0] is mock_cap