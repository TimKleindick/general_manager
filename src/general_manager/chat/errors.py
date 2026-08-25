"""Public chat error mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PLANNED_PUBLIC_MESSAGES = {
    "invalid_plan": "I could not prepare a safe plan for that request.",
    "manager_unresolved": "I could not resolve the required application data.",
    "dependency_blocked": "A required part of the request could not be completed.",
    "budget_exhausted": "The request reached its execution limit.",
    "deadline_exceeded": "The request reached its time limit.",
    "provider_failed": "The provider could not complete the request.",
    "synthesis_failed": "I could not produce a grounded answer from the available data.",
}


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
    planned_reason = getattr(_exc, "public_reason", None)
    if isinstance(planned_reason, str):
        return planned_public_error(planned_reason)
    return PublicChatError("chat_error", "Chat request failed.")


def planned_public_error(reason: object) -> PublicChatError:
    """Map a stable planned terminal reason without exposing internal details."""
    if isinstance(reason, str) and reason in PLANNED_PUBLIC_MESSAGES:
        return PublicChatError(reason, PLANNED_PUBLIC_MESSAGES[reason])
    return PublicChatError("chat_error", "Chat request failed.")
