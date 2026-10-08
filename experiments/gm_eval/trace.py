"""Synthetic experiment observations, separate from provider wire payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


_PRIVATE_KEYS = frozenset(
    {
        "authorization",
        "headers",
        "access_token",
        "refresh_token",
        "id_token",
        "encrypted_content",
        "credential",
        "password",
        "secret",
        "api_key",
        "api-key",
        "x-api-key",
        "apikey",
        "credentials",
        "client_secret",
        "clientsecret",
    }
)
_SECRET_TEXT = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*|\bsk-[A-Za-z0-9_-]{8,}|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
_SECRET_ASSIGNMENT = re.compile(
    r"""(?i)(["']?(?:access_token|refresh_token|id_token|encrypted_content|api[-_]?key|x-api-key|authorization|credentials?|client_secret|password|secret)["']?\s*[:=]\s*)(?:"(?:[^"\\]|\\.)*"|'[^']*'|[^\s,;}]+)"""
)


def _sanitize_text(value: str) -> str:
    """Redact embedded reference JSON and recognizable credential strings."""
    starts = [position for token in ("{", "[") if (position := value.find(token)) >= 0]
    if starts:
        start = min(starts)
        try:
            parsed = json.loads(value[start:])
        except (ValueError, RecursionError):
            pass
        else:
            cleaned = sanitize(parsed)
            if (
                cleaned == parsed
                and not _SECRET_TEXT.search(value)
                and not _SECRET_ASSIGNMENT.search(value)
            ):
                return value
            return _SECRET_ASSIGNMENT.sub(
                r'\1"[REDACTED]"', _SECRET_TEXT.sub("[REDACTED]", value[:start])
            ) + json.dumps(sanitize(parsed), separators=(",", ":"), ensure_ascii=False)
    return _SECRET_ASSIGNMENT.sub(
        r'\1"[REDACTED]"', _SECRET_TEXT.sub("[REDACTED]", value)
    )


def sanitize(value: Any) -> Any:
    """Keep structured synthetic observations without credentials or wire data."""
    if isinstance(value, Mapping):
        return {
            str(key): sanitize(item)
            for key, item in value.items()
            if str(key).lower() not in _PRIVATE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            sanitize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


def source_hashes(paths: Iterable[Path]) -> dict[str, str]:
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


@dataclass
class TraceRecorder:
    """One case's neutral provider calls and production audit observations."""

    turn: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, category: str, payload: Mapping[str, Any]) -> None:
        self.events.append(
            {"turn": self.turn, "category": category, **sanitize(payload)}
        )

    def audit(self, event: dict[str, Any]) -> None:
        self.record("audit", event)

    def as_dict(self) -> dict[str, Any]:
        strong_calls = [call for call in self.calls if call.get("strong")]
        return {
            "provider_calls": sanitize(self.calls),
            "events": sanitize(self.events),
            "provider_request_count": sum(call["request_count"] for call in self.calls),
            "strong_model_requests": sum(
                call["request_count"] for call in strong_calls
            ),
            "reported_cost": None,
            "strong_model_reported_cost": None,
            "estimated_cost": None,
            "cost_note": "No monetary price is assumed; reported usage is per request or null.",
        }
