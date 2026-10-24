"""Verify planner history against each route's durable native source rows.

Raw traces remain intact. Only independently verified database locators become
conversation-relative ordinals in the temporary parity comparison.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

REFERENCE_PREFIX = "HISTORICAL_SCHEMA_REFERENCE="


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_parity_schema_history")
        result[key] = value
    return result


def schema_history_sources(
    conversation: Any, previous_count: int
) -> list[dict[str, Any]]:
    """Read only prior durable rows; derive their exact public history rendering."""
    from general_manager.chat.models import (
        get_conversation_messages,
        provider_messages_from_context,
    )
    from general_manager.chat.planned.planner_context import project_planner_history

    rows = get_conversation_messages(conversation)[:previous_count]
    indices = {str(row.pk): index for index, row in enumerate(rows)}
    messages = provider_messages_from_context(rows)
    projected = project_planner_history(messages)
    sources = []
    for original, selected in zip(messages, projected, strict=True):
        if selected.content == original.content:
            continue
        assert original.historical_schema is not None
        origin = json.loads(original.historical_schema.binding)["origin"]
        sources.append(
            {
                "durable_index": indices[origin["message_id"]],
                "origin": origin,
                "original": {"role": original.role, "content": original.content},
                "projected": {"role": selected.role, "content": selected.content},
            }
        )
    return sources


def history_messages(
    messages: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    *,
    relative: bool = False,
) -> list[dict[str, Any]]:
    """Restore native history or compare verified references, preserving order."""
    indexed = {}
    for source in sources:
        origin = source["origin"]
        key = (origin["conversation_id"], origin["message_id"])
        if key in indexed:
            raise ValueError("invalid_parity_schema_history")
        indexed[key] = source
    result = deepcopy(messages)
    for item in result:
        content = item.get("content")
        if (
            item.get("role") not in {"assistant", "tool"}
            or not isinstance(content, str)
            or not content.startswith(REFERENCE_PREFIX)
        ):
            continue
        try:
            pointer = json.loads(content[len(REFERENCE_PREFIX) :])
            origin = pointer["origin"]
            source = indexed[(origin["conversation_id"], origin["message_id"])]
        except (ValueError, TypeError, KeyError) as error:
            raise ValueError("invalid_parity_schema_history") from error
        if item != source["projected"]:
            raise ValueError("invalid_parity_schema_history")
        if relative:
            # All other source arguments, hashes, snapshots, reload instructions
            # and authority fields remain exact and participate in equality.
            pointer["origin"]["conversation_id"] = "parity-conversation"
            pointer["origin"]["message_id"] = str(source["durable_index"])
            item["content"] = REFERENCE_PREFIX + json.dumps(
                pointer, ensure_ascii=False, separators=(",", ":")
            )
        else:
            item.update(source["original"])
    return result


def provider_messages(
    call: dict[str, Any], sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep every provider field; verify only tagged planner context references."""
    messages: list[dict[str, Any]] = deepcopy(call["messages"])
    for message in messages:
        content = message.get("content", "")
        if not content.startswith("REFERENCE_DATA="):
            continue
        reference = json.loads(
            content.split("=", 1)[1], object_pairs_hook=_unique_pairs
        )
        if reference.get("planner_context_version") != "gm.planner-context/1":
            continue
        reference["conversation_context"] = history_messages(
            reference["conversation_context"], sources, relative=True
        )
        # Serialize again to preserve JSON scalar distinctions (true != 1,
        # 1 != 1.0); Python container equality would erase those differences.
        # Raw serialized messages remain available unmodified in the report.
        message["content"] = "REFERENCE_DATA=" + json.dumps(
            reference, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    return messages
