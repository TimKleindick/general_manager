"""Workflow engine backend configuration and lookup helpers."""

from __future__ import annotations

from typing import Any, Mapping

from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.workflow.backends.local import LocalWorkflowEngine
from general_manager.workflow.config import workflow_mode
from general_manager.workflow.engine import WorkflowEngine

_SETTINGS_KEY = "GENERAL_MANAGER"
_WORKFLOW_ENGINE_KEY = "WORKFLOW_ENGINE"

_engine: WorkflowEngine | None = None


def configure_workflow_engine(engine: WorkflowEngine | None) -> None:
    """Set the active workflow engine."""
    global _engine
    _engine = engine


def _resolve_engine(value: Any) -> WorkflowEngine | None:
    """Resolve workflow engine settings values into an engine instance."""
    if value is None:
        return None
    if isinstance(value, str):
        resolved = import_string(value)
    elif isinstance(value, Mapping):
        class_path = value.get("class")
        options = value.get("options", {})
        if class_path is None:
            return None
        resolved = (
            import_string(class_path) if isinstance(class_path, str) else class_path
        )
        if isinstance(resolved, type):
            return resolved(**options)
        if callable(resolved):
            return resolved(**options)
        return None
    else:
        resolved = value

    if isinstance(resolved, type):
        return resolved()
    if callable(resolved):
        return resolved()
    return resolved  # type: ignore[return-value]


def configure_workflow_engine_from_settings(django_settings: Any) -> None:
    """
    Configure the workflow engine from Django settings.

    Reads ``GENERAL_MANAGER['WORKFLOW_ENGINE']`` first, then falls back to
    top-level ``WORKFLOW_ENGINE``.
    """
    config: Mapping[str, Any] | None = getattr(django_settings, _SETTINGS_KEY, None)
    engine_setting: Any = None
    if isinstance(config, Mapping):
        engine_setting = config.get(_WORKFLOW_ENGINE_KEY)
    if engine_setting is None:
        engine_setting = getattr(django_settings, _WORKFLOW_ENGINE_KEY, None)
    configure_workflow_engine(_resolve_engine(engine_setting))


def get_workflow_engine() -> WorkflowEngine:
    """Return configured workflow engine, defaulting to local in-memory engine."""
    global _engine
    if _engine is not None:
        return _engine
    configure_workflow_engine_from_settings(settings)
    if _engine is None:
        if workflow_mode(settings) == "production":
            from general_manager.workflow.backends.celery import CeleryWorkflowEngine

            _engine = CeleryWorkflowEngine()
            return _engine
        _engine = LocalWorkflowEngine()
    return _engine
