"""
Shared logging utilities for the GeneralManager package.

The helpers defined here keep logger names consistent (``general_manager.*``),
expose lightweight context support, and stay fully compatible with Django's
``LOGGING`` settings.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import Any, cast

BASE_LOGGER_NAME = "general_manager"
COMPONENT_EXTRA_FIELD = "component"
CONTEXT_EXTRA_FIELD = "context"


class InvalidContextError(TypeError):
    def __init__(self) -> None:
        super().__init__("context must be a mapping when provided.")


class InvalidExtraError(TypeError):
    def __init__(self) -> None:
        super().__init__("extra must be a mutable mapping.")


class BlankComponentError(ValueError):
    def __init__(self) -> None:
        super().__init__("component cannot be blank or only dots.")


__all__ = [
    "BASE_LOGGER_NAME",
    "COMPONENT_EXTRA_FIELD",
    "CONTEXT_EXTRA_FIELD",
    "GeneralManagerLoggerAdapter",
    "build_logger_name",
    "get_logger",
]


class GeneralManagerLoggerAdapter(logging.LoggerAdapter[Any]):
    """
    Attach structured metadata (component + context) to log records.

    The adapter keeps ``extra`` mutable, merges ``context`` mappings, and can be
    used anywhere ``logging.Logger`` is expected.
    """

    def log(self, level: int, msg: Any, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        context_mapping = self._pop_context(kwargs)
        if context_mapping is not None:
            kwargs["context"] = context_mapping
        super().log(level, msg, *args, **kwargs)

    @staticmethod
    def _pop_context(
        kwargs: MutableMapping[str, Any],
    ) -> Mapping[str, Any] | None:
        context = kwargs.pop("context", None)
        if context is None:
            return None
        if not isinstance(context, Mapping):
            raise InvalidContextError()
        return context

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        context = self._pop_context(kwargs)

        extra_obj = kwargs.setdefault("extra", {})
        if not isinstance(extra_obj, MutableMapping):
            raise InvalidExtraError()
        extra = cast(MutableMapping[str, Any], extra_obj)

        extra_metadata = cast(Mapping[str, Any], self.extra or {})
        component = extra_metadata.get(COMPONENT_EXTRA_FIELD)
        if component is not None:
            extra.setdefault(COMPONENT_EXTRA_FIELD, component)

        if context is not None:
            current_context = cast(Mapping[str, Any], context)
            existing_context = extra.get(CONTEXT_EXTRA_FIELD)
            if existing_context is None:
                merged_context: dict[str, Any] = dict(current_context)
            elif isinstance(existing_context, Mapping):
                merged_context = {**dict(existing_context), **current_context}
            else:
                raise InvalidContextError()

            extra[CONTEXT_EXTRA_FIELD] = merged_context

        return msg, kwargs


def _normalize_component_name(component: str | None) -> str | None:
    if component is None:
        return None

    normalized = component.strip().strip(".")
    if not normalized:
        raise BlankComponentError()

    return normalized.replace(" ", "_")


def build_logger_name(component: str | None = None) -> str:
    """
    Build a fully-qualified logger name within the ``general_manager`` namespace.
    """

    normalized_component = _normalize_component_name(component)
    if not normalized_component:
        return BASE_LOGGER_NAME

    return ".".join([BASE_LOGGER_NAME, normalized_component])


def get_logger(component: str | None = None) -> GeneralManagerLoggerAdapter:
    """
    Return a ``GeneralManagerLoggerAdapter`` scoped to the requested component.
    """

    normalized_component = _normalize_component_name(component)
    logger_name = build_logger_name(normalized_component)
    adapter_extra: dict[str, Any] = {}
    if normalized_component:
        adapter_extra[COMPONENT_EXTRA_FIELD] = normalized_component
    return GeneralManagerLoggerAdapter(logging.getLogger(logger_name), adapter_extra)
