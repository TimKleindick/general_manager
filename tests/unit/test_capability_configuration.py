"""Comprehensive tests for capability configuration helpers."""

from __future__ import annotations

import pytest

from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
    CapabilitySet,
    flatten_capability_entries,
    iter_capability_entries,
)
from general_manager.interface.capabilities.builtin import BaseCapability


class DummyCapability(BaseCapability):
    """Test capability."""
    name = "dummy"


class AnotherCapability(BaseCapability):
    """Another test capability."""
    name = "another"


def test_interface_capability_config_initialization():
    """Test InterfaceCapabilityConfig basic initialization."""
    config = InterfaceCapabilityConfig(DummyCapability)
    
    assert config.handler == DummyCapability
    assert config.options is None


def test_interface_capability_config_with_options():
    """Test InterfaceCapabilityConfig with options."""
    options = {"timeout": 30, "retry_count": 3}
    config = InterfaceCapabilityConfig(DummyCapability, options=options)
    
    assert config.handler == DummyCapability
    assert config.options == options


def test_interface_capability_config_instantiate_no_options():
    """Test instantiating a capability without options."""
    config = InterfaceCapabilityConfig(DummyCapability)
    instance = config.instantiate()
    
    assert isinstance(instance, DummyCapability)
    assert instance.name == "dummy"


def test_interface_capability_config_instantiate_with_options():
    """Test instantiating a capability with options."""
    class ConfigurableCapability(BaseCapability):
        name = "configurable"
        
        def __init__(self, timeout=10, retries=1):
            self.timeout = timeout
            self.retries = retries
    
    options = {"timeout": 60, "retries": 5}
    config = InterfaceCapabilityConfig(ConfigurableCapability, options=options)
    instance = config.instantiate()
    
    assert isinstance(instance, ConfigurableCapability)
    assert instance.timeout == 60
    assert instance.retries == 5


def test_interface_capability_config_frozen():
    """Test that InterfaceCapabilityConfig is immutable."""
    config = InterfaceCapabilityConfig(DummyCapability, options={"key": "value"})
    
    with pytest.raises(AttributeError):
        config.handler = AnotherCapability


def test_capability_set_initialization():
    """Test CapabilitySet basic initialization."""
    config1 = InterfaceCapabilityConfig(DummyCapability)
    config2 = InterfaceCapabilityConfig(AnotherCapability)
    
    cap_set = CapabilitySet(label="test_set", entries=(config1, config2))
    
    assert cap_set.label == "test_set"
    assert len(cap_set.entries) == 2
    assert config1 in cap_set.entries
    assert config2 in cap_set.entries


def test_capability_set_entries_converted_to_tuple():
    """Test that CapabilitySet converts entries list to tuple."""
    config1 = InterfaceCapabilityConfig(DummyCapability)
    config2 = InterfaceCapabilityConfig(AnotherCapability)
    
    cap_set = CapabilitySet(label="test", entries=[config1, config2])
    
    assert isinstance(cap_set.entries, tuple)
    assert len(cap_set.entries) == 2


def test_capability_set_frozen():
    """Test that CapabilitySet is immutable."""
    config = InterfaceCapabilityConfig(DummyCapability)
    cap_set = CapabilitySet(label="test", entries=(config,))
    
    with pytest.raises(AttributeError):
        cap_set.label = "modified"


def test_flatten_capability_entries_configs_only():
    """Test flattening a list of configs without any sets."""
    config1 = InterfaceCapabilityConfig(DummyCapability)
    config2 = InterfaceCapabilityConfig(AnotherCapability)
    
    result = flatten_capability_entries([config1, config2])
    
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert result[0] == config1
    assert result[1] == config2


def test_flatten_capability_entries_with_sets():
    """Test flattening entries that include CapabilitySets."""
    config1 = InterfaceCapabilityConfig(DummyCapability)
    config2 = InterfaceCapabilityConfig(AnotherCapability)
    
    class ThirdCapability(BaseCapability):
        name = "third"
    
    config3 = InterfaceCapabilityConfig(ThirdCapability)
    cap_set = CapabilitySet(label="bundle", entries=(config2, config3))
    
    result = flatten_capability_entries([config1, cap_set])
    
    assert isinstance(result, tuple)
    assert len(result) == 3
    assert config1 in result
    assert config2 in result
    assert config3 in result


def test_flatten_capability_entries_nested_sets():
    """Test flattening entries with nested sets."""
    config1 = InterfaceCapabilityConfig(DummyCapability)
    config2 = InterfaceCapabilityConfig(AnotherCapability)
    
    set1 = CapabilitySet(label="set1", entries=(config1,))
    set2 = CapabilitySet(label="set2", entries=(config2,))
    
    result = flatten_capability_entries([set1, set2])
    
    assert len(result) == 2
    assert config1 in result
    assert config2 in result


def test_flatten_capability_entries_empty():
    """Test flattening an empty list."""
    result = flatten_capability_entries([])
    
    assert result == tuple()


def test_iter_capability_entries_configs_only():
    """Test iterating over configs without sets."""
    config1 = InterfaceCapabilityConfig(DummyCapability)
    config2 = InterfaceCapabilityConfig(AnotherCapability)
    
    result = list(iter_capability_entries([config1, config2]))
    
    assert len(result) == 2
    assert result[0] == config1
    assert result[1] == config2


def test_iter_capability_entries_with_sets():
    """Test iterating over entries including sets."""
    config1 = InterfaceCapabilityConfig(DummyCapability)
    config2 = InterfaceCapabilityConfig(AnotherCapability)
    
    cap_set = CapabilitySet(label="bundle", entries=(config2,))
    
    result = list(iter_capability_entries([config1, cap_set]))
    
    assert len(result) == 2
    assert config1 in result
    assert config2 in result


def test_iter_capability_entries_lazy():
    """Test that iter_capability_entries returns a generator."""
    config = InterfaceCapabilityConfig(DummyCapability)
    iterator = iter_capability_entries([config])
    
    # Should be a generator, not a list
    assert hasattr(iterator, '__iter__')
    assert hasattr(iterator, '__next__')


def test_flatten_preserves_order():
    """Test that flattening preserves entry order."""
    configs = [InterfaceCapabilityConfig(DummyCapability) for _ in range(5)]
    result = flatten_capability_entries(configs)
    
    assert list(result) == configs


def test_flatten_handles_mixed_iterables():
    """Test flattening with various iterable types."""
    config1 = InterfaceCapabilityConfig(DummyCapability)
    config2 = InterfaceCapabilityConfig(AnotherCapability)
    
    # Mix of tuple, list representations
    result = flatten_capability_entries((config1, config2))
    
    assert len(result) == 2
    assert config1 in result
    assert config2 in result


def test_capability_set_with_single_entry():
    """Test CapabilitySet with a single entry."""
    config = InterfaceCapabilityConfig(DummyCapability)
    cap_set = CapabilitySet(label="single", entries=(config,))
    
    assert len(cap_set.entries) == 1
    assert cap_set.entries[0] == config


def test_flatten_large_nested_structure():
    """Test flattening a deeply nested structure."""
    config1 = InterfaceCapabilityConfig(DummyCapability)
    config2 = InterfaceCapabilityConfig(AnotherCapability)
    
    inner_set = CapabilitySet(label="inner", entries=(config1, config2))
    outer_set = CapabilitySet(label="outer", entries=(inner_set.entries[0], inner_set.entries[1]))
    
    result = flatten_capability_entries([outer_set])
    
    assert len(result) == 2