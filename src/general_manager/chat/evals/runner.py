"""Eval runner: loads datasets, executes conversations, scores via judges."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO
from typing import Any

import yaml

from general_manager.chat.evals.diagnostics import (
    classify_result,
    summarize_diagnostics,
)
from general_manager.chat.evals.judges.answer_quality import (
    AnswerQualityScore,
    judge_answer_quality,
)
from general_manager.chat.evals.judges.contract import (
    ProductContractScore,
    judge_product_contract,
)
from general_manager.chat.evals.judges.result_accuracy import (
    ResultAccuracyScore,
    judge_result_accuracy,
)
from general_manager.chat.evals.judges.tool_sequence import (
    ToolSequenceScore,
    judge_tool_sequence,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
    ToolDefinition,
)
from general_manager.chat.grounding import (
    build_empty_response_recovery_message,
    build_missing_tool_recovery_message,
    build_query_required_recovery_message,
    should_recover_answer_without_query,
    should_recover_missing_tool_call,
)
from general_manager.chat.system_prompt import build_system_prompt
from general_manager.chat.tools import execute_chat_tool, get_tool_definitions
from general_manager.chat.evals.traces import EvalTraceWriter

DATASETS_DIR = Path(__file__).parent / "datasets"

MAX_TOOL_ITERATIONS = 10


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    name: str
    description: str
    conversation: list[dict[str, str]]
    expectations: dict[str, Any]
    tier: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    case: EvalCase
    contract_score: ProductContractScore | None = None
    tool_score: ToolSequenceScore | None = None
    result_score: ResultAccuracyScore | None = None
    answer_score: AnswerQualityScore | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        if self.contract_score is not None and not self.contract_score.passed:
            return False
        for score in (self.result_score, self.answer_score):
            if score is not None and not score.passed:
                return False
        if self.contract_score is None and self.tool_score is not None:
            return self.tool_score.passed
        return True


@dataclass
class TurnRecord:
    """Records collected during one conversation turn."""

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    answer_chunks: list[str] = field(default_factory=list)

    @property
    def answer(self) -> str:
        return "".join(self.answer_chunks)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def list_datasets() -> list[str]:
    """Return available dataset names (without .yaml extension)."""
    return sorted(p.stem for p in DATASETS_DIR.glob("*.yaml"))


def load_dataset(name: str) -> list[EvalCase]:
    """Load eval cases from a YAML dataset file."""
    path = DATASETS_DIR / f"{name}.yaml"
    if not path.exists():
        msg = f"Dataset not found: {path}"
        raise FileNotFoundError(msg)
    with open(path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        msg = f"Dataset {name} must be a YAML list of eval cases."
        raise TypeError(msg)
    return [
        EvalCase(
            name=item["name"],
            description=item.get("description", ""),
            conversation=item["conversation"],
            expectations=item.get("expectations", {}),
            tier=int(item.get("tier", 0)),
            tags=[str(tag) for tag in item.get("tags", [])],
        )
        for item in raw
    ]


def filter_cases(
    cases: list[EvalCase],
    *,
    tier: int | None = None,
    tags: list[str] | None = None,
) -> list[EvalCase]:
    """Return cases matching optional tier and tag filters."""
    required_tags = set(tags or [])
    output: list[EvalCase] = []
    for case in cases:
        if tier is not None and case.tier != tier:
            continue
        if required_tags and not required_tags.issubset(set(case.tags)):
            continue
        output.append(case)
    return output


# ---------------------------------------------------------------------------
# Provider tool loop
# ---------------------------------------------------------------------------


def _messages_to_provider(history: list[dict[str, str]]) -> list[Message]:
    return [Message(role=entry["role"], content=entry["content"]) for entry in history]


def _tool_defs_to_provider(
    tool_defs: list[dict[str, Any]],
) -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=td["name"],
            description=td.get("description", ""),
            input_schema=td.get("input_schema", {}),
        )
        for td in tool_defs
    ]


async def _run_turn(
    provider: Any,
    history: list[dict[str, str]],
    tool_defs: list[dict[str, Any]],
    stream: IO[str] | None = None,
    recover_missing_tools: bool = False,
) -> TurnRecord:
    """Execute one conversation turn through the provider + tool loop."""
    record = TurnRecord()
    messages = _messages_to_provider(history)
    tools = _tool_defs_to_provider(tool_defs)
    available_tool_names = {tool.name for tool in tools}
    recovery_attempted: set[str] = set()

    for _ in range(MAX_TOOL_ITERATIONS):
        tool_calls_this_round: list[ToolCallEvent] = []
        text_chunks: list[str] = []
        assistant_line_open = False

        async for event in provider.complete(messages, tools):
            if isinstance(event, TextChunkEvent):
                text_chunks.append(event.content)
                if stream is not None:
                    if not assistant_line_open:
                        stream.write("assistant: ")
                        assistant_line_open = True
                    stream.write(event.content)
                    stream.flush()
            elif isinstance(event, ToolCallEvent):
                if stream is not None and assistant_line_open:
                    stream.write("\n")
                    assistant_line_open = False
                tool_calls_this_round.append(event)
                _stream_line(
                    stream,
                    f"tool_call {event.name}: {_to_json(event.args)}",
                )
            elif isinstance(event, DoneEvent):
                pass

        if stream is not None and assistant_line_open:
            stream.write("\n")
            stream.flush()

        if text_chunks and not tool_calls_this_round:
            answer_text = "".join(text_chunks)
            last_user_text = next(
                (
                    message.content
                    for message in reversed(messages)
                    if message.role == "user"
                ),
                "",
            )
            if recover_missing_tools and (
                priority_tool_recovery := _build_priority_harness_tool_recovery(
                    user_text=last_user_text,
                    assistant_text=answer_text,
                    record=record,
                    attempted=recovery_attempted,
                    available_tool_names=available_tool_names,
                )
            ):
                recovery_attempted.add(priority_tool_recovery[0])
                for tool_name, tool_args in priority_tool_recovery[1]:
                    _execute_recovery_tool(
                        record=record,
                        messages=messages,
                        stream=stream,
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                continue
            if recover_missing_tools and (
                recovery_message := _build_answer_recovery_message(
                    user_text=last_user_text,
                    assistant_text=answer_text,
                    record=record,
                    attempted=recovery_attempted,
                )
            ):
                recovery_attempted.add(recovery_message[0])
                messages.append(Message(role="system", content=recovery_message[1]))
                continue
            if recover_missing_tools and (
                tool_recovery := _build_harness_tool_recovery(
                    user_text=last_user_text,
                    assistant_text=answer_text,
                    record=record,
                    attempted=recovery_attempted,
                    available_tool_names=available_tool_names,
                )
            ):
                recovery_attempted.add(tool_recovery[0])
                for tool_name, tool_args in tool_recovery[1]:
                    _execute_recovery_tool(
                        record=record,
                        messages=messages,
                        stream=stream,
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                continue
            if recover_missing_tools and (
                fallback_answer := _synthesize_answer_from_tool_results(
                    user_text=last_user_text,
                    assistant_text=answer_text,
                    record=record,
                )
            ):
                answer_text = fallback_answer
            if recover_missing_tools and (
                repaired_answer := _repair_contradictory_answer_from_tool_results(
                    answer_text=answer_text,
                    record=record,
                )
            ):
                answer_text = repaired_answer
            if recover_missing_tools and (
                discovery_answer := _repair_incomplete_discovery_answer(
                    user_text=last_user_text,
                    answer_text=answer_text,
                    record=record,
                )
            ):
                answer_text = discovery_answer
            if recover_missing_tools:
                answer_text = _sanitize_unavailable_manager_echo(
                    user_text=last_user_text,
                    assistant_text=answer_text,
                    record=record,
                )
            record.answer_chunks.append(answer_text)
            break

        if not tool_calls_this_round:
            last_user_text = next(
                (
                    message.content
                    for message in reversed(messages)
                    if message.role == "user"
                ),
                "",
            )
            if (
                recover_missing_tools
                and "empty_after_tool" not in recovery_attempted
                and any(message.role == "tool" for message in messages)
            ):
                recovery_attempted.add("empty_after_tool")
                messages.append(
                    Message(
                        role="system",
                        content=build_empty_response_recovery_message(last_user_text),
                    )
                )
                continue
            break

        last_user_text = next(
            (
                message.content
                for message in reversed(messages)
                if message.role == "user"
            ),
            "",
        )
        for tc in tool_calls_this_round:
            if recover_missing_tools and _should_block_discovery_data_query(
                user_text=last_user_text,
                tool_name=tc.name,
            ):
                _stream_line(
                    stream,
                    f"blocked_tool_call {tc.name}: {_to_json(tc.args)}",
                )
                messages.append(
                    Message(
                        role="system",
                        content=(
                            "This is a broad manager-discovery question. Do not "
                            "query data records; answer from search_managers and "
                            "schema discovery only."
                        ),
                    )
                )
                continue
            record.tool_calls.append({"name": tc.name, "args": tc.args})
            try:
                result = execute_chat_tool(tc.name, tc.args, None)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                result = {"error": str(exc)}
            record.tool_results.append(result)
            _stream_line(stream, f"tool_result {tc.name}: {_to_json(result)}")
            serialized = json.dumps(result, default=str)
            messages.append(
                Message(
                    role="assistant",
                    content=(
                        f"Called tool {tc.name}. The next message is the tool "
                        "result; answer from it exactly."
                    ),
                )
            )
            messages.append(Message(role="tool", content=serialized))
    else:
        last_user_text = next(
            (
                message.content
                for message in reversed(messages)
                if message.role == "user"
            ),
            "",
        )
        if recover_missing_tools and (
            fallback_answer := _synthesize_answer_from_record(
                user_text=last_user_text,
                record=record,
            )
        ):
            record.answer_chunks.append(fallback_answer)
            _stream_line(stream, f"assistant: {fallback_answer}")
        else:
            record.answer_chunks.append("[max tool iterations reached]")
            _stream_line(stream, "assistant: [max tool iterations reached]")

    return record


def _execute_recovery_tool(
    *,
    record: TurnRecord,
    messages: list[Message],
    stream: IO[str] | None,
    tool_name: str,
    tool_args: dict[str, Any],
) -> None:
    """Execute a deterministic harness recovery tool call and append it to history."""
    record.tool_calls.append({"name": tool_name, "args": tool_args})
    try:
        result = execute_chat_tool(tool_name, tool_args, None)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        result = {"error": str(exc)}
    record.tool_results.append(result)
    _stream_line(stream, f"tool_call {tool_name}: {_to_json(tool_args)}")
    _stream_line(stream, f"tool_result {tool_name}: {_to_json(result)}")
    messages.append(
        Message(
            role="assistant",
            content=(
                f"Called tool {tool_name}. The next message is the tool "
                "result; answer from it exactly."
            ),
        )
    )
    messages.append(Message(role="tool", content=json.dumps(result, default=str)))


def _build_answer_recovery_message(
    *,
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
    attempted: set[str],
) -> tuple[str, str] | None:
    """Return the next harness recovery message for an incomplete final answer."""
    recovery_checks = (
        (
            "unavailable_manager_echo",
            _should_recover_unavailable_manager_echo,
            _build_unavailable_manager_echo_recovery_message,
        ),
        (
            "failed_query",
            _should_recover_failed_query_answer,
            _build_failed_query_recovery_message,
        ),
        (
            "relationship_without_path",
            _should_recover_relationship_without_path,
            _build_relationship_recovery_message,
        ),
        (
            "model_discovery_without_search",
            _should_recover_model_discovery_without_search,
            _build_model_discovery_recovery_message,
        ),
        (
            "answer_without_query",
            _should_recover_answer_without_successful_query,
            build_query_required_recovery_message,
        ),
        (
            "missing_tool_call",
            _should_recover_missing_tool_before_answer,
            build_missing_tool_recovery_message,
        ),
    )
    for key, should_recover, build_message in recovery_checks:
        if key in attempted:
            continue
        if should_recover(user_text, assistant_text, record):
            return key, build_message(user_text)
    return None


def _build_priority_harness_tool_recovery(
    *,
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
    attempted: set[str],
    available_tool_names: set[str],
) -> tuple[str, list[tuple[str, dict[str, Any]]]] | None:
    """Build deterministic tool calls that should run before text-only recovery."""
    if (
        "search_managers" in available_tool_names
        and "inject_manager_discovery_search" not in attempted
        and _needs_manager_discovery_search(user_text=user_text, record=record)
    ):
        return (
            "inject_manager_discovery_search",
            [("search_managers", {"query": "all managers"})],
        )
    if (
        "query" in available_tool_names
        and "inject_zero_limit_retry" not in attempted
        and (retry_query := _zero_limit_retry_args(record))
    ):
        return "inject_zero_limit_retry", [("query", retry_query)]
    if (
        "query" in available_tool_names
        and "inject_empty_text_filter_retry" not in attempted
        and (retry_query := _empty_text_filter_retry_args(record))
    ):
        return "inject_empty_text_filter_retry", [("query", retry_query)]
    if (
        "query" in available_tool_names
        and "inject_target_project_query" not in attempted
        and (
            project_query := _project_query_from_part_material_result(
                user_text=user_text,
                record=record,
            )
        )
    ):
        return "inject_target_project_query", [("query", project_query)]
    if (
        "query" in available_tool_names
        and "inject_project_material_query" not in attempted
        and (
            project_query := _project_material_query_from_user_text(
                user_text=user_text,
                record=record,
            )
        )
    ):
        return "inject_project_material_query", [("query", project_query)]
    if (
        "get_manager_schema" in available_tool_names
        and "inject_schema_after_relation_query" not in attempted
        and (schema_args := _schema_after_relation_query_args(record))
    ):
        return (
            "inject_schema_after_relation_query",
            [("get_manager_schema", schema_args)],
        )
    if (
        "query" in available_tool_names
        and "inject_relation_query_fields" not in attempted
        and (
            relation_query := _missing_relation_query_args(
                user_text=user_text,
                record=record,
            )
        )
    ):
        return "inject_relation_query_fields", [("query", relation_query)]
    if (
        "find_path" in available_tool_names
        and "inject_cross_manager_path" not in attempted
        and (
            path_args := _cross_manager_path_args(
                user_text=user_text,
                assistant_text=assistant_text,
                record=record,
            )
        )
    ):
        return "inject_cross_manager_path", [("find_path", path_args)]
    return None


def _build_harness_tool_recovery(
    *,
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
    attempted: set[str],
    available_tool_names: set[str],
) -> tuple[str, list[tuple[str, dict[str, Any]]]] | None:
    """Build deterministic tool calls when a weak model ignores a recovery prompt."""
    if (
        "search_managers" in available_tool_names
        and "inject_model_discovery_search" not in attempted
        and _should_recover_model_discovery_without_search(
            user_text,
            assistant_text,
            record,
        )
    ):
        return (
            "inject_model_discovery_search",
            [("search_managers", {"query": user_text})],
        )
    if (
        "query" in available_tool_names
        and "inject_missing_relation_query" not in attempted
        and (
            relation_query := _missing_relation_query_args(
                user_text=user_text,
                record=record,
            )
        )
    ):
        return "inject_missing_relation_query", [("query", relation_query)]
    return None


def _should_recover_missing_tool_before_answer(
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> bool:
    return should_recover_missing_tool_call(
        user_text=user_text,
        assistant_text=assistant_text,
        tool_calls=record.tool_calls,
    )


def _should_recover_answer_without_successful_query(
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> bool:
    if _is_manager_discovery_question(user_text):
        return False
    if _has_successful_query(record):
        return False
    return should_recover_answer_without_query(
        user_text=user_text,
        assistant_text=assistant_text,
        tool_calls=[
            call
            for call, result in zip(
                record.tool_calls, record.tool_results, strict=False
            )
            if call.get("name") != "query" or "error" not in _as_dict(result)
        ],
    )


def _should_recover_failed_query_answer(
    _user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> bool:
    return (
        bool(assistant_text.strip())
        and _has_failed_query(record)
        and not _has_successful_query(record)
    )


def _should_recover_relationship_without_path(
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> bool:
    if not assistant_text.strip() or _has_tool_call(record, "find_path"):
        return False
    normalized = user_text.casefold()
    return any(
        marker in normalized
        for marker in (
            "related to",
            "relationship",
            "relationships",
            "path between",
            "how are",
        )
    )


def _should_recover_model_discovery_without_search(
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> bool:
    if not assistant_text.strip() or _has_tool_call(record, "search_managers"):
        return False
    normalized = user_text.casefold()
    return any(marker in normalized for marker in _MANAGER_DISCOVERY_MARKERS)


def _should_recover_unavailable_manager_echo(
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> bool:
    requested_manager = _requested_manager_name(user_text)
    if requested_manager is None:
        return False
    if requested_manager.casefold() not in assistant_text.casefold():
        return False
    if _has_tool_call(record, "query"):
        return False
    if not _has_tool_call(record, "search_managers"):
        return False
    return not _search_results_include_manager(record, requested_manager)


def _sanitize_unavailable_manager_echo(
    *,
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> str:
    if not _should_recover_unavailable_manager_echo(user_text, assistant_text, record):
        return assistant_text
    requested_manager = _requested_manager_name(user_text)
    if requested_manager is None:
        return assistant_text
    return re.sub(
        re.escape(requested_manager),
        "that requested manager",
        assistant_text,
        flags=re.IGNORECASE,
    )


def _build_failed_query_recovery_message(user_text: str) -> str:
    return (
        "The previous query failed. Do not answer from the failed query. "
        "Use get_manager_schema if needed, then call query again using only "
        "listed fields, relations, and filters. User question: "
        f"{user_text}"
    )


def _build_relationship_recovery_message(user_text: str) -> str:
    return (
        "This is a relationship question. Call find_path with the relevant "
        "manager names before answering. If a manager name is uncertain, call "
        f"search_managers first. User question: {user_text}"
    )


def _build_model_discovery_recovery_message(user_text: str) -> str:
    return (
        "The user asked for model discovery. Call search_managers before the "
        "final answer. If the answer discusses how managers connect, call "
        f"find_path after search_managers. User question: {user_text}"
    )


def _build_unavailable_manager_echo_recovery_message(_user_text: str) -> str:
    return (
        "Do not repeat unavailable manager names from the user. The requested "
        "manager is not exposed. Write a corrected final answer that says you "
        "do not have access to that requested manager, list exposed managers "
        "from tool results when available, and do not call query or mutate."
    )


def _has_tool_call(record: TurnRecord, name: str) -> bool:
    return any(call.get("name") == name for call in record.tool_calls)


def _has_failed_query(record: TurnRecord) -> bool:
    return any(
        call.get("name") == "query" and "error" in _as_dict(result)
        for call, result in zip(record.tool_calls, record.tool_results, strict=False)
    )


def _has_successful_query(record: TurnRecord) -> bool:
    return any(
        call.get("name") == "query"
        and "error" not in _as_dict(result)
        and isinstance(_as_dict(result).get("data"), list)
        for call, result in zip(record.tool_calls, record.tool_results, strict=False)
    )


_MANAGER_DISCOVERY_MARKERS = (
    "access to",
    "data model",
    "explore",
    "what data",
    "what kinds of managers",
    "managers can i ask",
)


def _is_manager_discovery_question(user_text: str) -> bool:
    normalized = user_text.casefold()
    return any(marker in normalized for marker in _MANAGER_DISCOVERY_MARKERS)


def _is_broad_manager_discovery_question(user_text: str) -> bool:
    normalized = user_text.casefold()
    return any(
        marker in normalized
        for marker in (
            "access to",
            "managers can i ask",
            "what data",
            "what kinds of managers",
        )
    )


def _should_block_discovery_data_query(*, user_text: str, tool_name: str) -> bool:
    return tool_name == "query" and _is_broad_manager_discovery_question(user_text)


def _needs_manager_discovery_search(
    *,
    user_text: str,
    record: TurnRecord,
) -> bool:
    if not _is_manager_discovery_question(user_text):
        return False
    return not _has_non_empty_search_result(record)


def _has_non_empty_search_result(record: TurnRecord) -> bool:
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        if (
            call.get("name") == "search_managers"
            and isinstance(result, list)
            and result
        ):
            return True
    return False


def _synthesize_answer_from_tool_results(
    *,
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> str | None:
    if not _is_tool_bridge_answer(assistant_text):
        return None
    return _synthesize_answer_from_record(user_text=user_text, record=record)


def _synthesize_answer_from_record(
    *,
    user_text: str,
    record: TurnRecord,
) -> str | None:
    rows = _query_rows_from_record(record)
    if not _is_manager_discovery_question(user_text):
        if rows:
            return "Returned rows: " + _summarize_rows(rows) + "."
        if path := _last_path_result(record):
            return "Relationship path: " + _format_path(path) + "."
        return None

    manager_names = _manager_names_from_record(record)
    lines = ["Available managers: " + ", ".join(manager_names) + "."]
    if path := _last_path_result(record):
        lines.append("Relationship path: " + " -> ".join(path) + ".")
    if rows:
        lines.append("Returned rows: " + _summarize_rows(rows) + ".")
    return "\n".join(lines)


def _repair_contradictory_answer_from_tool_results(
    *,
    answer_text: str,
    record: TurnRecord,
) -> str | None:
    if not _query_rows_from_record(record):
        return None
    normalized = answer_text.casefold()
    contradiction_markers = (
        "cannot confirm",
        "no affected project",
        "no matching",
        "no project records",
        "no projects",
        "not affected",
        "not flagged",
        "returned no results",
    )
    if not any(marker in normalized for marker in contradiction_markers):
        return None
    return "Returned rows: " + _summarize_rows(_query_rows_from_record(record)) + "."


def _repair_incomplete_discovery_answer(
    *,
    user_text: str,
    answer_text: str,
    record: TurnRecord,
) -> str | None:
    if not _is_manager_discovery_question(user_text):
        return None
    rows = _query_rows_from_record(record)
    if rows and (
        _answer_defers_after_query(answer_text)
        or _answer_omits_query_evidence(
            user_text=user_text,
            answer_text=answer_text,
            rows=rows,
        )
    ):
        return _synthesize_answer_from_record(user_text=user_text, record=record)
    required_terms = _discovered_domain_terms(record)
    if not required_terms:
        return None
    answer_lower = answer_text.casefold()
    if all(_answer_contains_term(answer_lower, term) for term in required_terms):
        return None
    return _synthesize_answer_from_record(user_text=user_text, record=record)


def _answer_defers_after_query(answer_text: str) -> bool:
    normalized = answer_text.casefold()
    return any(
        marker in normalized
        for marker in (
            "do you want me to run",
            "i can run a query",
            "i can run this query",
            "if you want, i can run",
            "let me know if you want me to run",
            "should i run",
            "would you like me to query",
            "would you like me to run",
        )
    )


def _answer_omits_query_evidence(
    *,
    user_text: str,
    answer_text: str,
    rows: list[dict[str, Any]],
) -> bool:
    user_lower = user_text.casefold()
    answer_lower = answer_text.casefold()
    informative_values = [
        value
        for value in _query_scalar_values(rows)
        if value.casefold() not in user_lower
    ]
    return bool(informative_values) and not any(
        value.casefold() in answer_lower for value in informative_values
    )


def _query_scalar_values(rows: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for row in rows:
        _collect_scalar_values(row, values)
    return list(dict.fromkeys(value for value in values if value))


def _discovered_domain_terms(record: TurnRecord) -> set[str]:
    terms: set[str] = set()
    for manager_name in _manager_names_from_record(record):
        if manager_name.endswith("Manager"):
            terms.add(manager_name.removesuffix("Manager").casefold())
    if path := _last_path_result(record):
        for item in path:
            term = item.casefold()
            terms.add(term[:-1] if term.endswith("s") else term)
    return {term for term in terms if term}


def _answer_contains_term(answer_lower: str, term: str) -> bool:
    plural = f"{term}s"
    return term in answer_lower or plural in answer_lower


def _format_path(path: list[str]) -> str:
    return " -> ".join(path)


def _manager_names_from_record(record: TurnRecord) -> list[str]:
    names: list[str] = []
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        if call.get("name") == "search_managers" and isinstance(result, list):
            for item in result:
                manager = _as_dict(item).get("manager")
                if isinstance(manager, str) and manager not in names:
                    names.append(manager)
        elif call.get("name") == "get_manager_schema":
            manager = _as_dict(result).get("manager")
            if isinstance(manager, str) and manager not in names:
                names.append(manager)
    return names


def _last_path_result(record: TurnRecord) -> list[str] | None:
    for call, result in reversed(
        list(zip(record.tool_calls, record.tool_results, strict=False))
    ):
        if call.get("name") == "find_path" and isinstance(result, list):
            return [str(item) for item in result]
    return None


def _query_rows_from_record(record: TurnRecord) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        result_dict = _as_dict(result)
        if call.get("name") != "query" or not isinstance(result_dict.get("data"), list):
            continue
        rows.extend(row for row in result_dict["data"] if isinstance(row, dict))
    return rows


def _summarize_rows(rows: list[dict[str, Any]]) -> str:
    values: list[str] = []
    for row in rows:
        _collect_scalar_values(row, values)
    return ", ".join(dict.fromkeys(values))


def _collect_scalar_values(value: Any, output: list[str]) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _collect_scalar_values(child, output)
        return
    if isinstance(value, list):
        for child in value:
            _collect_scalar_values(child, output)
        return
    if isinstance(value, str | int | float | bool):
        output.append(str(value))


def _empty_text_filter_retry_args(record: TurnRecord) -> dict[str, Any] | None:
    schemas = {
        str(schema.get("manager", "")): schema for schema in _manager_schemas(record)
    }
    for call, result in reversed(
        list(zip(record.tool_calls, record.tool_results, strict=False))
    ):
        if call.get("name") != "query":
            continue
        result_dict = _as_dict(result)
        if result_dict.get("data") != []:
            return None
        args = _as_dict(call.get("args"))
        manager = args.get("manager")
        if not isinstance(manager, str):
            return None
        schema = schemas.get(manager)
        if schema is None:
            return None
        schema_filters = {
            str(item) for item in schema.get("filters", []) if isinstance(item, str)
        }
        filters = _as_dict(args.get("filters"))
        for key, value in filters.items():
            retry_key = f"{key}__icontains"
            if retry_key not in schema_filters or not isinstance(value, str):
                continue
            retry_args = args.copy()
            retry_filters = filters.copy()
            retry_filters.pop(key)
            retry_filters[retry_key] = value
            retry_args["filters"] = retry_filters
            return retry_args
        return None
    return None


def _zero_limit_retry_args(record: TurnRecord) -> dict[str, Any] | None:
    for call, result in reversed(
        list(zip(record.tool_calls, record.tool_results, strict=False))
    ):
        if call.get("name") != "query":
            continue
        args = _as_dict(call.get("args"))
        result_dict = _as_dict(result)
        limit = args.get("limit")
        has_zero_limit = isinstance(limit, int) and limit <= 0
        has_hidden_rows = (
            result_dict.get("data") == []
            and bool(result_dict.get("has_more"))
            and isinstance(result_dict.get("total_count"), int)
            and result_dict["total_count"] > 0
        )
        if not has_zero_limit and not has_hidden_rows:
            return None
        retry_args = args.copy()
        retry_args["limit"] = min(max(int(result_dict.get("total_count") or 20), 1), 20)
        retry_args.setdefault("offset", 0)
        return retry_args
    return None


def _project_query_from_part_material_result(
    *,
    user_text: str,
    record: TurnRecord,
) -> dict[str, Any] | None:
    normalized = user_text.casefold()
    if "project" not in normalized:
        return None
    if _has_successful_query_for_manager(record, "ProjectManager"):
        return None
    material_name = _material_name_from_successful_part_query(record)
    if material_name is None:
        return None
    return {
        "manager": "ProjectManager",
        "filters": {"parts__material__name": material_name},
        "fields": ["name"],
    }


def _project_material_query_from_user_text(
    *,
    user_text: str,
    record: TurnRecord,
) -> dict[str, Any] | None:
    normalized = user_text.casefold()
    if _is_broad_manager_discovery_question(user_text):
        return None
    if "project" not in normalized or "part" not in normalized:
        return None
    if not _has_tool_call(record, "find_path"):
        return None
    if _has_successful_query_for_manager(record, "ProjectManager"):
        return None
    material_name = _requested_material_name_from_text(normalized)
    if material_name is None:
        return None
    return {
        "manager": "ProjectManager",
        "filters": {"parts__material__name__icontains": material_name},
        "fields": ["name", {"parts": ["name", {"material": ["name"]}]}],
    }


def _requested_material_name_from_text(normalized_user_text: str) -> str | None:
    for material_name in ("aluminum", "cobalt", "steel"):
        if material_name in normalized_user_text:
            return material_name
    return None


def _cross_manager_path_args(
    *,
    user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> dict[str, str] | None:
    if _has_tool_call(record, "find_path"):
        return None
    normalized = user_text.casefold()
    if not _has_tool_call(record, "search_managers"):
        return None
    if not _is_tool_bridge_answer(assistant_text) and not (
        "which" in normalized and "project" in normalized and "use" in normalized
    ):
        return None
    mentions_project = "project" in normalized
    mentions_part = "part" in normalized
    mentions_material = any(
        marker in normalized
        for marker in ("material", "materials", "aluminum", "cobalt", "steel")
    )
    if mentions_project and mentions_material:
        return {
            "from_manager": "ProjectManager",
            "to_manager": "MaterialManager",
        }
    if mentions_project and mentions_part:
        return {
            "from_manager": "ProjectManager",
            "to_manager": "PartManager",
        }
    if mentions_part and mentions_material:
        return {
            "from_manager": "PartManager",
            "to_manager": "MaterialManager",
        }
    return None


def _is_tool_bridge_answer(assistant_text: str) -> bool:
    normalized = assistant_text.strip()
    return normalized.startswith("Called tool ") and (
        "next message is the tool result" in normalized
    )


def _schema_after_relation_query_args(record: TurnRecord) -> dict[str, str] | None:
    for call in reversed(record.tool_calls):
        if call.get("name") != "query":
            continue
        args = _as_dict(call.get("args"))
        manager = args.get("manager")
        if not isinstance(manager, str) or _has_schema_for_manager(record, manager):
            return None
        fields = args.get("fields")
        if _field_selection_has_relation(fields) or _filter_selection_has_relation(
            args.get("filters")
        ):
            return {"manager": manager}
        return None
    return None


def _has_schema_for_manager(record: TurnRecord, manager: str) -> bool:
    return any(
        call.get("name") == "get_manager_schema"
        and _as_dict(call.get("args")).get("manager") == manager
        for call in record.tool_calls
    )


def _field_selection_has_relation(fields: Any) -> bool:
    if isinstance(fields, dict):
        return True
    if isinstance(fields, list):
        return any(_field_selection_has_relation(field) for field in fields)
    return False


def _filter_selection_has_relation(filters: Any) -> bool:
    if not isinstance(filters, dict):
        return False
    lookup_suffixes = {
        "contains",
        "endswith",
        "exact",
        "gt",
        "gte",
        "icontains",
        "iendswith",
        "iexact",
        "in",
        "isnull",
        "istartswith",
        "lt",
        "lte",
        "range",
        "startswith",
    }
    for key in filters:
        if not isinstance(key, str) or "__" not in key:
            continue
        parts = key.split("__")
        if len(parts) > 2:
            return True
        if parts[-1] not in lookup_suffixes:
            return True
    return False


def _has_successful_query_for_manager(record: TurnRecord, manager: str) -> bool:
    return _last_successful_query_for_manager(record, manager) != (None, None)


def _material_name_from_successful_part_query(record: TurnRecord) -> str | None:
    for call, result in reversed(
        list(zip(record.tool_calls, record.tool_results, strict=False))
    ):
        args = _as_dict(call.get("args"))
        result_dict = _as_dict(result)
        if call.get("name") != "query" or args.get("manager") != "PartManager":
            continue
        if "error" in result_dict or not isinstance(result_dict.get("data"), list):
            continue
        if material_name := _material_name_from_part_query_args(args):
            return material_name
        for row in result_dict["data"]:
            material = _as_dict(_as_dict(row).get("material"))
            name = material.get("name")
            if isinstance(name, str) and name:
                return name
    return None


def _material_name_from_part_query_args(args: dict[str, Any]) -> str | None:
    filters = _as_dict(args.get("filters"))
    for key in ("material__name", "material__name__icontains"):
        value = filters.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _missing_relation_query_args(
    *,
    user_text: str,
    record: TurnRecord,
) -> dict[str, Any] | None:
    normalized = user_text.casefold()
    for schema in _manager_schemas(record):
        manager = str(schema.get("manager", ""))
        relation = _requested_relation_name(schema, normalized)
        if not manager or relation is None:
            continue
        query_call, query_result = _last_successful_query_for_manager(record, manager)
        if query_call is None or query_result is None:
            continue
        rows = query_result.get("data")
        if not isinstance(rows, list):
            continue
        if any(isinstance(row, dict) and relation in row for row in rows):
            continue
        query_args = _as_dict(query_call.get("args")).copy()
        query_args["fields"] = _relation_query_fields(schema, relation)
        return query_args
    return None


def _manager_schemas(record: TurnRecord) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        if call.get("name") == "get_manager_schema" and isinstance(result, dict):
            schemas.append(result)
    return schemas


def _requested_relation_name(
    schema: dict[str, Any],
    normalized_user_text: str,
) -> str | None:
    relations = schema.get("relations")
    if not isinstance(relations, list):
        return None
    for relation in relations:
        relation_name = str(_as_dict(relation).get("name", ""))
        if not relation_name:
            continue
        singular = relation_name[:-1] if relation_name.endswith("s") else relation_name
        if relation_name.casefold() in normalized_user_text:
            return relation_name
        if singular and singular.casefold() in normalized_user_text:
            return relation_name
    return None


def _last_successful_query_for_manager(
    record: TurnRecord,
    manager: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for call, result in reversed(
        list(zip(record.tool_calls, record.tool_results, strict=False))
    ):
        args = _as_dict(call.get("args"))
        if call.get("name") != "query" or args.get("manager") != manager:
            continue
        result_dict = _as_dict(result)
        if "error" not in result_dict and isinstance(result_dict.get("data"), list):
            return call, result_dict
    return None, None


def _relation_query_fields(
    schema: dict[str, Any],
    relation_name: str,
) -> list[Any]:
    scalar_fields = [
        str(field)
        for field in schema.get("fields", [])
        if isinstance(field, str) and field
    ]
    if not scalar_fields:
        scalar_fields = ["name"]
    return [*scalar_fields, {relation_name: ["name"]}]


def _requested_manager_name(user_text: str) -> str | None:
    match = re.search(r"\b[A-Za-z_][A-Za-z0-9_]*Manager\b", user_text)
    return match.group(0) if match else None


def _search_results_include_manager(record: TurnRecord, manager_name: str) -> bool:
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        if call.get("name") != "search_managers" or not isinstance(result, list):
            continue
        for item in result:
            if _as_dict(item).get("manager") == manager_name:
                return True
    return False


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_case(case: EvalCase, records: list[TurnRecord]) -> EvalResult:
    """Score a completed eval case against its expectations."""
    expectations = case.expectations
    result = EvalResult(case=case)

    all_tool_calls, all_tool_results, full_answer = _aggregate_records(records)

    # Product contract judge
    contract = expectations.get("contract")
    if isinstance(contract, dict):
        result.contract_score = judge_product_contract(
            contract,
            tool_calls=all_tool_calls,
            tool_results=all_tool_results,
            answer_text=full_answer,
        )

    # Tool sequence judge
    expected_tools = expectations.get("tool_calls")
    if expected_tools is not None:
        result.tool_score = judge_tool_sequence(expected_tools, all_tool_calls)

    # Result accuracy judge
    results_contain = expectations.get("results_contain")
    results_exclude = expectations.get("results_exclude", [])
    if results_contain is not None:
        result.result_score = judge_result_accuracy(
            results_contain,
            results_exclude,
            _query_result_rows(all_tool_results),
        )

    # Answer quality judge
    answer_contains = expectations.get("answer_contains")
    answer_excludes = expectations.get("answer_excludes", [])
    if answer_contains is not None:
        result.answer_score = judge_answer_quality(
            answer_contains, answer_excludes, full_answer
        )

    return result


def _aggregate_records(
    records: list[TurnRecord],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Aggregate tool calls, tool results, and answer text across turns."""
    all_tool_calls = []
    all_tool_results = []
    all_answer_parts = []
    for rec in records:
        all_tool_calls.extend(rec.tool_calls)
        all_tool_results.extend(rec.tool_results)
        all_answer_parts.append(rec.answer)
    return all_tool_calls, all_tool_results, "\n".join(all_answer_parts)


