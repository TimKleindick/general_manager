"""Tests for declarative search-invalidation startup checks."""

from __future__ import annotations

from importlib import import_module
from typing import ClassVar
from unittest.mock import patch

from django.core.checks import Error
from django.db import models

from general_manager.apps import GeneralmanagerConfig
from general_manager.interface.orm_interface import OrmInterfaceBase
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.search.checks import register_search_checks, run_search_checks
from general_manager.search.config import IndexConfig, SearchInvalidationRule
from tests.utils.simple_manager_interface import BaseTestInterface


class SourceModel(models.Model):
    class Meta:
        app_label = "search_check_tests"


class OtherSourceModel(models.Model):
    class Meta:
        app_label = "search_check_tests"


class OwnerModel(models.Model):
    sources = models.ManyToManyField(SourceModel)
    other_sources = models.ManyToManyField(OtherSourceModel)
    label = models.CharField(max_length=32)

    class Meta:
        app_label = "search_check_tests"


class SymmetricalModel(models.Model):
    peers = models.ManyToManyField("self")

    class Meta:
        app_label = "search_check_tests"


class OwnerToFieldModel(models.Model):
    code = models.CharField(max_length=32, unique=True)
    sources = models.ManyToManyField(
        SourceModel,
        through="OwnerToFieldThrough",
    )

    class Meta:
        app_label = "search_check_tests"


class OwnerToFieldThrough(models.Model):
    owner = models.ForeignKey(
        OwnerToFieldModel,
        to_field="code",
        on_delete=models.CASCADE,
    )
    source = models.ForeignKey(SourceModel, on_delete=models.CASCADE)

    class Meta:
        app_label = "search_check_tests"


class SourceToFieldModel(models.Model):
    code = models.CharField(max_length=32, unique=True)

    class Meta:
        app_label = "search_check_tests"


class SourceToFieldOwnerModel(models.Model):
    sources = models.ManyToManyField(
        SourceToFieldModel,
        through="SourceToFieldThrough",
    )

    class Meta:
        app_label = "search_check_tests"


class SourceToFieldThrough(models.Model):
    owner = models.ForeignKey(SourceToFieldOwnerModel, on_delete=models.CASCADE)
    source = models.ForeignKey(
        SourceToFieldModel,
        to_field="code",
        on_delete=models.CASCADE,
    )

    class Meta:
        app_label = "search_check_tests"


class SourceInterface(OrmInterfaceBase[SourceModel]):
    _model = SourceModel


class OtherSourceInterface(OrmInterfaceBase[OtherSourceModel]):
    _model = OtherSourceModel


class OwnerInterface(OrmInterfaceBase[OwnerModel]):
    _model = OwnerModel


class CompositeOwnerInterface(OrmInterfaceBase[OwnerModel]):
    _model = OwnerModel
    input_fields: ClassVar = {
        "id": Input(int),
        "tenant": Input(str),
    }


class SymmetricalInterface(OrmInterfaceBase[SymmetricalModel]):
    _model = SymmetricalModel


class OwnerToFieldInterface(OrmInterfaceBase[OwnerToFieldModel]):
    _model = OwnerToFieldModel


class SourceToFieldInterface(OrmInterfaceBase[SourceToFieldModel]):
    _model = SourceToFieldModel


class SourceToFieldOwnerInterface(OrmInterfaceBase[SourceToFieldOwnerModel]):
    _model = SourceToFieldOwnerModel


class SourceManager(GeneralManager):
    Interface = BaseTestInterface


class OtherSourceManager(GeneralManager):
    Interface = BaseTestInterface


class OwnerManager(GeneralManager):
    Interface = BaseTestInterface

    class SearchConfig:
        indexes: ClassVar = (
            IndexConfig(name="global", fields=("label",)),
            IndexConfig(name="secondary", fields=("label",)),
        )
        invalidation_rules: ClassVar = ()


class NonOrmManager(GeneralManager):
    Interface = BaseTestInterface

    class SearchConfig:
        indexes: ClassVar = (IndexConfig(name="global", fields=("label",)),)
        invalidation_rules: ClassVar = ()


