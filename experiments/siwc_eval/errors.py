"""Diagnostics without importing Django or any project settings."""

from typing import Any, NoReturn
import re

KNOWN_CODES = frozenset(
    {
        "subscription_sharing_user_not_eligible",
        "subscription_sharing_usage_limit_exceeded",
        "subscription_sharing_usage_unavailable",
        "subscription_sharing_unsupported_capability",
        "subscription_sharing_route_not_supported",
        "subscription_sharing_invalid_user",
        "subscription_sharing_user_unavailable",
        "chatpass_v2_scope_not_authorized",
        "chatpass_v2_invalid_authorization_context",
        "invalid_grant",
        "invalid_client",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)


class EvalError(RuntimeError):
    """Only locally controlled strings and allowlisted fields enter reports."""

    def __init__(self, message: str, diagnostic: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic or {}


def diagnostic(
    body: Any, status: int | None = None, request_id: str = ""
) -> dict[str, Any]:
    shape = "other"
    code = "unrecognized"
    if isinstance(body, dict):
        if isinstance(body.get("error"), dict):
            shape = "error"
            candidate = body["error"].get("code")
            if isinstance(candidate, str) and candidate in KNOWN_CODES:
                code = candidate
        elif "detail" in body:
            shape = "detail"
    return {
        "status": status,
        "shape": shape,
        "code": code,
        "request_id": request_id
        if re.fullmatch(r"req_[A-Za-z0-9]{16,128}", request_id)
        else None,
    }


def fail(message: str) -> NoReturn:
    """Raise only sanitized diagnostics at trust boundaries."""
    raise EvalError(message)
