from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from general_manager.workflow.backend_registry import (
    _resolve_engine,
    configure_workflow_engine,
    configure_workflow_engine_from_settings,
    get_workflow_engine,
)
from general_manager.workflow.backends.celery import CeleryWorkflowEngine
from general_manager.workflow.backends.local import LocalWorkflowEngine


class _CallableEngine:
    def __call__(self) -> LocalWorkflowEngine:
        return LocalWorkflowEngine()


class BackendRegistryTests(SimpleTestCase):
    def tearDown(self) -> None:
        configure_workflow_engine(None)

    def test_resolve_engine_none(self) -> None:
        assert _resolve_engine(None) is None

    def test_resolve_engine_type(self) -> None:
        resolved = _resolve_engine(LocalWorkflowEngine)
        assert isinstance(resolved, LocalWorkflowEngine)

    def test_resolve_engine_callable(self) -> None:
        resolved = _resolve_engine(_CallableEngine())
        assert isinstance(resolved, LocalWorkflowEngine)

    def test_resolve_engine_mapping_with_class_path(self) -> None:
        resolved = _resolve_engine(
            {"class": "general_manager.workflow.backends.local.LocalWorkflowEngine"}
        )
        assert isinstance(resolved, LocalWorkflowEngine)

    def test_configure_workflow_engine_from_settings(self) -> None:
        dummy_settings = SimpleNamespace(WORKFLOW_ENGINE=LocalWorkflowEngine)
        configure_workflow_engine_from_settings(dummy_settings)
        assert isinstance(get_workflow_engine(), LocalWorkflowEngine)

    @override_settings(GENERAL_MANAGER={"WORKFLOW_ENGINE": LocalWorkflowEngine})
    def test_get_workflow_engine_uses_settings(self) -> None:
        configure_workflow_engine(None)
        assert isinstance(get_workflow_engine(), LocalWorkflowEngine)

    def test_get_workflow_engine_defaults(self) -> None:
        configure_workflow_engine(None)
        assert isinstance(get_workflow_engine(), LocalWorkflowEngine)

    @override_settings(GENERAL_MANAGER={"WORKFLOW_MODE": "production"})
    def test_get_workflow_engine_defaults_to_celery_in_production_mode(self) -> None:
        configure_workflow_engine(None)
        assert isinstance(get_workflow_engine(), CeleryWorkflowEngine)
