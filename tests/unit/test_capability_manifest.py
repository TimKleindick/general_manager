"""Tests for capability manifest and resolution."""

from __future__ import annotations

import pytest

from general_manager.interface.manifests.capability_manifest import (
    CapabilityManifest,
    CAPABILITY_MANIFEST,
    DEFAULT_FLAG_MAPPING,
)
from general_manager.interface.manifests.capability_models import CapabilityPlan
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.interface.interfaces.database import DatabaseInterface
from general_manager.interface.interfaces.existing_model import ExistingModelInterface
from general_manager.interface.interfaces.read_only import ReadOnlyInterface
from general_manager.interface.interfaces.calculation import CalculationInterface


def test_capability_manifest_resolve_simple():
    """Test resolving a capability plan for a single interface."""
    plan = CapabilityPlan(
        required=frozenset(["read", "write"]),
        optional=frozenset(["delete"]),
        flags={"enable_delete": "delete"}
    )
    manifest = CapabilityManifest(plans={InterfaceBase: plan})
    
    resolved = manifest.resolve(InterfaceBase)
    
    assert resolved.required == frozenset(["read", "write"])
    assert resolved.optional == frozenset(["delete"])
    assert resolved.flags == {"enable_delete": "delete"}


def test_capability_manifest_resolve_inheritance():
    """Test resolving with inheritance aggregation."""
    base_plan = CapabilityPlan(
        required=frozenset(["read"]),
        optional=frozenset(["cache"]),
        flags={}
    )
    derived_plan = CapabilityPlan(
        required=frozenset(["write"]),
        optional=frozenset(["audit"]),
        flags={"enable_audit": "audit"}
    )
    
    class BaseInterface(InterfaceBase):
        pass
    
    class DerivedInterface(BaseInterface):
        pass
    
    manifest = CapabilityManifest(plans={
        BaseInterface: base_plan,
        DerivedInterface: derived_plan
    })
    
    resolved = manifest.resolve(DerivedInterface)
    
    # Should merge capabilities from both classes
    assert "read" in resolved.required
    assert "write" in resolved.required
    assert "cache" in resolved.optional
    assert "audit" in resolved.optional
    assert "enable_audit" in resolved.flags


def test_capability_manifest_contains():
    """Test __contains__ method."""
    plan = CapabilityPlan(required=frozenset(["read"]))
    manifest = CapabilityManifest(plans={InterfaceBase: plan})
    
    assert InterfaceBase in manifest
    assert DatabaseInterface not in manifest  # Not in this custom manifest


def test_default_manifest_database_interface():
    """Test default manifest for DatabaseInterface."""
    plan = CAPABILITY_MANIFEST.resolve(DatabaseInterface)
    
    # Required capabilities from OrmInterfaceBase + DatabaseInterface
    assert "orm_support" in plan.required
    assert "orm_lifecycle" in plan.required
    assert "orm_mutation" in plan.required
    assert "create" in plan.required
    assert "update" in plan.required
    assert "delete" in plan.required
    assert "history" in plan.required
    assert "read" in plan.required
    assert "validation" in plan.required
    assert "query" in plan.required
    
    # Optional capabilities
    assert "notification" in plan.optional
    assert "scheduling" in plan.optional
    assert "access_control" in plan.optional


def test_default_manifest_read_only_interface():
    """Test default manifest for ReadOnlyInterface."""
    plan = CAPABILITY_MANIFEST.resolve(ReadOnlyInterface)
    
    # Should include read_only_management
    assert "read_only_management" in plan.required
    assert "orm_support" in plan.required
    assert "read" in plan.required
    assert "validation" in plan.required
    
    # Should not include write capabilities
    assert "create" not in plan.required
    assert "update" not in plan.required
    assert "delete" not in plan.required
    assert "orm_mutation" not in plan.required