class CompositeOwnerManager(GeneralManager):
    Interface = BaseTestInterface

    class SearchConfig:
        indexes: ClassVar = (IndexConfig(name="global", fields=("label",)),)
        invalidation_rules: ClassVar = ()


class SymmetricalManager(GeneralManager):
    Interface = BaseTestInterface

    class SearchConfig:
        indexes: ClassVar = (IndexConfig(name="global", fields=()),)
        invalidation_rules: ClassVar = ()


class OwnerToFieldManager(GeneralManager):
    Interface = BaseTestInterface

    class SearchConfig:
        indexes: ClassVar = (IndexConfig(name="global", fields=()),)
        invalidation_rules: ClassVar = ()


class SourceToFieldManager(GeneralManager):
    Interface = BaseTestInterface


class SourceToFieldOwnerManager(GeneralManager):
    Interface = BaseTestInterface

    class SearchConfig:
        indexes: ClassVar = (IndexConfig(name="global", fields=()),)
        invalidation_rules: ClassVar = ()


SourceManager.Interface = SourceInterface
OtherSourceManager.Interface = OtherSourceInterface
OwnerManager.Interface = OwnerInterface
CompositeOwnerManager.Interface = CompositeOwnerInterface
SymmetricalManager.Interface = SymmetricalInterface
OwnerToFieldManager.Interface = OwnerToFieldInterface
SourceToFieldManager.Interface = SourceToFieldInterface
SourceToFieldOwnerManager.Interface = SourceToFieldOwnerInterface

for _manager in (
    SourceManager,
    OtherSourceManager,
    OwnerManager,
    NonOrmManager,
    CompositeOwnerManager,
    SymmetricalManager,
    OwnerToFieldManager,
    SourceToFieldManager,
    SourceToFieldOwnerManager,
):
    for _registry in (
        GeneralManagerMeta.all_classes,
        GeneralManagerMeta.pending_attribute_initialization,
        GeneralManagerMeta.pending_graphql_interfaces,
    ):
        if _manager in _registry:
            _registry.remove(_manager)


def _run_rule(
    owner: type[GeneralManager],
    rule: SearchInvalidationRule,
) -> list:
    config = type.__getattribute__(owner, "SearchConfig")
    with patch.object(config, "invalidation_rules", (rule,)):
        return run_search_checks(managers=(owner,))


def test_search_check_accepts_valid_class_source_and_selected_indexes() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(
            source=SourceManager,
            resolve=lambda _change, _owner: (),
            indexes=("global",),
        ),
    )

    assert errors == []


def test_search_check_accepts_valid_dotted_source_and_m2m_relation() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(
            source=f"{SourceManager.__module__}.SourceManager",
            relation="sources",
        ),
    )

    assert errors == []


def test_search_check_rejects_invalid_source_without_leaking_import_details() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(source="private.missing.SourceManager"),
    )

    assert errors == [
        Error(
            "Search invalidation rule source must resolve to a GeneralManager subclass.",
            id="general_manager.search.E001",
        )
    ]


def test_search_check_rejects_non_manager_source_class() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(source=SourceModel),  # type: ignore[arg-type]
    )

    assert [error.id for error in errors] == ["general_manager.search.E001"]


def test_search_check_rejects_non_callable_resolver() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(source=SourceManager, resolve=42),  # type: ignore[arg-type]
    )

    assert errors == [
        Error(
            "Search invalidation rule resolver must be callable.",
            id="general_manager.search.E002",
        )
    ]


def test_search_check_rejects_empty_explicit_indexes() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(source=SourceManager, indexes=()),
    )

    assert [error.id for error in errors] == ["general_manager.search.E003"]


def test_search_check_rejects_unknown_explicit_indexes() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(source=SourceManager, indexes=("missing",)),
    )

    assert errors == [
        Error(
            "Search invalidation rule indexes must be a non-empty subset of owner indexes.",
            id="general_manager.search.E003",
        )
    ]


def test_search_check_rejects_relation_on_non_orm_owner() -> None:
    errors = _run_rule(
        NonOrmManager,
        SearchInvalidationRule(source=SourceManager, relation="sources"),
    )

    assert [error.id for error in errors] == ["general_manager.search.E004"]


def test_search_check_rejects_relation_with_non_orm_source() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(source=NonOrmManager, relation="sources"),
    )

    assert [error.id for error in errors] == ["general_manager.search.E005"]


