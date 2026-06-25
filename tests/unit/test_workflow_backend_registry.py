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
from general_manager.workflow.backends.n8n import (
    N8nOperationNotImplementedError,
    N8nWorkflowEngine,
)
from general_manager.workflow.engine import WorkflowDefinition
from general_manager.workflow import backend_registry


class _CallableEngine:
    def __call__(self) -> LocalWorkflowEngine:
        return LocalWorkflowEngine()


class _ConfigurableEngine(LocalWorkflowEngine):
    def __init__(self, *, label: str) -> None:
        super().__init__()
        self.label = label


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

    def test_resolve_engine_mapping_with_options(self) -> None:
        resolved = _resolve_engine(
            {"class": _ConfigurableEngine, "options": {"label": "demo"}}
        )
        assert isinstance(resolved, _ConfigurableEngine)
        assert resolved.label == "demo"

    def test_configure_workflow_engine_from_settings(self) -> None:
        dummy_settings = SimpleNamespace(WORKFLOW_ENGINE=LocalWorkflowEngine)
        configure_workflow_engine_from_settings(dummy_settings)
        assert isinstance(get_workflow_engine(), LocalWorkflowEngine)

    def test_configure_workflow_engine_from_settings_nested_none_disables(self) -> None:
        dummy_settings = SimpleNamespace(
            GENERAL_MANAGER={"WORKFLOW_ENGINE": None},
            WORKFLOW_ENGINE=_ConfigurableEngine,
        )

        configure_workflow_engine_from_settings(dummy_settings)

        assert backend_registry._engine is None

    def test_configure_workflow_engine_from_settings_rejects_invalid_options(
        self,
    ) -> None:
        dummy_settings = SimpleNamespace(
            GENERAL_MANAGER={
                "WORKFLOW_ENGINE": {
                    "class": _ConfigurableEngine,
                    "options": ["not", "a", "mapping"],
                }
            }
        )

        with self.assertRaisesRegex(TypeError, "WORKFLOW_ENGINE options"):
            configure_workflow_engine_from_settings(dummy_settings)

    def test_configure_workflow_engine_from_settings_rejects_non_engine(self) -> None:
        dummy_settings = SimpleNamespace(WORKFLOW_ENGINE=object())

        with self.assertRaisesRegex(TypeError, "Workflow engine"):
            configure_workflow_engine_from_settings(dummy_settings)

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

    def test_n8n_engine_stores_configuration_and_raises_for_operations(self) -> None:
        engine = N8nWorkflowEngine(base_url="https://n8n.example.test", api_key="key")

        assert engine.base_url == "https://n8n.example.test"
        assert engine.api_key == "key"

        workflow = WorkflowDefinition(workflow_id="wf-n8n")
        operations = (
            lambda: engine.start(workflow, {"value": 1}, correlation_id="corr"),
            lambda: engine.resume("exec-1", {"approved": True}),
            lambda: engine.cancel("exec-1", reason="stop"),
            lambda: engine.status("exec-1"),
        )

        for operation in operations:
            with self.assertRaises(N8nOperationNotImplementedError):
                operation()
