"""Workflow engine backend configuration and lookup helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.workflow.backends.local import LocalWorkflowEngine
from general_manager.workflow.config import workflow_mode
from general_manager.workflow.engine import WorkflowEngine

_SETTINGS_KEY = "GENERAL_MANAGER"
_WORKFLOW_ENGINE_KEY = "WORKFLOW_ENGINE"

_engine: WorkflowEngine | None = None


class InvalidWorkflowEngineOptionsError(TypeError):
    """Raised when a WORKFLOW_ENGINE mapping uses non-mapping options."""

    def __init__(self) -> None:
        super().__init__("WORKFLOW_ENGINE options must be a mapping.")


class InvalidWorkflowEngineError(TypeError):
    """Raised when a workflow engine setting does not resolve to an engine."""

    def __init__(self, engine_setting: object) -> None:
        super().__init__(
            "Workflow engine setting did not resolve to a WorkflowEngine: "
            f"{engine_setting!r}"
        )


def configure_workflow_engine(engine: WorkflowEngine | None) -> None:
    """
    Set the process-local active workflow engine.

    Pass `None` to clear the configured engine so the next
    `get_workflow_engine()` call reads settings and may install the
    `WORKFLOW_MODE` default.
    """
    global _engine
    _engine = engine


def _instantiate_engine_reference(
    value: object,
    options: Mapping[str, object] | None = None,
) -> object:
    """Instantiate an engine class or factory while preserving engine instances."""
    if isinstance(value, type):
        factory = cast(Callable[..., object], value)
        return factory(**dict(options or {}))
    if callable(value) and not isinstance(value, WorkflowEngine):
        factory = cast(Callable[..., object], value)
        return factory(**dict(options or {}))
    return value


def _resolve_engine(value: object) -> WorkflowEngine | None:
    """Resolve workflow engine settings values into an engine instance."""
    if value is None:
        return None
    if isinstance(value, str):
        resolved: object = import_string(value)
    elif isinstance(value, Mapping):
        config = cast(Mapping[str, object], value)
        engine_reference = config.get("class")
        options_value = config.get("options", {})
        if engine_reference is None:
            return None
        if options_value is None:
            options: Mapping[str, object] = {}
        elif isinstance(options_value, Mapping):
            options = cast(Mapping[str, object], options_value)
        else:
            raise InvalidWorkflowEngineOptionsError
        resolved_reference = (
            import_string(engine_reference)
            if isinstance(engine_reference, str)
            else engine_reference
        )
        resolved = _instantiate_engine_reference(resolved_reference, options)
    else:
        resolved = value

    resolved = _instantiate_engine_reference(resolved)
    return resolved if isinstance(resolved, WorkflowEngine) else None


def configure_workflow_engine_from_settings(django_settings: object) -> None:
    """
    Configure the workflow engine from Django settings.

    `GENERAL_MANAGER["WORKFLOW_ENGINE"]` takes precedence over a top-level
    `WORKFLOW_ENGINE` setting, including explicit `None` to clear the configured
    engine and allow `get_workflow_engine()` to use the `WORKFLOW_MODE` default.
    Values may be:

    - `None` or missing to clear the active engine.
    - A `WorkflowEngine` instance.
    - A dotted import path to a `WorkflowEngine` instance, class, or factory.
    - A zero-argument callable returning a `WorkflowEngine`.
    - A mapping with `{"class": <path-or-callable>, "options": {...}}`; options
      are passed as keyword arguments when constructing/calling the reference.

    Import, factory, and constructor exceptions propagate.

    Raises:
        TypeError: If mapping `options` is not a mapping, or if a non-`None`
            setting cannot be resolved to a `WorkflowEngine`.
    """
    config_candidate: object = getattr(django_settings, _SETTINGS_KEY, None)
    engine_setting: object = None
    if isinstance(config_candidate, Mapping):
        config = cast(Mapping[str, object], config_candidate)
        if _WORKFLOW_ENGINE_KEY in config:
            engine_setting = config[_WORKFLOW_ENGINE_KEY]
        else:
            engine_setting = getattr(django_settings, _WORKFLOW_ENGINE_KEY, None)
    else:
        engine_setting = getattr(django_settings, _WORKFLOW_ENGINE_KEY, None)
    engine_instance = _resolve_engine(engine_setting)
    if engine_setting is not None and engine_instance is None:
        raise InvalidWorkflowEngineError(engine_setting)
    configure_workflow_engine(engine_instance)


def get_workflow_engine() -> WorkflowEngine:
    """
    Return the configured workflow engine, installing settings/defaults first.

    If no process-local engine is active, this function reads Django settings via
    `configure_workflow_engine_from_settings(django.conf.settings)`. If settings
    leave the engine unset, production `WORKFLOW_MODE` installs one
    `CeleryWorkflowEngine`; all other modes install one `LocalWorkflowEngine`.
    The installed default is cached for later calls.
    """
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