def test_search_check_rejects_missing_relation() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(source=SourceManager, relation="missing"),
    )

    assert [error.id for error in errors] == ["general_manager.search.E006"]


def test_search_check_rejects_non_many_to_many_relation() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(source=SourceManager, relation="label"),
    )

    assert [error.id for error in errors] == ["general_manager.search.E006"]


def test_search_check_rejects_relation_to_wrong_source_model() -> None:
    errors = _run_rule(
        OwnerManager,
        SearchInvalidationRule(source=SourceManager, relation="other_sources"),
    )

    assert [error.id for error in errors] == ["general_manager.search.E006"]


def test_search_check_rejects_nonstandard_owner_identification() -> None:
    errors = _run_rule(
        CompositeOwnerManager,
        SearchInvalidationRule(source=SourceManager, relation="sources"),
    )

    assert errors == [
        Error(
            "M2M search invalidation owners must use exactly the 'id' input.",
            id="general_manager.search.E007",
        )
    ]


def test_search_check_rejects_self_symmetrical_many_to_many() -> None:
    errors = _run_rule(
        SymmetricalManager,
        SearchInvalidationRule(source=SymmetricalManager, relation="peers"),
    )

    assert errors == [
        Error(
            "Self-symmetrical M2M search invalidation is not supported.",
            id="general_manager.search.E008",
        )
    ]


def test_search_check_rejects_owner_through_fk_targeting_non_primary_key() -> None:
    errors = _run_rule(
        OwnerToFieldManager,
        SearchInvalidationRule(source=SourceManager, relation="sources"),
    )

    assert errors == [
        Error(
            "M2M search invalidation through fields must target endpoint primary keys.",
            id="general_manager.search.E009",
        )
    ]


def test_search_check_rejects_source_through_fk_targeting_non_primary_key() -> None:
    errors = _run_rule(
        SourceToFieldOwnerManager,
        SearchInvalidationRule(source=SourceToFieldManager, relation="sources"),
    )

    assert errors == [
        Error(
            "M2M search invalidation through fields must target endpoint primary keys.",
            id="general_manager.search.E009",
        )
    ]


def test_search_check_converts_malformed_rule_to_error() -> None:
    config = type.__getattribute__(OwnerManager, "SearchConfig")
    with patch.object(config, "invalidation_rules", (object(),)):
        errors = run_search_checks(managers=(OwnerManager,))

    assert [error.id for error in errors] == ["general_manager.search.E000"]


def test_search_check_registration_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr("general_manager.search.checks._registered", False)
    with patch("general_manager.search.checks.register") as register:
        register_search_checks()
        register_search_checks()

    register.assert_called_once_with("general_manager")


def test_app_ready_registers_search_checks() -> None:
    config = GeneralmanagerConfig("general_manager", import_module("general_manager"))

    with (
        patch.object(config, "install_startup_hook_runner"),
        patch.object(config, "register_system_checks"),
        patch.object(config, "initialize_general_manager_classes"),
        patch("general_manager.uploads.checks.register_upload_checks"),
        patch(
            "general_manager.search.checks.register_search_checks"
        ) as register_search_checks,
        patch("general_manager.apps._autoload_app_managers_modules"),
        patch("general_manager.apps.handle_remote_api"),
        patch("general_manager.apps.configure_audit_logger_from_settings"),
        patch("general_manager.apps.configure_search_backend_from_settings"),
        patch("general_manager.search.invalidation.configure_search_invalidation"),
        patch("general_manager.apps.configure_workflow_engine_from_settings"),
        patch("general_manager.apps.configure_event_registry_from_settings"),
        patch("general_manager.apps.configure_workflow_signal_bridge_from_settings"),
        patch("general_manager.apps.configure_workflow_beat_schedule_from_settings"),
        patch(
            "general_manager.apps.configure_search_reconcile_beat_schedule_from_settings"
        ),
        patch(
            "general_manager.apps.configure_graphql_warmup_beat_schedule_from_settings"
        ),
        patch("general_manager.conf.get_setting", return_value=False),
        patch("general_manager.apps.initialize_chat"),
    ):
        config.ready()

    register_search_checks.assert_called_once_with()
