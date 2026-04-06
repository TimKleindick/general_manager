"""Django signals used by chat."""

from __future__ import annotations

from typing import Any

from django.dispatch import Signal


chat_message_received = Signal()
chat_mutation_executed = Signal()
chat_tool_called = Signal()
chat_error = Signal()


def emit_chat_message_received(**kwargs: Any) -> None:
    """Emit the chat_message_received signal."""
    chat_message_received.send(sender="general_manager.chat", **kwargs)


def emit_chat_mutation_executed(**kwargs: Any) -> None:
    """Emit the chat_mutation_executed signal."""
    chat_mutation_executed.send(sender="general_manager.chat", **kwargs)


def emit_chat_tool_called(**kwargs: Any) -> None:
    """Emit the chat_tool_called signal."""
    chat_tool_called.send(sender="general_manager.chat", **kwargs)


def emit_chat_error(**kwargs: Any) -> None:
    """Emit the chat_error signal."""
    chat_error.send(sender="general_manager.chat", **kwargs)


__all__ = [
    "chat_error",
    "chat_message_received",
    "chat_mutation_executed",
    "chat_tool_called",
    "emit_chat_error",
    "emit_chat_message_received",
    "emit_chat_mutation_executed",
    "emit_chat_tool_called",
]
