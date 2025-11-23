"""Unit tests for existing-model capabilities."""

from __future__ import annotations

from typing import Any, ClassVar, Iterator

import pytest
from django.db import models
from unittest.mock import MagicMock

from general_manager.factory.auto_factory import AutoFactory
from general_manager.interface.capabilities.existing_model import (
    ExistingModelResolutionCapability,
)
from general_manager.interface.capabilities.orm import (
    OrmPersistenceSupportCapability,
    SoftDeleteCapability,
)
from general_manager.interface.interfaces.existing_model import ExistingModelInterface
from general_manager.interface.utils.errors import (
    InvalidModelReferenceError,
    MissingModelConfigurationError,
)


def _make_model(
    name: str,
    *,
    include_is_active: bool = True,
    with_history_attr: bool = False,
) -> type[models.Model]:
    attrs: dict[str, Any] = {
        "__module__": "tests.unit.test_existing_model_capabilities",
        "value": models.CharField(max_length=32),
    }
    if include_is_active:
        attrs["is_active"] = models.BooleanField(default=True)
    if with_history_attr:
        attrs["history"] = None
    attrs["Meta"] = type("Meta", (), {"app_label": "tests"})
    return type(name, (models.Model,), attrs)


def _make_interface(
    model: type[models.Model] | str | None,
) -> tuple[type[ExistingModelInterface], SoftDeleteCapability]:
    attrs: dict[str, Any] = {
        "__module__": "tests.unit.test_existing_model_capabilities",
        "model": model,
    }
    interface_cls = type(
        f"InterfaceFor{getattr(model, '__name__', 'Unknown')}",
        (ExistingModelInterface,),
        attrs,
    )
    soft_delete = SoftDeleteCapability()
    handlers = {"soft_delete": soft_delete}

    @classmethod
    def get_capability_handler(cls, capability_name: str):
        return handlers.get(capability_name)

    interface_cls.get_capability_handler = get_capability_handler  # type: ignore[assignment]
    interface_cls._capability_handlers = handlers  # type: ignore[attr-defined]
    return interface_cls, soft_delete


