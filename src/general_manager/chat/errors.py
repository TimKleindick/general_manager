"""Public chat error mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from general_manager.chat.tools import (
    ChatSchemaNotInitializedError,
    ManagerNotChatExposedError,
    MutationAuthenticationRequiredError,
    MutationNotAllowedError,
    UnknownChatToolError,
)


@dataclass(frozen=True)
class PublicChatError:
    code: str
    message: str

    def as_event(self) -> dict[str, Any]:
        return {"type": "error", "message": self.message, "code": self.code}


def public_chat_error(exc: Exception) -> PublicChatError:
    if isinstance(exc, ChatSchemaNotInitializedError):
        return PublicChatError("chat_schema_unavailable", "Chat schema is unavailable.")
    if isinstance(exc, ManagerNotChatExposedError):
        return PublicChatError(
            "manager_unavailable", "That manager is not available in chat."
        )
    if isinstance(exc, MutationAuthenticationRequiredError):
        return PublicChatError(
            "authentication_required", "Chat mutations require authentication."
        )
    if isinstance(exc, MutationNotAllowedError):
        return PublicChatError(
            "mutation_not_allowed", "That mutation is not available in chat."
        )
    if isinstance(exc, UnknownChatToolError):
        return PublicChatError(
            "unknown_tool", "The model requested an unavailable tool."
        )
    if isinstance(exc, TimeoutError):
        return PublicChatError("provider_timeout", "Chat provider timed out.")
    return PublicChatError("chat_error", "Chat request failed.")
