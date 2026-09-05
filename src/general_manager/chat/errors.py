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
    "rate_limited": "Chat rate limit exceeded. Try again later.",
}


@dataclass(frozen=True)
class PublicChatError:
    """Sanitized chat error safe to return to clients."""

    code: str
    message: str
    retry_after_seconds: int | None = None

    def as_event(self) -> dict[str, Any]:
        """Render the error as the public chat event payload."""
        event: dict[str, Any] = {
            "type": "error",
            "message": self.message,
            "code": self.code,
        }
        if self.retry_after_seconds is not None:
            event["retry_after_seconds"] = self.retry_after_seconds
        return event


def public_chat_error(_exc: Exception) -> PublicChatError:
    """Map an internal exception to a generic public chat error."""
    planned_reason = getattr(_exc, "public_reason", None)
    retry_after = getattr(_exc, "retry_after_seconds", None)
    if (
        planned_reason == "rate_limited"
        and type(retry_after) is int
        and retry_after >= 0
    ):
        return PublicChatError(
            "rate_limited", PLANNED_PUBLIC_MESSAGES["rate_limited"], retry_after
        )
    if isinstance(planned_reason, str):
        return planned_public_error(planned_reason)
    if isinstance(_exc, TimeoutError):
        return planned_public_error("deadline_exceeded")
    return PublicChatError("chat_error", "Chat request failed.")


def planned_public_error(reason: object) -> PublicChatError:
    """Map a stable planned terminal reason without exposing internal details."""
    if isinstance(reason, str) and reason in PLANNED_PUBLIC_MESSAGES:
        return PublicChatError(reason, PLANNED_PUBLIC_MESSAGES[reason])
    return PublicChatError("chat_error", "Chat request failed.")
