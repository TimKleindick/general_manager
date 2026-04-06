"""Audit helpers for chat interactions."""

from __future__ import annotations

import json
from typing import Any

from django.utils.module_loading import import_string

from general_manager.chat.settings import get_chat_settings


def _should_emit(event_type: str, level: str) -> bool:
    if level == "off":
        return False
    if level == "messages":
        return event_type in {"user_message", "assistant_message"}
    return True


def _redact(value: Any, redact_fields: set[str]) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(field in lowered for field in redact_fields):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact(item, redact_fields)
        return redacted
    if isinstance(value, list):
        return [_redact(item, redact_fields) for item in value]
    return value


def _truncate_result(value: Any, max_result_size: int) -> str:
    serialized = json.dumps(value, sort_keys=True)
    if len(serialized) <= max_result_size:
        return serialized
    return f"{serialized[:max_result_size]}..."


def _resolve_sink() -> Any:
    audit_settings = get_chat_settings()["audit"]
    logger_path = audit_settings.get("logger")
    if logger_path is None:
        return None
    if callable(logger_path):
        return logger_path
    return import_string(str(logger_path))


def emit_chat_audit_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    sink: Any | None = None,
) -> None:
    """Emit one sanitized chat audit event when chat audit logging is enabled."""
    audit_settings = get_chat_settings()["audit"]
    if not audit_settings.get("enabled"):
        return
    level = str(audit_settings.get("level", "off"))
    if not _should_emit(event_type, level):
        return

    redact_fields = {
        str(field).lower() for field in audit_settings.get("redact_fields", [])
    }
    sanitized = _redact(payload, redact_fields)
    if "result" in sanitized:
        sanitized["result"] = _truncate_result(
            sanitized["result"],
            int(audit_settings.get("max_result_size", 4096)),
        )
    event = {"event_type": event_type, **sanitized}

    target = sink if sink is not None else _resolve_sink()
    if target is None:
        return
    if callable(target):
        target(event)
