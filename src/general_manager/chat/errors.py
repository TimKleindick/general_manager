"""Public chat error mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PublicChatError:
    """Sanitized chat error safe to return to clients."""

    code: str
    message: str

    def as_event(self) -> dict[str, Any]:
        """Render the error as the public chat event payload."""
        return {"type": "error", "message": self.message, "code": self.code}


def public_chat_error(_exc: Exception) -> PublicChatError:
    """Map an internal exception to a generic public chat error."""
    return PublicChatError("chat_error", "Chat request failed.")
