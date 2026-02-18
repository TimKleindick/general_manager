"""Authentication and principal resolution helpers for MCP gateway adapters."""

from __future__ import annotations

from typing import Any, Callable, Mapping
import uuid

from django.contrib.auth import get_user_model
from django.utils.module_loading import import_string

from general_manager.logging import get_logger
from general_manager.mcp.contract import QueryContext


logger = get_logger("mcp.auth")


class MCPAuthError(PermissionError):
    """Raised when gateway authentication fails."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _is_authenticated(user: Any) -> bool:
    return bool(getattr(user, "is_authenticated", False))


def _resolve_callable(config: Mapping[str, Any], key: str) -> Callable[..., Any] | None:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        resolved = import_string(value)
        return resolved if callable(resolved) else None
    if callable(value):
        return value
    return None


def build_context_from_http_request(
    request: Any,
    gateway_config: Mapping[str, Any] | None = None,
) -> QueryContext:
    """Build query context for HTTP adapter calls."""
    config = gateway_config or {}
    request_id = getattr(request, "headers", {}).get("X-Request-ID") or str(
        uuid.uuid4()
    )

    user = getattr(request, "user", None)
    auth_resolver = _resolve_callable(config, "AUTH_RESOLVER")
    if auth_resolver is not None:
        user = auth_resolver(request)

    if not _is_authenticated(user):
        raise MCPAuthError("UNAUTHENTICATED", "Authentication required.")

    tenant = None
    tenant_resolver = _resolve_callable(config, "TENANT_RESOLVER")
    if tenant_resolver is not None:
        try:
            tenant_value = tenant_resolver(request=request, user=user)
            if tenant_value is not None:
                tenant = str(tenant_value)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "tenant resolver failed",
                context={"error": type(exc).__name__, "message": str(exc)},
            )

    return QueryContext(user=user, request_id=str(request_id), tenant=tenant)


def build_context_from_auth_payload(
    auth_payload: Any,
    gateway_config: Mapping[str, Any] | None = None,
) -> QueryContext:
    """Build query context for stdio MCP calls.

    Expected payload shape:
        {"user_id": <int|str>, "request_id": "...", "tenant": "..."}
    """

    config = gateway_config or {}
    if not isinstance(auth_payload, Mapping):
        raise MCPAuthError("UNAUTHENTICATED", "auth payload must be an object.")

    resolver = _resolve_callable(config, "AUTH_RESOLVER")
    if resolver is not None:
        user = resolver(auth_payload)
    else:
        user_id = auth_payload.get("user_id")
        if user_id is None:
            raise MCPAuthError("UNAUTHENTICATED", "auth.user_id is required.")
        UserModel = get_user_model()
        try:
            user = UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist as exc:  # type: ignore[attr-defined]
            raise MCPAuthError("UNAUTHENTICATED", "User not found.") from exc

    if not _is_authenticated(user):
        raise MCPAuthError("UNAUTHENTICATED", "Authentication required.")

    request_id = auth_payload.get("request_id") or str(uuid.uuid4())
    tenant = auth_payload.get("tenant")
    tenant_text = str(tenant) if tenant is not None else None
    return QueryContext(user=user, request_id=str(request_id), tenant=tenant_text)
