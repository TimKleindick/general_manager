"""
Shared logging utilities for the GeneralManager package.

The helpers defined here keep logger names consistent (``general_manager.*``),
expose lightweight context support, and stay fully compatible with Django's
``LOGGING`` settings.

``COMPONENT_EXTRA_FIELD`` and ``CONTEXT_EXTRA_FIELD`` are stable ``LogRecord``
extra keys for formatters and filters.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from types import TracebackType
from typing import TypedDict, cast

BASE_LOGGER_NAME = "general_manager"
COMPONENT_EXTRA_FIELD = "component"
CONTEXT_EXTRA_FIELD = "context"
_COMPONENT_TYPE_ERROR = "component must be a string or None."

type ExcInfo = (
    bool
    | BaseException
    | tuple[type[BaseException], BaseException, TracebackType | None]
    | tuple[None, None, None]
    | None
)


class LoggingKwargs(TypedDict, total=False):
    """Keyword arguments accepted by stdlib logging calls after processing."""

    exc_info: ExcInfo
    stack_info: bool
    stacklevel: int
    extra: Mapping[str, object] | None


class InvalidContextError(TypeError):
    """Raised when log context metadata is not a mapping."""

    def __init__(self) -> None:
        """Initialize the error with the fixed context validation message."""
        super().__init__("context must be a mapping when provided.")


class InvalidExtraError(TypeError):
    """Raised when logging `extra` metadata is not mutable."""

    def __init__(self) -> None:
        """Initialize the error with the fixed extra validation message."""
        super().__init__("extra must be a mutable mapping.")


class BlankComponentError(ValueError):
    """Raised when a component name normalizes to an empty logger suffix."""

    def __init__(self) -> None:
        """Initialize the error with the fixed blank-component message."""
        super().__init__("component cannot be blank or only dots.")


__all__ = [
    "BASE_LOGGER_NAME",
    "COMPONENT_EXTRA_FIELD",
    "CONTEXT_EXTRA_FIELD",
    "GeneralManagerLoggerAdapter",
    "build_logger_name",
    "get_logger",
]


class GeneralManagerLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    """
    Attach structured metadata (component + context) to log records.

    The adapter supports standard ``LoggerAdapter`` logging calls and accepts a
    top-level ``context={...}`` keyword. Context merging is shallow: existing
    ``extra["context"]`` values are copied first, then per-call ``context``
    wins on key conflicts. The caller-provided ``extra`` mapping is updated in
    place so Python's logging machinery receives the merged metadata.

    Prefer constructing adapters through ``get_logger()``. Direct construction
    follows ``logging.LoggerAdapter(logger, extra)``; provide a mutable
    ``extra`` mapping with optional ``component`` and mapping-valued ``context``
    keys. Shape validation happens when a log call is processed.
    """

    def log(
        self,
        level: int,
        msg: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        """
        Log a message after validating and forwarding optional context.

        Use ``context={...}`` for structured per-call metadata and standard
        logging keyword arguments such as ``exc_info`` or ``stack_info`` as
        usual. ``extra`` must be a mutable mapping when provided.

        Raises:
            InvalidContextError: If ``context`` is not a mapping.
            InvalidExtraError: If ``extra`` is present but not mutable.
        """
        context_mapping = self._pop_context(kwargs)
        if context_mapping is not None:
            kwargs["context"] = context_mapping
        if self.isEnabledFor(level):
            processed_msg, processed_kwargs = self.process(msg, kwargs)
            self.logger.log(
                level,
                processed_msg,
                *args,
                **cast(LoggingKwargs, processed_kwargs),
            )

    @staticmethod
    def _pop_context(
        kwargs: MutableMapping[str, object],
    ) -> Mapping[str, object] | None:
        context = kwargs.pop("context", None)
        if context is None:
            return None
        if not isinstance(context, Mapping):
            raise InvalidContextError()
        return context

    def process(
        self, msg: object, kwargs: MutableMapping[str, object]
    ) -> tuple[object, MutableMapping[str, object]]:
        """
        Merge adapter metadata and per-call context into logging kwargs.

        The ``kwargs`` mapping and caller-provided ``extra`` mapping are
        mutated in place. Nested context mappings are shallow-copied into a new
        merged dict; ``self.extra`` is read but not mutated.

        Raises:
            InvalidExtraError: If ``extra`` is present but not mutable.
            InvalidContextError: If ``context`` or ``extra["context"]`` is not
                a mapping.
        """
        context = self._pop_context(kwargs)

        extra_obj = kwargs.setdefault("extra", {})
        if not isinstance(extra_obj, MutableMapping):
            raise InvalidExtraError()
        extra = cast(MutableMapping[str, object], extra_obj)

        extra_metadata = self.extra or {}
        component = extra_metadata.get(COMPONENT_EXTRA_FIELD)
        if component is not None:
            extra.setdefault(COMPONENT_EXTRA_FIELD, component)

        if context is not None:
            current_context = context
            existing_context = extra.get(CONTEXT_EXTRA_FIELD)
            if existing_context is None:
                merged_context: dict[str, object] = dict(current_context)
            elif isinstance(existing_context, Mapping):
                merged_context = {**dict(existing_context), **current_context}
            else:
                raise InvalidContextError()

            extra[CONTEXT_EXTRA_FIELD] = merged_context

        return msg, kwargs


def _normalize_component_name(component: str | None) -> str | None:
    if component is None:
        return None
    if not isinstance(component, str):
        raise TypeError(_COMPONENT_TYPE_ERROR)

    normalized = component.strip().strip(".")
    if not normalized:
        raise BlankComponentError()

    return normalized.replace(" ", "_")


def build_logger_name(component: str | None = None) -> str:
    """
    Build a fully-qualified logger name within the ``general_manager`` namespace.

    ``None`` returns ``"general_manager"``. A component such as
    ``"cache.dependency_index"`` returns
    ``"general_manager.cache.dependency_index"``. Components are stripped of
    surrounding whitespace/dots and spaces are replaced with underscores; an
    already-prefixed component is treated as a literal suffix.

    Raises:
        BlankComponentError: If ``component`` is blank or only dots.
        TypeError: If ``component`` is not ``None`` or ``str``.
    """

    normalized_component = _normalize_component_name(component)
    if not normalized_component:
        return BASE_LOGGER_NAME

    return ".".join([BASE_LOGGER_NAME, normalized_component])


def get_logger(component: str | None = None) -> GeneralManagerLoggerAdapter:
    """
    Return a ``GeneralManagerLoggerAdapter`` scoped to the requested component.

    ``get_logger(None)`` uses logger name ``"general_manager"`` and no initial
    component extra. ``get_logger("cache.dependency_index")`` uses logger name
    ``"general_manager.cache.dependency_index"`` and sets the adapter's
    ``component`` extra to ``"cache.dependency_index"``.

    Raises:
        BlankComponentError: If ``component`` is blank or only dots.
        TypeError: If ``component`` is not ``None`` or ``str``.
    """

    normalized_component = _normalize_component_name(component)
    logger_name = build_logger_name(normalized_component)
    adapter_extra: dict[str, object] = {}
    if normalized_component:
        adapter_extra[COMPONENT_EXTRA_FIELD] = normalized_component
    return GeneralManagerLoggerAdapter(logging.getLogger(logger_name), adapter_extra)