@pytest.fixture(autouse=True)
def bypass_observability(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Invoke capability callbacks directly in tests."""
    monkeypatch.setattr(
        "general_manager.interface.capabilities.existing_model.resolution.call_with_observability",
        lambda *_args, **kwargs: kwargs["func"](),
    )
    yield


def test_resolve_model_from_class_sets_soft_delete_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _make_model("ResolveModel")
    interface_cls, soft_delete = _make_interface(model)
    capability = ExistingModelResolutionCapability()

    resolved = capability.resolve_model(interface_cls)

    assert resolved is model
    assert interface_cls._model is model  # type: ignore[attr-defined]
    assert interface_cls.model is model
    assert soft_delete.is_enabled() is True


def test_resolve_model_from_string_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _make_model("StringModel")
    interface_cls, _ = _make_interface("tests.StringModel")
    capability = ExistingModelResolutionCapability()

    def fake_get_model(label: str) -> type[models.Model]:
        if label == "tests.StringModel":
            return model
        raise LookupError(label)

    monkeypatch.setattr(
        "general_manager.interface.capabilities.existing_model.resolution.apps.get_model",
        fake_get_model,
    )

    resolved = capability.resolve_model(interface_cls)

    assert resolved is model
    assert interface_cls.model is model


def test_resolve_model_missing_configuration_raises() -> None:
    interface_cls, _ = _make_interface(None)
    capability = ExistingModelResolutionCapability()

    with pytest.raises(MissingModelConfigurationError):
        capability.resolve_model(interface_cls)


def test_resolve_model_invalid_reference_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interface_cls, _ = _make_interface("tests.MissingModel")
    capability = ExistingModelResolutionCapability()

    monkeypatch.setattr(
        "general_manager.interface.capabilities.existing_model.resolution.apps.get_model",
        lambda label: (_ for _ in ()).throw(LookupError(label)),
    )

    with pytest.raises(InvalidModelReferenceError):
        capability.resolve_model(interface_cls)


def test_ensure_history_registers_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _make_model("HistoryModel")
    capability = ExistingModelResolutionCapability()
    called: dict[str, Any] = {}

    def fake_register(target: type[models.Model], *, m2m_fields: list[str]) -> None:
        called["target"] = target
        called["m2m_fields"] = list(m2m_fields)

    monkeypatch.setattr(
        "general_manager.interface.capabilities.existing_model.resolution.register",
        fake_register,
    )
    if hasattr(model._meta, "simple_history_manager_attribute"):
        delattr(model._meta, "simple_history_manager_attribute")  # type: ignore[attr-defined]

    capability.ensure_history(model)

    assert called["target"] is model
    assert isinstance(called["m2m_fields"], list)


def test_ensure_history_skips_when_already_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _make_model("RegisteredModel")
    capability = ExistingModelResolutionCapability()
    model._meta.simple_history_manager_attribute = "history"  # type: ignore[attr-defined]

    def _fail_register(*_args, **_kwargs):
        msg = "register should not be called"
        raise AssertionError(msg)

    monkeypatch.setattr(
        "general_manager.interface.capabilities.existing_model.resolution.register",
        _fail_register,
    )

    capability.ensure_history(model)


def test_apply_rules_combines_interface_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _make_model("RulesModel")
    interface_cls, _ = _make_interface(model)
    existing_rule = object()
    model._meta.rules = [existing_rule]  # type: ignore[attr-defined]

    class Meta:
        rules: ClassVar[list[str]] = ["new_rule"]

    interface_cls.Meta = Meta  # type: ignore[attr-defined]
    capability = ExistingModelResolutionCapability()
    original_clean = model.full_clean

    capability.apply_rules(interface_cls, model)

    assert model._meta.rules == [existing_rule, "new_rule"]  # type: ignore[attr-defined]
    assert model.full_clean is not original_clean


def test_pre_create_returns_concrete_interface_and_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _make_model("PreCreateModel")
    capability = ExistingModelResolutionCapability()
    interface_cls, _ = _make_interface(model)
    interface_cls.Factory = type("CustomFactory", (), {"custom_attr": True})

    monkeypatch.setattr(
        ExistingModelResolutionCapability,
        "resolve_model",
        lambda *_args: model,
    )
    monkeypatch.setattr(
        ExistingModelResolutionCapability,
        "ensure_history",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        ExistingModelResolutionCapability,
        "apply_rules",
        lambda *_args, **_kwargs: None,
    )

    attrs = {"__module__": __name__}
    updated_attrs, concrete_interface, resolved_model = capability.pre_create(
        name="DemoManager",
        attrs=attrs,
        interface=interface_cls,
    )

    assert resolved_model is model
    assert updated_attrs["_interface_type"] == interface_cls._interface_type
    assert updated_attrs["Interface"] is concrete_interface
    assert issubclass(concrete_interface, interface_cls)
    factory = updated_attrs["Factory"]
    assert issubclass(factory, AutoFactory)
    assert factory.interface is concrete_interface
    assert factory._meta.model is model
    assert getattr(factory, "custom_attr", False) is True


def test_post_create_sets_managers_and_soft_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _make_model("PostModel")
    capability = ExistingModelResolutionCapability()
    interface_cls, soft_delete = _make_interface(model)
    soft_delete.set_state(enabled=True)
    support = MagicMock(spec=OrmPersistenceSupportCapability)
    support.get_manager.side_effect = ["active_manager", "inactive_manager"]

    @classmethod
    def fake_require_capability(cls, name: str, expected_type=None):
        assert name == "orm_support"
        return support

    interface_cls.require_capability = fake_require_capability  # type: ignore[assignment]
    manager_cls = type("GeneratedManager", (), {})

    capability.post_create(
        new_class=manager_cls,
        interface_class=interface_cls,
        model=model,
    )

    assert interface_cls._parent_class is manager_cls  # type: ignore[attr-defined]
    assert model._general_manager_class is manager_cls  # type: ignore[attr-defined]
    assert manager_cls.objects == "active_manager"
    assert manager_cls.all_objects == "inactive_manager"
    assert hasattr(model, "all_objects")


def test_post_create_no_model_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    capability = ExistingModelResolutionCapability()
    interface_cls, _ = _make_interface(_make_model("UnusedModel"))
    manager_cls = type("GeneratedManager", (), {})

    capability.post_create(
        new_class=manager_cls,
        interface_class=interface_cls,
        model=None,
    )

    assert not hasattr(interface_cls, "_parent_class")


def test_build_factory_inherits_auto_factory() -> None:
    model = _make_model("FactoryModel")
    interface_cls, _ = _make_interface(model)
    capability = ExistingModelResolutionCapability()

    class CustomFactory:
        custom = "flag"

    factory = capability.build_factory(
        name="Demo",
        interface_cls=interface_cls,
        model=model,
        factory_definition=CustomFactory,
    )

    assert issubclass(factory, AutoFactory)
    assert factory.interface is interface_cls
    assert factory._meta.model is model
    assert factory.custom == "flag"