def test_default_manifest_calculation_interface():
    """Test default manifest for CalculationInterface."""
    plan = CAPABILITY_MANIFEST.resolve(CalculationInterface)
    
    assert "read" in plan.required
    assert "validation" in plan.required
    assert "observability" in plan.required
    assert "query" in plan.required
    assert "calculation_lifecycle" in plan.required
    
    # Should not have ORM capabilities
    assert "orm_support" not in plan.required
    assert "orm_lifecycle" not in plan.required


def test_default_manifest_existing_model_interface():
    """Test default manifest for ExistingModelInterface."""
    plan = CAPABILITY_MANIFEST.resolve(ExistingModelInterface)
    
    # Should include existing_model_resolution
    assert "existing_model_resolution" in plan.required
    
    # Should include writable ORM capabilities
    assert "orm_mutation" in plan.required
    assert "create" in plan.required
    assert "update" in plan.required
    assert "delete" in plan.required
    assert "history" in plan.required


def test_default_flag_mapping():
    """Test that default flag mapping is correct."""
    assert "notifications" in DEFAULT_FLAG_MAPPING
    assert DEFAULT_FLAG_MAPPING["notifications"] == "notification"
    assert DEFAULT_FLAG_MAPPING["scheduling"] == "scheduling"
    assert DEFAULT_FLAG_MAPPING["access_control"] == "access_control"
    assert DEFAULT_FLAG_MAPPING["observability"] == "observability"


def test_manifest_resolve_empty_plans():
    """Test resolving when no plans exist in the hierarchy."""
    class UnknownInterface(InterfaceBase):
        pass
    
    manifest = CapabilityManifest(plans={})
    resolved = manifest.resolve(UnknownInterface)
    
    assert resolved.required == frozenset()
    assert resolved.optional == frozenset()
    assert resolved.flags == {}


def test_manifest_resolve_multiple_inheritance():
    """Test resolving with multiple inheritance levels."""
    plan1 = CapabilityPlan(required=frozenset(["a"]), optional=frozenset(["b"]))
    plan2 = CapabilityPlan(required=frozenset(["c"]), optional=frozenset(["d"]))
    plan3 = CapabilityPlan(required=frozenset(["e"]), optional=frozenset(["f"]))
    
    class Level1(InterfaceBase):
        pass
    
    class Level2(Level1):
        pass
    
    class Level3(Level2):
        pass
    
    manifest = CapabilityManifest(plans={
        Level1: plan1,
        Level2: plan2,
        Level3: plan3
    })
    
    resolved = manifest.resolve(Level3)
    
    assert "a" in resolved.required
    assert "c" in resolved.required
    assert "e" in resolved.required
    assert "b" in resolved.optional
    assert "d" in resolved.optional
    assert "f" in resolved.optional


def test_manifest_flag_merging():
    """Test that flags are properly merged across inheritance."""
    base_plan = CapabilityPlan(flags={"flag_a": "cap_a"})
    derived_plan = CapabilityPlan(flags={"flag_b": "cap_b"})
    
    class Base(InterfaceBase):
        pass
    
    class Derived(Base):
        pass
    
    manifest = CapabilityManifest(plans={
        Base: base_plan,
        Derived: derived_plan
    })
    
    resolved = manifest.resolve(Derived)
    
    assert "flag_a" in resolved.flags
    assert "flag_b" in resolved.flags
    assert resolved.flags["flag_a"] == "cap_a"
    assert resolved.flags["flag_b"] == "cap_b"


def test_manifest_flag_override():
    """Test that child class flags override parent flags."""
    base_plan = CapabilityPlan(flags={"common_flag": "cap_a"})
    derived_plan = CapabilityPlan(flags={"common_flag": "cap_b"})
    
    class Base(InterfaceBase):
        pass
    
    class Derived(Base):
        pass
    
    manifest = CapabilityManifest(plans={
        Base: base_plan,
        Derived: derived_plan
    })
    
    resolved = manifest.resolve(Derived)
    
    # Later in MRO should win
    assert resolved.flags["common_flag"] == "cap_b"