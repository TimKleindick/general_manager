"""Tests for the manifest-driven capability builder."""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar, cast

import pytest

from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities import CapabilityName, CapabilityRegistry
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.manifests.capability_manifest import CapabilityManifest
from general_manager.interface.manifests.capability_models import CapabilityPlan
from general_manager.interface.manifests import (
    CapabilityConfig,
    ManifestCapabilityBuilder,
)
from general_manager.interface.infrastructure.startup_hooks import (
    clear_startup_hooks,
    registered_startup_hooks,
)
from general_manager.interface.infrastructure.system_checks import (
    clear_system_checks,
    registered_system_checks,
)
from general_manager.interface.manifests import capability_builder as builder_module
from general_manager.interface.interfaces.calculation import (
    CalculationInterface,
)
from general_manager.interface.interfaces.database import (
    DatabaseInterface,
)
from general_manager.interface.capabilities.orm import (
    OrmHistoryCapability,
    OrmQueryCapability,
    OrmValidationCapability,
)
from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)


def test_capability_builder_module_exports_builder() -> None:
    assert builder_module.__all__ == ["ManifestCapabilityBuilder"]


def test_database_interface_default_capabilities() -> None:
    builder = ManifestCapabilityBuilder()
    selection = builder.build(DatabaseInterface)

    assert selection.required == frozenset(
        {
            "orm_support",
            "orm_mutation",
            "orm_lifecycle",
            "soft_delete",
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


def test_disabled_optional_capability_wins_over_manual_enable() -> None:
    builder = ManifestCapabilityBuilder()
    config = CapabilityConfig(enabled={"notification"}, disabled={"notification"})
    selection = builder.build(DatabaseInterface, config=config)

    assert selection.activated_optional == frozenset()


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


def test_build_restores_interface_state_when_capability_instantiation_fails() -> None:
    class LocalInterface(InterfaceBase):
        pass

    unknown_capability = cast(CapabilityName, "unknown_capability_for_test")
    manifest = CapabilityManifest(
        plans={
            LocalInterface: CapabilityPlan(
                required=frozenset({unknown_capability}),
            )
        }
    )
    builder = ManifestCapabilityBuilder(manifest=manifest)

    with pytest.raises(KeyError, match="unknown_capability_for_test"):
        builder.build(LocalInterface)

    assert LocalInterface._capability_selection is None
    assert LocalInterface._capabilities == frozenset()
    assert LocalInterface._capability_handlers == {}
    assert builder.registry.get(LocalInterface) == frozenset()
    assert builder.registry.instances(LocalInterface) == ()


def test_build_restores_interface_state_when_registry_publication_fails() -> None:
    clear_startup_hooks()
    clear_system_checks()

    class LocalInterface(InterfaceBase):
        capability_overrides: ClassVar[dict[CapabilityName, object]] = {}

    class FailingRegistryError(RuntimeError):
        pass

    class FailingRegistry(CapabilityRegistry):
        def register(
            self,
            interface_cls: type[InterfaceBase],
            capabilities: Iterable[CapabilityName],
            *,
            replace: bool = False,
        ) -> None:
            raise FailingRegistryError

    def startup_hook() -> None:
        return None

    def system_check() -> list[object]:
        return []

    class RollbackCapability(BaseCapability):
        name: ClassVar[CapabilityName] = "observability"

        def get_startup_hooks(
            self, interface_cls: type[InterfaceBase]
        ) -> tuple[object, ...]:
            return (startup_hook,)

        def get_system_checks(
            self, interface_cls: type[InterfaceBase]
        ) -> tuple[object, ...]:
            return (system_check,)

    LocalInterface.capability_overrides = {"observability": RollbackCapability}
    manifest = CapabilityManifest(
        plans={LocalInterface: CapabilityPlan(required=frozenset({"observability"}))}
    )
    builder = ManifestCapabilityBuilder(
        manifest=manifest,
        registry=FailingRegistry(),
    )

    with pytest.raises(FailingRegistryError):
        builder.build(LocalInterface)

    assert LocalInterface._capability_selection is None
    assert LocalInterface._capabilities == frozenset()
    assert LocalInterface._capability_handlers == {}
    assert registered_startup_hooks().get(LocalInterface, ()) == ()
    assert registered_system_checks().get(LocalInterface, ()) == ()

    clear_startup_hooks()
    clear_system_checks()


def test_calculation_interface_requires_observability() -> None:
    builder = ManifestCapabilityBuilder()
    selection = builder.build(CalculationInterface)

    assert "observability" in selection.required
    assert "observability" not in selection.optional
    assert "calculation_lifecycle" in selection.required
