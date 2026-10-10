"""Selective planning history, backed by immutable native schema origins."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from hashlib import sha256
import json
from typing import Any, NoReturn

from general_manager.chat.planned.schema_projection import (
    _bounded,
    history_slots,
    MAX_CHARS,
)
from general_manager.chat.providers.base import Message
from general_manager.chat.schema_inspection import SNAPSHOT_PATTERN, TYPE_NAME_PATTERN

VERSION = "gm.planner-context/1"
REFERENCE_PREFIX = "HISTORICAL_SCHEMA_REFERENCE="


def _invalid() -> NoReturn:
    raise ValueError("invalid_planner_history_projection")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _history_size(role: str, content: str) -> int:
    return len(json.dumps(_dump({"role": role, "content": content})))


def project_planner_history(messages: Sequence[Message]) -> list[Message]:
    """Project qualified origins only; every original message remains untouched.

    References advertise a reload action, not current schema/completion proof.
    Old annotations without exact native payload bytes retain full visibility.
    """
    history_slots(messages)  # Validate all original role/content attestations first.
    result = []
    for message in messages:
        origin = message.historical_schema
        if origin is None or origin.payload_json is None:
            result.append(message)
            continue
        if (
            not isinstance(origin.payload_json, str)
            or len(origin.payload_json) > MAX_CHARS
        ):
            _invalid()
        try:
            binding = json.loads(origin.binding)
            native = json.loads(origin.payload_json)
        except (TypeError, ValueError, RecursionError):
            _invalid()
        if not isinstance(binding, dict) or binding.get("tool") != "get_manager_schema":
            result.append(message)
            continue
        _bounded(native)
        if (
            set(binding) != {"origin", "tool", "payload_sha256"}
            or sha256(origin.payload_json.encode()).hexdigest()
            != binding["payload_sha256"]
            or _dump(native) != origin.payload_json
            or origin.text_format.render(native) != message.content
            or not isinstance(binding["origin"], Mapping)
        ):
            _invalid()
        version = (
            native.get("inspection_version", 2) if isinstance(native, dict) else None
        )
        if (
            not isinstance(native, dict)
            or type(native.get("contract_version")) is not int
            or native["contract_version"] != 2
            or type(version) is not int
            or version not in {2, 3}
            or not isinstance(native.get("manager"), str)
            or not native["manager"].strip()
            or not isinstance(native.get("schema_view"), str)
            or native["schema_view"] not in {"overview", "detail", "full"}
            or not isinstance(native.get("snapshot"), str)
            or not SNAPSHOT_PATTERN.fullmatch(native["snapshot"])
            or native.get("schema_complete") is not (native["schema_view"] == "full")
            or "error" in native
            or native.get("status") == "error"
        ):
            result.append(message)
            continue
        source = binding["origin"]
        args = source.get("tool_args")
        if args is None or any(
            not isinstance(source.get(key), str) or not source[key]
            for key in ("conversation_id", "message_id")
        ):
            result.append(message)
            continue
        if (
            not isinstance(args, Mapping)
            or set(args) - {"manager", "view", "types", "snapshot"}
            or args.get("manager") != native["manager"]
            or args.get("view", "overview") != native["schema_view"]
        ):
            _invalid()
        reload_args: dict[str, Any] = {
            "manager": native["manager"],
            "view": native["schema_view"],
        }
        if native["schema_view"] == "detail":
            names = args.get("types")
            definitions = native.get("types")
            if (
                not isinstance(names, list)
                or not names
                or any(
                    not isinstance(n, str) or not TYPE_NAME_PATTERN.fullmatch(n)
                    for n in names
                )
                or len(set(names)) != len(names)
                or not isinstance(definitions, dict)
                or set(names) != set(definitions)
                or args.get("snapshot") != native["snapshot"]
            ):
                _invalid()
            reload_args.update(types=list(names), snapshot=native["snapshot"])
        elif "types" in args or "snapshot" in args:
            _invalid()
        pointer = {
            "format": "gm.schema-history-reference/1",
            "manager": native["manager"],
            "schema_view": native["schema_view"],
            "inspection_version": version,
            "snapshot": native["snapshot"],
            "original_content_sha256": origin.content_sha256,
            "original_payload_sha256": binding["payload_sha256"],
            "origin": dict(source),
            "reload": {"tool": "get_manager_schema", "arguments": reload_args},
            "current_schema_or_completion_authority": False,
        }
        content = REFERENCE_PREFIX + _dump(pointer)
        result.append(
            replace(message, content=content, historical_schema=None)
            if _history_size(message.role, content)
            < _history_size(message.role, message.content)
            else message
        )
    return result
