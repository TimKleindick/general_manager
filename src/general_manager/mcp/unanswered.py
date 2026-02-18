"""Pluggable logging for unanswered MCP AI chat requests."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping

from django.apps import apps
from django.conf import settings
from django.utils.module_loading import import_string

from general_manager.logging import get_logger


logger = get_logger("mcp.unanswered")


@dataclass(slots=True)
class UnansweredEvent:
    """Structured event emitted when AI chat cannot answer cleanly."""

    question: str
    reason_code: str
    reason_message: str
    context: Any
    query_request: dict[str, Any] | None = None
    gateway_response: dict[str, Any] | None = None
    answer: str | None = None


def emit_unanswered_event(
    event: UnansweredEvent, gateway_config: Mapping[str, Any]
) -> None:
    """Dispatch unanswered event to configured logger callable."""
    logger_path = gateway_config.get("UNANSWERED_LOGGER")
    if not isinstance(logger_path, str) or not logger_path.strip():
        return

    try:
        handler = import_string(logger_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "unable to import unanswered logger",
            context={"path": logger_path, "error": type(exc).__name__},
        )
        return
    if not callable(handler):
        return

    try:
        handler(event=event, gateway_config=gateway_config)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "unanswered logger failed",
            context={"path": logger_path, "error": type(exc).__name__},
        )


def log_to_model(*, event: UnansweredEvent, gateway_config: Mapping[str, Any]) -> None:
    """Persist unanswered events to a Django model defined by setting."""
    model_label = gateway_config.get("UNANSWERED_LOG_MODEL")
    if not isinstance(model_label, str) or not model_label.strip():
        return

    model = apps.get_model(model_label)
    if model is None:
        return

    field_names = {
        field.name
        for field in model._meta.get_fields()  # type: ignore[attr-defined]
        if hasattr(field, "name")
    }
    payload: dict[str, Any] = {}
    if "user" in field_names:
        payload["user"] = getattr(event.context, "user", None)
    if "tenant" in field_names:
        payload["tenant"] = getattr(event.context, "tenant", None) or ""
    if "request_id" in field_names:
        payload["request_id"] = getattr(event.context, "request_id", "")
    if "question" in field_names:
        payload["question"] = event.question
    if "reason_code" in field_names:
        payload["reason_code"] = event.reason_code
    if "reason_message" in field_names:
        payload["reason_message"] = event.reason_message
    if "query_request" in field_names:
        payload["query_request"] = event.query_request or {}
    if "gateway_response" in field_names:
        payload["gateway_response"] = event.gateway_response or {}
    if "answer" in field_names:
        payload["answer"] = event.answer or ""

    model.objects.create(**payload)


def log_to_file(*, event: UnansweredEvent, gateway_config: Mapping[str, Any]) -> None:
    """Append unanswered events as JSONL for fast setup without migrations."""
    configured_path = gateway_config.get("UNANSWERED_LOG_FILE")
    if isinstance(configured_path, str) and configured_path.strip():
        path = Path(configured_path)
    else:
        base_dir = Path(str(getattr(settings, "BASE_DIR", Path.cwd())))
        path = base_dir / "unanswered_ai_requests.jsonl"

    record = {
        "question": event.question,
        "reason_code": event.reason_code,
        "reason_message": event.reason_message,
        "request_id": getattr(event.context, "request_id", ""),
        "tenant": getattr(event.context, "tenant", None),
        "user_id": getattr(getattr(event.context, "user", None), "id", None),
        "query_request": event.query_request or {},
        "gateway_response": event.gateway_response or {},
        "answer": event.answer or "",
    }
    line = json.dumps(record, ensure_ascii=True) + "\n"

    targets = [
        path,
        Path(tempfile.gettempdir()) / "general_manager_unanswered_ai_requests.jsonl",
    ]
    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            continue
        else:
            return

    raise OSError