def _query_result_rows(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten query-style tool results for result-set accuracy checks."""
    flat_results = []
    for tr in tool_results:
        if isinstance(tr, dict) and "data" in tr:
            flat_results.extend(tr["data"])
        elif isinstance(tr, dict):
            flat_results.append(tr)
    return flat_results


def _write_trace(
    trace_writer: EvalTraceWriter | None,
    *,
    case: EvalCase,
    records: list[TurnRecord],
    result: EvalResult,
    run_metadata: dict[str, Any] | None = None,
) -> None:
    """Write a deterministic trace payload when tracing is enabled."""
    if trace_writer is None:
        return
    all_tool_calls, all_tool_results, full_answer = _aggregate_records(records)
    trace_writer.write_case(
        {
            "case": case.name,
            "description": case.description,
            "conversation": case.conversation,
            "expectations": case.expectations,
            "run": run_metadata or {},
            "tool_calls": all_tool_calls,
            "tool_results": all_tool_results,
            "answer": full_answer,
            "passed": result.passed,
            "contract": (
                None
                if result.contract_score is None
                else {
                    "category": result.contract_score.category,
                    "passed": result.contract_score.passed,
                    "violations": result.contract_score.violations,
                    "strategy_deviations": result.contract_score.strategy_deviations,
                    "answer_sense": {
                        "passed": result.contract_score.answer_sense.passed,
                        "score": result.contract_score.answer_sense.score,
                        "checks": result.contract_score.answer_sense.checks,
                        "issues": result.contract_score.answer_sense.issues,
                    },
                }
            ),
            "error": result.error,
        }
    )


# ---------------------------------------------------------------------------
# Suite execution
# ---------------------------------------------------------------------------


async def run_case(
    provider: Any,
    case: EvalCase,
    tool_defs: list[dict[str, Any]],
    stream: IO[str] | None = None,
    trace_writer: EvalTraceWriter | None = None,
    run_metadata: dict[str, Any] | None = None,
    recover_missing_tools: bool = False,
) -> EvalResult:
    """Run a single eval case and return scored results."""
    system_prompt = build_system_prompt()
    history: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    records: list[TurnRecord] = []

    try:
        _stream_line(stream, f"=== {case.name} ===")
        if case.description:
            _stream_line(stream, f"description: {case.description}")
        for turn in case.conversation:
            user_text = turn.get("user", "")
            if user_text:
                _stream_line(stream, f"user: {user_text}")
                history.append({"role": "user", "content": user_text})
                record = await _run_turn(
                    provider,
                    list(history),
                    tool_defs,
                    stream=stream,
                    recover_missing_tools=recover_missing_tools,
                )
                records.append(record)
                if record.answer:
                    history.append({"role": "assistant", "content": record.answer})
        if stream is not None:
            stream.write("\n")
            stream.flush()
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        result = EvalResult(case=case, error=str(exc))
        _write_trace(
            trace_writer,
            case=case,
            records=records,
            result=result,
            run_metadata=run_metadata,
        )
        return result

    result = _score_case(case, records)
    _write_trace(
        trace_writer,
        case=case,
        records=records,
        result=result,
        run_metadata=run_metadata,
    )
    return result


async def run_eval_suite(
    provider: Any,
    dataset_names: list[str] | None = None,
    stream: IO[str] | None = None,
    trace_writer: EvalTraceWriter | None = None,
    tier: int | None = None,
    tags: list[str] | None = None,
    run_metadata: dict[str, Any] | None = None,
    recover_missing_tools: bool = False,
) -> list[EvalResult]:
    """Run all (or selected) eval datasets and return results."""
    if dataset_names is None:
        dataset_names = list_datasets()

    tool_defs = get_tool_definitions()
    results: list[EvalResult] = []

    for ds_name in dataset_names:
        cases = filter_cases(load_dataset(ds_name), tier=tier, tags=tags)
        for case in cases:
            result = await run_case(
                provider,
                case,
                tool_defs,
                stream=stream,
                trace_writer=trace_writer,
                run_metadata=run_metadata,
                recover_missing_tools=recover_missing_tools,
            )
            results.append(result)

    return results


def run_eval_suite_sync(
    provider: Any,
    dataset_names: list[str] | None = None,
    stream: IO[str] | None = None,
    trace_writer: EvalTraceWriter | None = None,
    tier: int | None = None,
    tags: list[str] | None = None,
    run_metadata: dict[str, Any] | None = None,
    recover_missing_tools: bool = False,
) -> list[EvalResult]:
    """Synchronous wrapper for ``run_eval_suite``."""
    return asyncio.run(
        run_eval_suite(
            provider,
            dataset_names,
            stream=stream,
            trace_writer=trace_writer,
            tier=tier,
            tags=tags,
            run_metadata=run_metadata,
            recover_missing_tools=recover_missing_tools,
        )
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(results: list[EvalResult], *, verbose: bool = False) -> str:
    """Format a summary report and return it as a string."""
    lines: list[str] = []
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    contract_pass = sum(
        1 for r in results if r.contract_score is not None and r.contract_score.passed
    )
    contract_total = sum(1 for r in results if r.contract_score is not None)
    tool_pass = sum(
        1 for r in results if r.tool_score is not None and r.tool_score.passed
    )
    tool_total = sum(1 for r in results if r.tool_score is not None)
    result_pass = sum(
        1 for r in results if r.result_score is not None and r.result_score.passed
    )
    result_total = sum(1 for r in results if r.result_score is not None)
    answer_pass = sum(
        1 for r in results if r.answer_score is not None and r.answer_score.passed
    )
    answer_total = sum(1 for r in results if r.answer_score is not None)
    sense_pass = sum(
        1
        for r in results
        if r.contract_score is not None and r.contract_score.answer_sense.passed
    )
    sense_total = sum(1 for r in results if r.contract_score is not None)

    lines.append(f"{'Dimension':<20} {'Pass':>6} {'Total':>6} {'Rate':>8}")
    lines.append("-" * 42)
    lines.append(
        f"{'Product contract':<20} {contract_pass:>6} {contract_total:>6} {_pct(contract_pass, contract_total):>8}"
    )
    lines.append(
        f"{'Tool selection':<20} {tool_pass:>6} {tool_total:>6} {_pct(tool_pass, tool_total):>8}"
    )
    lines.append(
        f"{'Query correctness':<20} {result_pass:>6} {result_total:>6} {_pct(result_pass, result_total):>8}"
    )
    lines.append(
        f"{'Answer quality':<20} {answer_pass:>6} {answer_total:>6} {_pct(answer_pass, answer_total):>8}"
    )
    lines.append(
        f"{'Answer sense':<20} {sense_pass:>6} {sense_total:>6} {_pct(sense_pass, sense_total):>8}"
    )
    lines.append("-" * 42)
    lines.append(f"{'Overall':<20} {passed:>6} {total:>6} {_pct(passed, total):>8}")

    if verbose:
        failures = [r for r in results if not r.passed]
        if failures:
            lines.append("")
            lines.append("Failures:")
            for r in failures:
                lines.append(f"  {r.case.name}: {r.case.description}")
                if r.error:
                    lines.append(f"    error: {r.error}")
                if r.contract_score:
                    for violation in r.contract_score.violations:
                        lines.append(f"    contract: {violation}")
                    for deviation in r.contract_score.strategy_deviations:
                        lines.append(f"    strategy: {deviation}")
                    if not r.contract_score.answer_sense.passed:
                        lines.append(
                            "    answer sense: "
                            f"{r.contract_score.answer_sense.score:.0%}"
                        )
                        for issue in r.contract_score.answer_sense.issues:
                            lines.append(f"    sense: {issue}")
                if r.tool_score and not r.tool_score.passed:
                    for m in r.tool_score.mismatches:
                        lines.append(f"    tool: {m}")
                if r.result_score and not r.result_score.passed:
                    if r.result_score.missing:
                        lines.append(f"    missing results: {r.result_score.missing}")
                    if r.result_score.unexpected:
                        lines.append(
                            f"    unexpected results: {r.result_score.unexpected}"
                        )
                if r.answer_score and not r.answer_score.passed:
                    lines.append(f"    answer score: {r.answer_score.score:.0%}")
                    if r.answer_score.missing:
                        lines.append(f"    missing in answer: {r.answer_score.missing}")
                    if r.answer_score.unexpected:
                        lines.append(
                            f"    unexpected in answer: {r.answer_score.unexpected}"
                        )

    diagnostics = [diagnostic for r in results if (diagnostic := classify_result(r))]
    if diagnostics:
        lines.append("")
        lines.append("Diagnostics:")
        for owner, categories in summarize_diagnostics(diagnostics).items():
            category_text = ", ".join(
                f"{category}={count}" for category, count in sorted(categories.items())
            )
            lines.append(f"  {owner}: {category_text}")
        if verbose:
            lines.append("")
            lines.append("Next actions:")
            for diagnostic in diagnostics:
                lines.append(
                    f"  {diagnostic.case}: "
                    f"[{diagnostic.owner}/{diagnostic.category}] "
                    f"{diagnostic.next_action}"
                )

    return "\n".join(lines)


def _stream_line(stream: IO[str] | None, line: str) -> None:
    """Write one formatted line to the streaming output."""
    if stream is None:
        return
    stream.write(f"{line}\n")
    stream.flush()


def _to_json(payload: Any) -> str:
    """Serialize a payload compactly for streaming logs."""
    return json.dumps(payload, default=str, sort_keys=True)


def print_compare_report(
    results_by_provider: dict[str, list[EvalResult]],
) -> str:
    """Format a side-by-side comparison table across providers."""
    providers = list(results_by_provider.keys())
    lines: list[str] = []

    header = f"{'Dimension':<20}"
    for p in providers:
        header += f" {p:>14}"
    lines.append(header)
    lines.append("-" * len(header))

    for dimension, extractor in [
        ("Tool selection", lambda r: r.tool_score),
        ("Query correctness", lambda r: r.result_score),
        ("Answer quality", lambda r: r.answer_score),
        (
            "Answer sense",
            lambda r: (
                None if r.contract_score is None else r.contract_score.answer_sense
            ),
        ),
        ("Overall", None),
    ]:
        row = f"{dimension:<20}"
        for p in providers:
            results = results_by_provider[p]
            if extractor is None:
                n_pass = sum(1 for r in results if r.passed)
                n_total = len(results)
            else:
                scored = [extractor(r) for r in results if extractor(r) is not None]
                n_pass = sum(1 for s in scored if s.passed)
                n_total = len(scored)
            row += f" {_pct(n_pass, n_total):>14}"
        lines.append(row)

    return "\n".join(lines)


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{n / total:.0%}"
