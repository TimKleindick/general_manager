"""Tests for capability data models."""

from __future__ import annotations

import pytest

from general_manager.interface.manifests.capability_models import (
    CapabilityPlan,
    CapabilityConfig,
    CapabilitySelection,
)


def test_capability_plan_initialization():
    """Test CapabilityPlan initialization."""
    plan = CapabilityPlan(
        required=frozenset(["read", "write"]),
        optional=frozenset(["delete"]),
        flags={"enable_delete": "delete"}
    )
    
    assert plan.required == frozenset(["read", "write"])
    assert plan.optional == frozenset(["delete"])
    assert plan.flags == {"enable_delete": "delete"}


def test_capability_plan_default_values():
    """Test CapabilityPlan with default values."""
    plan = CapabilityPlan()
    
    assert plan.required == frozenset()
    assert plan.optional == frozenset()
    assert isinstance(plan.flags, dict)


def test_capability_plan_post_init_conversion():
    """Test that CapabilityPlan converts inputs to frozen types."""
    plan = CapabilityPlan(
        required=["read", "write"],  # List input
        optional={"delete"},  # Set input
        flags={"key": "value"}
    )
    
    assert isinstance(plan.required, frozenset)
    assert isinstance(plan.optional, frozenset)
    from types import MappingProxyType
    assert isinstance(plan.flags, (dict, MappingProxyType))


def test_capability_plan_immutable():
    """Test that CapabilityPlan is immutable."""
    plan = CapabilityPlan(required=frozenset(["read"]))
    
    with pytest.raises((AttributeError, TypeError)):
        plan.required = frozenset(["write"])


def test_capability_config_initialization():
    """Test CapabilityConfig initialization."""
    config = CapabilityConfig(
        enabled={"notification"},
        disabled={"scheduling"},
        flags={"debug": True}
    )
    
    assert config.enabled == {"notification"}
    assert config.disabled == {"scheduling"}
    assert config.flags == {"debug": True}


def test_capability_config_default_values():
    """Test CapabilityConfig with default values."""
    config = CapabilityConfig()
    
    assert config.enabled == set()
    assert config.disabled == set()
    assert config.flags == {}


def test_capability_config_is_flag_enabled_true():
    """Test is_flag_enabled with truthy values."""
    config = CapabilityConfig(flags={
        "feature_a": True,
        "feature_b": 1,
        "feature_c": "yes",
        "feature_d": [1, 2, 3]
    })
    
    assert config.is_flag_enabled("feature_a") is True
    assert config.is_flag_enabled("feature_b") is True
    assert config.is_flag_enabled("feature_c") is True
    assert config.is_flag_enabled("feature_d") is True


def test_capability_config_is_flag_enabled_false():
    """Test is_flag_enabled with falsy values."""
    config = CapabilityConfig(flags={
        "feature_a": False,
        "feature_b": 0,
        "feature_c": "",
        "feature_d": []
    })
    
    assert config.is_flag_enabled("feature_a") is False
    assert config.is_flag_enabled("feature_b") is False
    assert config.is_flag_enabled("feature_c") is False
    assert config.is_flag_enabled("feature_d") is False


def test_capability_config_is_flag_enabled_missing():
    """Test is_flag_enabled with missing flag returns False."""
    config = CapabilityConfig(flags={"existing": True})
    
    assert config.is_flag_enabled("missing") is False


def test_capability_config_mutable():
    """Test that CapabilityConfig is mutable."""
    config = CapabilityConfig()
    
    config.enabled.add("notification")
    config.disabled.add("scheduling")
    config.flags["debug"] = True
    
    assert "notification" in config.enabled
    assert "scheduling" in config.disabled
    assert config.flags["debug"] is True


def test_capability_selection_initialization():
    """Test CapabilitySelection initialization."""
    selection = CapabilitySelection(
        required=frozenset(["read", "write"]),
        optional=frozenset(["delete", "query"]),
        activated_optional=frozenset(["delete"])
    )
    
    assert selection.required == frozenset(["read", "write"])
    assert selection.optional == frozenset(["delete", "query"])
    assert selection.activated_optional == frozenset(["delete"])


def test_capability_selection_all_property():
    """Test CapabilitySelection.all property."""
    selection = CapabilitySelection(
        required=frozenset(["read", "write"]),
        optional=frozenset(["delete", "query", "history"]),
        activated_optional=frozenset(["query", "history"])
    )
    
    all_caps = selection.all
    
    assert all_caps == frozenset(["read", "write", "query", "history"])
    assert "delete" not in all_caps  # Not activated


def test_capability_selection_all_empty_optional():
    """Test CapabilitySelection.all with no activated optional."""
    selection = CapabilitySelection(
        required=frozenset(["read"]),
        optional=frozenset(["write", "delete"]),
        activated_optional=frozenset()
    )
    
    assert selection.all == frozenset(["read"])


def test_capability_selection_all_empty_required():
    """Test CapabilitySelection.all with no required."""
    selection = CapabilitySelection(
        required=frozenset(),
        optional=frozenset(["notify"]),
        activated_optional=frozenset(["notify"])
    )
    
    assert selection.all == frozenset(["notify"])


def test_capability_selection_post_init_conversion():
    """Test that CapabilitySelection converts inputs to frozensets."""
    selection = CapabilitySelection(
        required=["read"],
        optional={"write"},
        activated_optional=("delete",)
    )
    
    assert isinstance(selection.required, frozenset)
    assert isinstance(selection.optional, frozenset)
    assert isinstance(selection.activated_optional, frozenset)


def test_capability_selection_immutable():
    """Test that CapabilitySelection is immutable."""
    selection = CapabilitySelection(
        required=frozenset(["read"]),
        optional=frozenset(["write"]),
        activated_optional=frozenset()
    )
    
    with pytest.raises((AttributeError, TypeError)):
        selection.required = frozenset(["write"])


def test_capability_plan_with_overlapping_sets():
    """Test CapabilityPlan with capabilities in both required and optional."""
    # This should be allowed at the model level; validation happens elsewhere
    plan = CapabilityPlan(
        required=frozenset(["read"]),
        optional=frozenset(["read", "write"])
    )
    
    assert "read" in plan.required
    assert "read" in plan.optional


def test_capability_selection_with_non_optional_activated():
    """Test CapabilitySelection with activated capabilities not in optional list."""
    # This should be allowed at the model level; validation happens elsewhere
    selection = CapabilitySelection(
        required=frozenset(["read"]),
        optional=frozenset(["write"]),
        activated_optional=frozenset(["delete"])  # Not in optional
    )
    
    # The model doesn't validate, just stores
    assert "delete" in selection.activated_optional
    assert "delete" in selection.all