"""Tests for the manifest-driven capability builder."""

from __future__ import annotations

import pytest

from general_manager.interface.builders import (
    CapabilityConfig,
    ManifestCapabilityBuilder,
)
from general_manager.interface.backends.calculation.calculation_interface import (
    CalculationInterface,
)
from general_manager.interface.backends.database.database_interface import (
    DatabaseInterface,
)
from general_manager.interface.capabilities.orm import (
    OrmHistoryCapability,
    OrmQueryCapability,
    OrmValidationCapability,
)
from general_manager.interface.capabilities.observability import (
    LoggingObservabilityCapability,
)


def test_database_interface_default_capabilities() -> None:
    builder = ManifestCapabilityBuilder()
    selection = builder.build(DatabaseInterface)

    assert selection.required == frozenset(
        {
            "orm_support",
            "orm_mutation",
            "orm_lifecycle",
            "read",
            "history",
            "validation",
            "create",
            "update",
            "delete",
            "query",
            "observability",
        }
    )
    assert selection.activated_optional == frozenset()
    assert builder.registry.get(DatabaseInterface) == selection.all
    instances = builder.registry.instances(DatabaseInterface)
    assert len(instances) == len(selection.all)
    assert DatabaseInterface.get_capability_handler("read") is not None
    assert isinstance(
        DatabaseInterface.get_capability_handler("history"), OrmHistoryCapability
    )
    assert isinstance(
        DatabaseInterface.get_capability_handler("validation"), OrmValidationCapability
    )
    assert isinstance(
        DatabaseInterface.get_capability_handler("query"), OrmQueryCapability
    )
    assert isinstance(
        DatabaseInterface.get_capability_handler("observability"),
        LoggingObservabilityCapability,
    )


def test_optional_capabilities_enabled_via_flags() -> None:
    builder = ManifestCapabilityBuilder()
    config = CapabilityConfig(flags={"notifications": True})
    selection = builder.build(DatabaseInterface, config=config)

    assert selection.activated_optional == frozenset({"notification"})


def test_optional_capabilities_enabled_manually() -> None:
    builder = ManifestCapabilityBuilder()
    config = CapabilityConfig(enabled={"notification", "scheduling"})
    selection = builder.build(DatabaseInterface, config=config)

    assert selection.activated_optional == frozenset({"notification", "scheduling"})


def test_disabling_required_capability_raises() -> None:
    builder = ManifestCapabilityBuilder()
    config = CapabilityConfig(disabled={"read"})
    with pytest.raises(ValueError, match="Required capabilities cannot be disabled"):
        builder.build(DatabaseInterface, config=config)


def test_enable_non_optional_capability_raises() -> None:
    builder = ManifestCapabilityBuilder()
    config = CapabilityConfig(enabled={"history"})
    with pytest.raises(ValueError, match="not optional"):
        builder.build(DatabaseInterface, config=config)


def test_calculation_interface_requires_observability() -> None:
    builder = ManifestCapabilityBuilder()
    selection = builder.build(CalculationInterface)

    assert "observability" in selection.required
    assert "observability" not in selection.optional
    assert "calculation_lifecycle" in selection.required
