"""Public chat error mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PublicChatError:
    code: str
    message: str

    def as_event(self) -> dict[str, Any]:
        return {"type": "error", "message": self.message, "code": self.code}


def public_chat_error(_exc: Exception) -> PublicChatError:
    return PublicChatError("chat_error", "Chat request failed.")
