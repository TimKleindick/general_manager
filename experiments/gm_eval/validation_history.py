"""Reconstruct feedback from already stream/result-bound native sources.

This adapter is not a source or execution certificate. Its caller must bind
every tool, native event/result, original provider ordinal and chronology first.
History grouping and serialization remain owned by the actual task runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from typing import TYPE_CHECKING, Any, NoReturn

from general_manager.chat.planned.evidence import canonical_call_identity
from general_manager.chat.planned.scheduler import _TaskRuntime
from general_manager.chat.providers.base import ToolCallEvent

if TYPE_CHECKING:
    from general_manager.chat.planned.models import PlannedTask
    from general_manager.chat.providers.base import Message


def _invalid() -> NoReturn:
    message = "unbound history source order or arguments"
    raise ValueError(message)


def history_from_bound_sources(
    task: PlannedTask, sources: Sequence[Mapping[str, Any]]
) -> list[Message]:
    """Preserve backend identities, schema feedback selection and response groups."""
    runtime = _TaskRuntime(task=task, root_id=task.task_id)
    previous = 0
    group_start = 0
    identifiers: set[str] = set()
    for source in sources:
        index = source.get("index")
        if type(index) is not int or index < 1 or index < previous:
            _invalid()
        tool = source.get("tool")
        backend = source.get("args")
        if (
            not isinstance(tool, Mapping)
            or type(tool.get("id")) is not str
            or not tool["id"]
            or tool["id"] in identifiers
            or tool.get("name") not in {"get_manager_schema", "find_path", "query"}
            or not isinstance(tool.get("args"), Mapping)
            or not isinstance(backend, Mapping)
            or "result" not in source
        ):
            _invalid()
        expected = {k: v for k, v in tool["args"].items() if k != "requirement_id"}
        identity = canonical_call_identity(tool["name"], backend)
        if identity != canonical_call_identity(tool["name"], expected):
            _invalid()
        if previous and index != previous:
            runtime.group_tool_results(group_start)
            group_start = len(runtime.tool_history)
        feedback = json.loads(identity)["args"]
        # execute_tool deliberately retains schema selection only in feedback;
        # native backend arguments and cache identity exclude requirement_id.
        if tool["name"] == "get_manager_schema" and "requirement_id" in tool["args"]:
            feedback["requirement_id"] = tool["args"]["requirement_id"]
        runtime.remember_tool_result(
            ToolCallEvent(tool["id"], tool["name"], feedback),
            deepcopy(source["result"]),
        )
        previous = index
        identifiers.add(tool["id"])
    runtime.group_tool_results(group_start)
    return runtime.tool_history
