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

ALLOWED_RECOVERY_EVENTS = frozenset(
    {
        "answer_without_query",
        "block_discovery_data_query",
        "block_relationship_data_query",
        "empty_after_tool",
        "failed_query",
        "final_answer_after_tool_budget",
        "inject_anchor_relation_query",
        "inject_empty_text_filter_retry",
        "inject_failed_query_fields_retry",
        "inject_manager_discovery_search",
        "inject_missing_relation_query",
        "inject_model_discovery_search",
        "inject_discovered_manager_path",
        "inject_relation_query_fields",
        "inject_schema_after_relation_query",
        "inject_structured_filter_retry",
        "inject_target_manager_filter_query",
        "inject_target_manager_list_query",
        "inject_target_schema_after_path",
        "inject_zero_limit_retry",
        "missing_tool_call",
        "model_discovery_without_search",
        "relationship_without_path",
        "tool_bridge_answer",
        "unavailable_manager_echo",
        "unavailable_manager_echo_final_retry",
        "unavailable_manager_echo_minimal_retry",
        "unavailable_manager_echo_retry",
    }
)
FORBIDDEN_RECOVERY_EVENTS = frozenset(
    {
        "inject_cross_manager_path",
        "inject_project_material_query",
        "inject_target_project_query",
        "repair_contradictory_answer",
        "repair_incomplete_discovery_answer",
        "sanitize_unavailable_manager_echo",
        "synthesize_answer_after_max_iterations",
        "synthesize_answer_from_tool_results",
    }
)
FORBIDDEN_RECOVERY_PREFIXES = ("repair_", "synthesize_")
_LOOKUP_SUFFIXES = frozenset(
    {
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
)
_TARGET_VALUE_STOPWORDS = frozenset(
    {
        "about",
        "access",
        "affected",
        "ask",
        "all",
        "changed",
        "data",
        "deleted",
        "each",
        "every",
        "find",
        "first",
        "have",
        "help",
        "available",
        "catalog",
        "need",
        "explore",
        "inventory",
        "into",
        "its",
        "from",
        "with",
        "using",
        "and",
        "for",
        "can",
        "there",
        "their",
        "they",
        "them",
        "then",
        "this",
        "that",
        "your",
        "you",
        "me",
        "i",
        "are",
        "field",
        "fields",
        "list",
        "manager",
        "to",
        "if",
        "model",
        "named",
        "records",
        "show",
        "the",
        "updated",
        "use",
        "uses",
        "what",
        "which",
        "would",
    }
)


def is_forbidden_recovery_event(event: str) -> bool:
    """Return whether a recovery event is forbidden in production gates."""
    if event in FORBIDDEN_RECOVERY_EVENTS:
        return True
    if event.startswith(FORBIDDEN_RECOVERY_PREFIXES):
        return True
    return event not in ALLOWED_RECOVERY_EVENTS


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
    recovery_events: list[str] = field(default_factory=list)

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
    recovery_events: list[str] = field(default_factory=list)

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
                record.recovery_events.append(priority_tool_recovery[0])
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
                record.recovery_events.append(recovery_message[0])
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
                record.recovery_events.append(tool_recovery[0])
                for tool_name, tool_args in tool_recovery[1]:
                    _execute_recovery_tool(
                        record=record,
                        messages=messages,
                        stream=stream,
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                continue
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
                record.recovery_events.append("empty_after_tool")
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
            if recover_missing_tools and _should_block_relationship_data_query(
                user_text=last_user_text,
                tool_name=tc.name,
                record=record,
            ):
                _stream_line(
                    stream,
                    f"blocked_tool_call {tc.name}: {_to_json(tc.args)}",
                )
                record.recovery_events.append("block_relationship_data_query")
                messages.append(
                    Message(
                        role="system",
                        content=(
                            "This is a relationship/path question, not a data "
                            "record query. Do not call query. Answer from the "
                            "find_path result and include the path terms."
                        ),
                    )
                )
                continue
            if recover_missing_tools and _should_block_discovery_data_query(
                user_text=last_user_text,
                tool_name=tc.name,
            ):
                _stream_line(
                    stream,
                    f"blocked_tool_call {tc.name}: {_to_json(tc.args)}",
                )
                record.recovery_events.append("block_discovery_data_query")
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
            if (
                recover_missing_tools
                and tc.name == "query"
                and "inject_structured_filter_retry" not in record.recovery_events
                and (
                    retry_query := _structured_filter_retry_args_for_candidate(
                        record=record,
                        candidate_args=tc.args,
                    )
                )
            ):
                _stream_line(
                    stream,
                    f"blocked_tool_call {tc.name}: {_to_json(tc.args)}",
                )
                record.recovery_events.append("inject_structured_filter_retry")
                _execute_recovery_tool(
                    record=record,
                    messages=messages,
                    stream=stream,
                    tool_name="query",
                    tool_args=retry_query,
                )
                continue
            if (
                recover_missing_tools
                and tc.name == "query"
                and "inject_anchor_relation_query" not in record.recovery_events
                and (
                    anchor_retry := _anchor_relation_recovery_tools_for_candidate(
                        user_text=last_user_text,
                        record=record,
                        candidate_args=tc.args,
                    )
                )
            ):
                _stream_line(
                    stream,
                    f"blocked_tool_call {tc.name}: {_to_json(tc.args)}",
                )
                record.recovery_events.append("inject_anchor_relation_query")
                for tool_name, tool_args in anchor_retry:
                    _execute_recovery_tool(
                        record=record,
                        messages=messages,
                        stream=stream,
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                continue
            if (
                recover_missing_tools
                and tc.name == "query"
                and "inject_target_manager_filter_query" not in record.recovery_events
                and (
                    target_retry := _target_manager_filter_recovery_tools_for_candidate(
                        user_text=last_user_text,
                        record=record,
                        candidate_args=tc.args,
                    )
                )
            ):
                _stream_line(
                    stream,
                    f"blocked_tool_call {tc.name}: {_to_json(tc.args)}",
                )
                record.recovery_events.append("inject_target_manager_filter_query")
                for tool_name, tool_args in target_retry:
                    _execute_recovery_tool(
                        record=record,
                        messages=messages,
                        stream=stream,
                        tool_name=tool_name,
                        tool_args=tool_args,
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
        if recover_missing_tools and _has_successful_query(record):
            record.recovery_events.append("final_answer_after_tool_budget")
            messages.append(
                Message(
                    role="system",
                    content=(
                        "The tool-call budget is exhausted. Do not call any more "
                        "tools. Write the final answer now using only the "
                        "successful query results already returned. Include the "
                        f"actual returned values. User question: {last_user_text}"
                    ),
                )
            )
            if final_answer := await _collect_model_final_answer(
                provider=provider,
                messages=messages,
                stream=stream,
            ):
                record.answer_chunks.append(final_answer)
                return record
        record.answer_chunks.append("[max tool iterations reached]")
        _stream_line(stream, "assistant: [max tool iterations reached]")

    return record


async def _collect_model_final_answer(
    *,
    provider: Any,
    messages: list[Message],
    stream: IO[str] | None,
) -> str:
    text_chunks: list[str] = []
    assistant_line_open = False
    async for event in provider.complete(messages, []):
        if isinstance(event, TextChunkEvent):
            text_chunks.append(event.content)
            if stream is not None:
                if not assistant_line_open:
                    stream.write("assistant: ")
                    assistant_line_open = True
                stream.write(event.content)
                stream.flush()
        elif isinstance(event, DoneEvent):
            pass
    if stream is not None and assistant_line_open:
        stream.write("\n")
        stream.flush()
    return "".join(text_chunks)


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
            "unavailable_manager_echo_retry",
            _should_recover_unavailable_manager_echo,
            _build_unavailable_manager_echo_recovery_message,
        ),
        (
            "unavailable_manager_echo_final_retry",
            _should_recover_unavailable_manager_echo,
            _build_unavailable_manager_echo_final_recovery_message,
        ),
        (
            "unavailable_manager_echo_minimal_retry",
            _should_recover_unavailable_manager_echo,
            _build_unavailable_manager_echo_minimal_recovery_message,
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
            "tool_bridge_answer",
            _should_recover_tool_bridge_answer,
            _build_tool_bridge_answer_recovery_message,
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
        and "inject_structured_filter_retry" not in attempted
        and (retry_query := _structured_filter_retry_args(record))
    ):
        return "inject_structured_filter_retry", [("query", retry_query)]
    if (
        "query" in available_tool_names
        and "inject_failed_query_fields_retry" not in attempted
        and (
            retry_query := _failed_query_fields_retry_args(record, user_text=user_text)
        )
    ):
        return "inject_failed_query_fields_retry", [("query", retry_query)]
    if (
        "query" in available_tool_names
        and "inject_empty_text_filter_retry" not in attempted
        and (retry_query := _empty_text_filter_retry_args(record))
    ):
        return "inject_empty_text_filter_retry", [("query", retry_query)]
    if (
        "get_manager_schema" in available_tool_names
        and "inject_target_schema_after_path" not in attempted
        and (
            schema_args := _target_schema_after_path_args(
                user_text=user_text,
                record=record,
            )
        )
    ):
        return "inject_target_schema_after_path", [("get_manager_schema", schema_args)]
    if (
        "query" in available_tool_names
        and "inject_target_manager_list_query" not in attempted
        and (
            target_recovery := _target_manager_list_recovery_tools(
                user_text=user_text,
                record=record,
            )
        )
    ):
        return "inject_target_manager_list_query", target_recovery
    if (
        "query" in available_tool_names
        and "inject_anchor_relation_query" not in attempted
        and (
            anchor_recovery := _anchor_relation_recovery_tools(
                user_text=user_text,
                record=record,
            )
        )
    ):
        return "inject_anchor_relation_query", anchor_recovery
    if (
        "query" in available_tool_names
        and "inject_target_manager_filter_query" not in attempted
        and (
            target_recovery := _target_manager_filter_recovery_tools(
                user_text=user_text,
                record=record,
                available_tool_names=available_tool_names,
            )
        )
    ):
        return "inject_target_manager_filter_query", target_recovery
    if (
        "find_path" in available_tool_names
        and "inject_discovered_manager_path" not in attempted
        and (path_args := _discovered_manager_path_args(record))
    ):
        return "inject_discovered_manager_path", [("find_path", path_args)]
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
    if _has_unavailable_requested_manager_search(user_text, record):
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


def _should_recover_tool_bridge_answer(
    _user_text: str,
    assistant_text: str,
    record: TurnRecord,
) -> bool:
    return _is_tool_bridge_answer(assistant_text) and any(
        _tool_result_has_successful_evidence(result) for result in record.tool_results
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
    return _has_unavailable_requested_manager_search(user_text, record)


def _has_unavailable_requested_manager_search(
    user_text: str,
    record: TurnRecord,
) -> bool:
    requested_manager = _requested_manager_name(user_text)
    if requested_manager is None:
        return False
    if not _has_tool_call(record, "search_managers"):
        return False
    return not _search_results_include_manager(record, requested_manager)


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
        "Do not repeat, spell, quote, or copy unavailable manager names from "
        "the user. Your next answer must not contain any user-provided token "
        "ending in Manager. The requested manager is not exposed. Write a "
        "corrected final answer that uses the phrase 'that requested manager', "
        "lists exposed managers from tool results when available, and does "
        "not call query or mutate."
    )


def _build_unavailable_manager_echo_final_recovery_message(_user_text: str) -> str:
    return (
        "The previous answer still copied the unavailable manager name. Write "
        "a corrected final answer in two short sentences. Use the exact phrase "
        "'that requested manager' for the unavailable target. Do not write, "
        "spell, quote, or copy any other manager name from the user. You may "
        "list only exposed manager names from the search_managers tool result."
    )


def _build_unavailable_manager_echo_minimal_recovery_message(_user_text: str) -> str:
    return (
        "The previous answer still failed because it included the unavailable "
        "manager name. Reply with exactly this sentence and nothing else: "
        "I do not have access to that requested manager."
    )


def _build_tool_bridge_answer_recovery_message(user_text: str) -> str:
    return (
        "The previous response was an internal tool bridge, not a final answer. "
        "Write a concise final answer from the previous tool result values. "
        "Include the actual returned values that answer the user. Do not mention "
        "tool calls or ask to run another query. Do not include code fences, raw "
        "GraphQL, JSON tool arguments, YAML, or query examples. Do not propose "
        f"another query after data has already been returned. User question: {user_text}"
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


def _has_non_empty_successful_query(record: TurnRecord) -> bool:
    return any(
        call.get("name") == "query"
        and "error" not in _as_dict(result)
        and isinstance(_as_dict(result).get("data"), list)
        and bool(_as_dict(result).get("data"))
        for call, result in zip(record.tool_calls, record.tool_results, strict=False)
    )


def _tool_result_has_successful_evidence(result: Any) -> bool:
    if isinstance(result, list):
        return bool(result)
    result_dict = _as_dict(result)
    if "error" in result_dict:
        return False
    if isinstance(result_dict.get("data"), list):
        return True
    return bool(result_dict)


def _is_tool_bridge_answer(assistant_text: str) -> bool:
    normalized = assistant_text.strip()
    return normalized.startswith("Called tool ") and (
        "next message is the tool result" in normalized
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


def _should_block_relationship_data_query(
    *,
    user_text: str,
    tool_name: str,
    record: TurnRecord,
) -> bool:
    return (
        tool_name == "query"
        and _is_relationship_only_question(user_text)
        and _has_tool_call(record, "find_path")
    )


def _is_relationship_only_question(user_text: str) -> bool:
    normalized = user_text.casefold()
    has_relationship_marker = any(
        marker in normalized
        for marker in (
            "how are",
            "related",
            "relationship",
            "relationships",
            "path between",
        )
    )
    has_data_row_marker = any(
        marker in normalized
        for marker in (
            "which ",
            "list ",
            "show ",
            "find ",
            "how many ",
            "what parts",
            "what projects",
        )
    )
    return has_relationship_marker and not has_data_row_marker


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
        filters = _as_dict(args.get("filters"))
        empty_filter_keys = [
            key
            for key, value in filters.items()
            if isinstance(key, str) and (value == "" or value is None)
        ]
        if empty_filter_keys:
            retry_args = args.copy()
            retry_filters = {
                key: value
                for key, value in filters.items()
                if key not in empty_filter_keys
            }
            if retry_filters:
                retry_args["filters"] = retry_filters
            else:
                retry_args.pop("filters", None)
            return retry_args
        schema = schemas.get(manager)
        if schema is None:
            return None
        schema_filters = {
            str(item) for item in schema.get("filters", []) if isinstance(item, str)
        }
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


def _structured_filter_retry_args(record: TurnRecord) -> dict[str, Any] | None:
    pairs = list(zip(record.tool_calls, record.tool_results, strict=False))
    for index, (call, result) in enumerate(pairs):
        if call.get("name") != "query":
            continue
        result_dict = _as_dict(result)
        if "error" not in result_dict:
            continue
        args = _as_dict(call.get("args"))
        manager = args.get("manager")
        if not isinstance(manager, str) or not manager:
            continue
        parsed_filters = _parse_text_filter_mapping(args.get("filters"))
        if not parsed_filters:
            continue
        if _has_later_successful_query_with_filters(
            pairs=pairs,
            start=index,
            manager=manager,
            expected_filters=parsed_filters,
        ):
            continue

        retry_args = _latest_later_successful_query_args(
            pairs=pairs,
            start=index,
            manager=manager,
        )
        if retry_args is None:
            retry_args = args.copy()
        else:
            retry_args = retry_args.copy()
        retry_args["manager"] = manager
        retry_filters = _as_dict(retry_args.get("filters")).copy()
        retry_filters.update(parsed_filters)
        retry_args["filters"] = retry_filters

        fields = retry_args.get("fields")
        if not isinstance(fields, list | dict):
            parsed_fields = _parse_text_fields(args.get("fields"))
            if parsed_fields:
                retry_args["fields"] = parsed_fields
        return retry_args
    return None


def _structured_filter_retry_args_for_candidate(
    *,
    record: TurnRecord,
    candidate_args: dict[str, Any],
) -> dict[str, Any] | None:
    manager = candidate_args.get("manager")
    if not isinstance(manager, str) or not manager:
        return None
    candidate_filters = _as_dict(candidate_args.get("filters"))
    for call, result in reversed(
        list(zip(record.tool_calls, record.tool_results, strict=False))
    ):
        if call.get("name") != "query":
            continue
        result_dict = _as_dict(result)
        if "error" not in result_dict:
            continue
        args = _as_dict(call.get("args"))
        if args.get("manager") != manager:
            continue
        parsed_filters = _parse_text_filter_mapping(args.get("filters"))
        if not parsed_filters:
            continue
        if all(
            candidate_filters.get(key) == value for key, value in parsed_filters.items()
        ):
            return None

        retry_args = candidate_args.copy()
        retry_filters = candidate_filters.copy()
        retry_filters.update(parsed_filters)
        retry_args["filters"] = retry_filters
        fields = retry_args.get("fields")
        if not isinstance(fields, list | dict):
            parsed_fields = _parse_text_fields(args.get("fields"))
            if parsed_fields:
                retry_args["fields"] = parsed_fields
        return retry_args
    return None


def _has_later_successful_query_with_filters(
    *,
    pairs: list[tuple[dict[str, Any], Any]],
    start: int,
    manager: str,
    expected_filters: dict[str, Any],
) -> bool:
    for call, result in pairs[start + 1 :]:
        args = _as_dict(call.get("args"))
        result_dict = _as_dict(result)
        if (
            call.get("name") != "query"
            or args.get("manager") != manager
            or "error" in result_dict
            or not isinstance(result_dict.get("data"), list)
        ):
            continue
        filters = _as_dict(args.get("filters"))
        if all(filters.get(key) == value for key, value in expected_filters.items()):
            return True
    return False


def _latest_later_successful_query_args(
    *,
    pairs: list[tuple[dict[str, Any], Any]],
    start: int,
    manager: str,
) -> dict[str, Any] | None:
    for call, result in reversed(pairs[start + 1 :]):
        args = _as_dict(call.get("args"))
        result_dict = _as_dict(result)
        if (
            call.get("name") == "query"
            and args.get("manager") == manager
            and "error" not in result_dict
            and isinstance(result_dict.get("data"), list)
        ):
            return args
    return None


def _parse_text_filter_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, str):
        return {}
    text = value.strip().strip("[]{}")
    filters: dict[str, str] = {}
    for part in text.split(","):
        raw_part = part.strip()
        if not raw_part:
            continue
        if ":" in raw_part:
            raw_key, raw_value = raw_part.split(":", 1)
        elif "=" in raw_part:
            raw_key, raw_value = raw_part.split("=", 1)
        else:
            continue
        key = re.sub(r"[^A-Za-z0-9_]+", "", raw_key)
        parsed_value = raw_value.strip().strip("'\"")
        if key and parsed_value:
            filters[key] = parsed_value
    return filters


def _parse_text_field_selection(value: Any) -> list[Any]:
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return _parse_text_fields(value)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict | str):
        return [parsed]
    return []


def _parse_text_fields(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    text = value.strip().strip("[]{}")
    fields = []
    for part in text.split(","):
        field = re.sub(r"[^A-Za-z0-9_]+", "", part)
        if field:
            fields.append(field)
    return fields


def _failed_query_fields_retry_args(
    record: TurnRecord,
    *,
    user_text: str,
) -> dict[str, Any] | None:
    schemas_by_manager = {
        str(schema.get("manager", "")): schema for schema in _manager_schemas(record)
    }
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    pairs = list(zip(record.tool_calls, record.tool_results, strict=False))
    for index, (call, result) in enumerate(pairs):
        if call.get("name") != "query":
            continue
        result_dict = _as_dict(result)
        args = _as_dict(call.get("args"))
        if not _query_error_supports_field_retry(result_dict, args):
            continue
        manager = args.get("manager")
        if not isinstance(manager, str):
            continue
        schema = schemas_by_manager.get(manager)
        if schema is None:
            continue
        retry_fields = _sanitize_query_fields(
            args.get("fields"),
            schema=schema,
            schemas_by_manager=schemas_by_manager,
        )
        if not retry_fields or retry_fields == args.get("fields"):
            continue
        if _has_later_query_with_fields(
            pairs=pairs,
            start=index,
            manager=manager,
            fields=retry_fields,
        ):
            continue
        retry_args = args.copy()
        retry_args["fields"] = retry_fields
        retry_filters = _sanitize_query_filters(
            args.get("filters"),
            user_text=user_text,
        )
        if retry_filters:
            retry_args["filters"] = retry_filters
        else:
            retry_args.pop("filters", None)
        candidates.append((_field_selection_score(retry_fields), index, retry_args))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], -item[1]))
    return candidates[0][2]


def _sanitize_query_filters(
    filters: Any,
    *,
    user_text: str,
) -> dict[str, Any]:
    if not isinstance(filters, dict):
        return {}
    normalized_user_text = user_text.casefold()
    sanitized: dict[str, Any] = {}
    for key, value in filters.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, str):
            if value and value.casefold() in normalized_user_text:
                sanitized[key] = value
            continue
        sanitized[key] = value
    return sanitized


def _query_error_mentions_invalid_field(result: dict[str, Any]) -> bool:
    error = result.get("error")
    if not isinstance(error, str):
        return False
    normalized = error.casefold()
    return "cannot query field" in normalized or "is not defined by type" in normalized


def _query_error_supports_field_retry(
    result: dict[str, Any],
    args: dict[str, Any],
) -> bool:
    if _query_error_mentions_invalid_field(result):
        return True
    if not isinstance(args.get("fields"), str):
        return False
    error = result.get("error")
    if not isinstance(error, str):
        return False
    normalized = error.casefold()
    return "syntax error" in normalized or "unexpected character" in normalized


def _has_later_query_with_fields(
    *,
    pairs: list[tuple[dict[str, Any], Any]],
    start: int,
    manager: str,
    fields: list[Any],
) -> bool:
    for call, _result in pairs[start + 1 :]:
        args = _as_dict(call.get("args"))
        if call.get("name") == "query" and args.get("manager") == manager:
            if args.get("fields") == fields:
                return True
    return False


def _sanitize_query_fields(
    fields: Any,
    *,
    schema: dict[str, Any],
    schemas_by_manager: dict[str, dict[str, Any]],
) -> list[Any]:
    scalar_fields = {
        str(field) for field in schema.get("fields", []) if isinstance(field, str)
    }
    relation_targets = {
        str(_as_dict(relation).get("name")): str(_as_dict(relation).get("target"))
        for relation in schema.get("relations", [])
        if isinstance(_as_dict(relation).get("name"), str)
        and isinstance(_as_dict(relation).get("target"), str)
    }
    if isinstance(fields, str):
        parsed_fields = _parse_text_field_selection(fields)
        requested_fields = parsed_fields if parsed_fields else [fields]
    else:
        requested_fields = fields if isinstance(fields, list) else [fields]
    sanitized: list[Any] = []
    for requested_field in requested_fields:
        if isinstance(requested_field, str):
            if requested_field in scalar_fields:
                sanitized.append(requested_field)
            continue
        if not isinstance(requested_field, dict):
            continue
        for relation_name, child_fields in requested_field.items():
            if (
                not isinstance(relation_name, str)
                or relation_name not in relation_targets
            ):
                continue
            child_schema = schemas_by_manager.get(relation_targets[relation_name])
            if child_schema is None:
                sanitized_child = _fallback_relation_query_fields(child_fields)
            else:
                sanitized_child = _sanitize_query_fields(
                    child_fields,
                    schema=child_schema,
                    schemas_by_manager=schemas_by_manager,
                )
            sanitized.append({relation_name: sanitized_child or ["name"]})
    if not any(isinstance(field, str) for field in sanitized):
        if "name" in scalar_fields:
            sanitized.insert(0, "name")
        elif scalar_fields:
            sanitized.insert(0, sorted(scalar_fields)[0])
    return _dedupe_field_selection(sanitized)


def _fallback_relation_query_fields(fields: Any) -> list[Any]:
    requested_fields = fields if isinstance(fields, list) else [fields]
    scalar_names = [field for field in requested_fields if isinstance(field, str)]
    if "name" in scalar_names:
        return ["name"]
    if scalar_names:
        return [scalar_names[0]]
    return ["name"]


def _dedupe_field_selection(fields: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for selected_field in fields:
        key = json.dumps(selected_field, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(selected_field)
    return deduped


def _field_selection_score(fields: list[Any]) -> int:
    score = 0
    for selected_field in fields:
        if isinstance(selected_field, dict):
            score += 3
            score += _field_selection_score(
                [
                    child
                    for child_fields in selected_field.values()
                    for child in (
                        child_fields if isinstance(child_fields, list) else []
                    )
                ]
            )
        elif isinstance(selected_field, str):
            score += 1
    return score


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


def _anchor_relation_recovery_tools_for_candidate(
    *,
    user_text: str,
    record: TurnRecord,
    candidate_args: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]] | None:
    target_manager = candidate_args.get("manager")
    if not isinstance(target_manager, str) or not target_manager:
        return None
    value = _candidate_user_filter_value(
        candidate_args=candidate_args,
        user_text=user_text,
        record=record,
    )
    if value is None:
        return None
    return _anchor_relation_recovery_tools(
        user_text=user_text,
        record=record,
        target_manager=target_manager,
        value=value,
    )


def _anchor_relation_recovery_tools(
    *,
    user_text: str,
    record: TurnRecord,
    target_manager: str | None = None,
    value: str | None = None,
) -> list[tuple[str, dict[str, Any]]] | None:
    target_summary = _requested_target_manager_summary(user_text, record)
    if target_manager is None and target_summary is not None:
        requested_manager = target_summary.get("manager")
        if isinstance(requested_manager, str) and requested_manager:
            target_manager = requested_manager
    if not target_manager:
        return None

    value = value or _target_filter_value(user_text=user_text, record=record)
    if value is None:
        return None

    anchor = _anchor_relation_query_args(
        user_text=user_text,
        record=record,
        target_manager=target_manager,
        value=value,
    )
    if anchor is None:
        return None
    return [("query", anchor)]


def _anchor_relation_query_args(
    *,
    user_text: str,
    record: TurnRecord,
    target_manager: str,
    value: str,
) -> dict[str, Any] | None:
    normalized = user_text.casefold()
    candidates: list[tuple[int, int, str, str, str, dict[str, Any]]] = []
    for schema in _manager_schemas(record):
        manager = str(schema.get("manager", ""))
        if not manager or manager == target_manager:
            continue
        manager_tokens = _manager_name_tokens(manager)
        manager_score = sum(
            _text_contains_token(normalized, token) for token in manager_tokens
        )
        if not manager_score:
            continue
        relation_name = _requested_relation_name(schema, normalized)
        if relation_name is None:
            continue
        relation_target = _relation_target_manager(schema, relation_name)
        if relation_target != target_manager:
            continue
        filter_key = _anchor_scalar_filter_key(schema)
        if filter_key is None:
            continue
        if _has_successful_anchor_relation_query(
            record=record,
            manager=manager,
            value=value,
            relation_name=relation_name,
        ):
            continue
        relation_position = _token_position(normalized, relation_name)
        candidates.append(
            (
                manager_score,
                relation_position,
                manager,
                relation_name,
                filter_key,
                schema,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3], item[4]))
    _, _, manager, relation_name, filter_key, schema = candidates[0]
    return {
        "manager": manager,
        "filters": {filter_key: value},
        "fields": _relation_query_fields_for_query(
            schema=schema,
            relation_name=relation_name,
            query_args={"filters": {filter_key: value}},
        ),
    }


def _candidate_user_filter_value(
    *,
    candidate_args: dict[str, Any],
    user_text: str,
    record: TurnRecord,
) -> str | None:
    normalized = user_text.casefold()
    schema_tokens = _schema_tokens(record)
    for value in _as_dict(candidate_args.get("filters")).values():
        if not isinstance(value, str) or not value:
            continue
        if value.casefold() not in normalized:
            continue
        value_tokens = set(_tokenize_text(value))
        if value_tokens and value_tokens.issubset(schema_tokens):
            continue
        return value
    return None


def _relation_target_manager(
    schema: dict[str, Any],
    relation_name: str,
) -> str | None:
    for relation in schema.get("relations", []):
        relation_dict = _as_dict(relation)
        if relation_dict.get("name") != relation_name:
            continue
        target = relation_dict.get("target")
        return target if isinstance(target, str) and target else None
    return None


def _anchor_scalar_filter_key(schema: dict[str, Any]) -> str | None:
    filters = [
        str(item)
        for item in schema.get("filters", [])
        if isinstance(item, str) and _relation_selection_from_filter_key(item) is None
    ]
    for preferred in (
        "name",
        "name__icontains",
        "title",
        "title__icontains",
        "label",
        "label__icontains",
    ):
        if preferred in filters:
            return preferred
    return None


def _has_successful_anchor_relation_query(
    *,
    record: TurnRecord,
    manager: str,
    value: str,
    relation_name: str,
) -> bool:
    normalized_value = value.casefold()
    relation_selection = {relation_name: ["name"]}
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        args = _as_dict(call.get("args"))
        result_dict = _as_dict(result)
        if (
            call.get("name") != "query"
            or args.get("manager") != manager
            or "error" in result_dict
            or not isinstance(result_dict.get("data"), list)
        ):
            continue
        if not any(
            isinstance(filter_value, str)
            and filter_value.casefold() == normalized_value
            for filter_value in _as_dict(args.get("filters")).values()
        ):
            continue
        if _rows_cover_relation_selection(result_dict["data"], relation_selection):
            return True
    return False


def _target_manager_filter_recovery_tools(
    *,
    user_text: str,
    record: TurnRecord,
    available_tool_names: set[str],
) -> list[tuple[str, dict[str, Any]]] | None:
    target_summary = _requested_target_manager_summary(user_text, record)
    if target_summary is None:
        return None
    target_manager = str(target_summary.get("manager", ""))
    if not target_manager:
        return None
    if _has_successful_query_evidence_for_target(record, target_summary):
        return None

    value = _target_filter_value(user_text=user_text, record=record)
    if value is None:
        return None
    filter_key = _target_relation_filter_key(
        user_text=user_text,
        record=record,
        target_summary=target_summary,
    )
    if filter_key is None:
        return None
    if _has_successful_query_for_manager_with_filter_value(
        record=record,
        manager=target_manager,
        value=value,
    ):
        return None

    tools: list[tuple[str, dict[str, Any]]] = []
    source_manager = _source_manager_for_filter(record, target_manager, filter_key)
    if (
        source_manager is not None
        and "find_path" in available_tool_names
        and not _has_tool_call(record, "find_path")
    ):
        tools.append(
            (
                "find_path",
                {
                    "from_manager": target_manager,
                    "to_manager": source_manager,
                },
            )
        )
    tools.append(
        (
            "query",
            {
                "manager": target_manager,
                "filters": {filter_key: value},
                "fields": _target_query_fields(target_summary, filter_key),
            },
        )
    )
    return tools


def _target_manager_filter_recovery_tools_for_candidate(
    *,
    user_text: str,
    record: TurnRecord,
    candidate_args: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]] | None:
    target_summary = _requested_target_manager_summary(user_text, record)
    if target_summary is None:
        return None
    target_manager = str(target_summary.get("manager", ""))
    if not target_manager or candidate_args.get("manager") != target_manager:
        return None
    value = _target_filter_value(user_text=user_text, record=record)
    if value is None:
        return None
    candidate_filters = _as_dict(candidate_args.get("filters"))
    normalized_user_text = user_text.casefold()
    if any(
        isinstance(filter_value, str)
        and filter_value
        and filter_value.casefold() in normalized_user_text
        for filter_value in candidate_filters.values()
    ):
        return None
    if any(
        isinstance(filter_value, str)
        and filter_value
        and filter_value.casefold() == value.casefold()
        for filter_value in candidate_filters.values()
    ):
        return None
    filter_key = _target_relation_filter_key(
        user_text=user_text,
        record=record,
        target_summary=target_summary,
    )
    if filter_key is None:
        return None
    return [
        (
            "query",
            {
                "manager": target_manager,
                "filters": {filter_key: value},
                "fields": _target_query_fields(target_summary, filter_key),
            },
        )
    ]


def _target_schema_after_path_args(
    *,
    user_text: str,
    record: TurnRecord,
) -> dict[str, str] | None:
    normalized = user_text.casefold()
    candidates: list[tuple[int, int, str]] = []
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        if (
            call.get("name") != "find_path"
            or not isinstance(result, list)
            or not result
        ):
            continue
        args = _as_dict(call.get("args"))
        for manager_key in ("from_manager", "to_manager"):
            manager = args.get(manager_key)
            if not isinstance(manager, str) or not manager:
                continue
            if _has_schema_for_manager(record, manager):
                continue
            if _has_successful_query_for_manager(record, manager):
                continue
            manager_tokens = _manager_name_tokens(manager)
            score = sum(
                _text_contains_token(normalized, token) for token in manager_tokens
            )
            if not score:
                continue
            position = min(
                (_token_position(normalized, token) for token in manager_tokens),
                default=len(normalized),
            )
            candidates.append((score, position, manager))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    return {"manager": candidates[0][2]}


def _target_manager_list_recovery_tools(
    *,
    user_text: str,
    record: TurnRecord,
) -> list[tuple[str, dict[str, Any]]] | None:
    target_summary = _requested_target_manager_summary(user_text, record)
    if target_summary is None:
        return None
    target_manager = str(target_summary.get("manager", ""))
    if not target_manager or _has_successful_query_for_manager(record, target_manager):
        return None
    if _has_successful_query_evidence_for_target(record, target_summary):
        return None
    if _target_filter_value(user_text=user_text, record=record) is not None:
        return None
    if not _is_target_manager_list_question(user_text, target_manager):
        return None
    return [
        (
            "query",
            {
                "manager": target_manager,
                "fields": _target_list_query_fields(
                    user_text=user_text,
                    target_summary=target_summary,
                ),
            },
        )
    ]


def _discovered_manager_path_args(record: TurnRecord) -> dict[str, str] | None:
    if _has_tool_call(record, "find_path"):
        return None
    for call, result in reversed(
        list(zip(record.tool_calls, record.tool_results, strict=False))
    ):
        if call.get("name") != "query":
            continue
        result_dict = _as_dict(result)
        if "error" in result_dict or not isinstance(result_dict.get("data"), list):
            continue
        args = _as_dict(call.get("args"))
        target_manager = args.get("manager")
        if not isinstance(target_manager, str) or not target_manager:
            continue
        filters = _as_dict(args.get("filters"))
        for filter_key in filters:
            if not isinstance(filter_key, str) or not _filter_selection_has_relation(
                {filter_key: filters[filter_key]}
            ):
                continue
            source_manager = _source_manager_for_filter(
                record,
                target_manager,
                filter_key,
            )
            if source_manager is None:
                continue
            return {
                "from_manager": target_manager,
                "to_manager": source_manager,
            }
    return None


def _has_successful_query_evidence_for_target(
    record: TurnRecord,
    target_summary: dict[str, Any],
) -> bool:
    manager = str(target_summary.get("manager", ""))
    target_tokens = _manager_name_tokens(manager)
    if not target_tokens:
        return False
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        if call.get("name") != "query":
            continue
        result_dict = _as_dict(result)
        rows = result_dict.get("data")
        if "error" in result_dict or not isinstance(rows, list) or not rows:
            continue
        if _value_has_key_matching_tokens(rows, target_tokens):
            return True
    return False


def _value_has_key_matching_tokens(value: Any, tokens: set[str]) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and tokens.intersection(_tokenize_text(key)):
                return True
            if _value_has_key_matching_tokens(child, tokens):
                return True
        return False
    if isinstance(value, list):
        return any(_value_has_key_matching_tokens(item, tokens) for item in value)
    return False


def _requested_target_manager_summary(
    user_text: str,
    record: TurnRecord,
) -> dict[str, Any] | None:
    normalized = user_text.casefold()
    candidates: list[tuple[int, int, str, dict[str, Any]]] = []
    for summary in _discovered_manager_summaries(record):
        manager = str(summary.get("manager", ""))
        if not manager:
            continue
        manager_tokens = _manager_name_tokens(manager)
        score = sum(_text_contains_token(normalized, token) for token in manager_tokens)
        if score:
            position = min(
                (_token_position(normalized, token) for token in manager_tokens),
                default=len(normalized),
            )
            candidates.append((score, position, manager, summary))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    return candidates[0][3]


def _is_target_manager_list_question(user_text: str, target_manager: str) -> bool:
    normalized = user_text.casefold()
    target_tokens = _manager_name_tokens(target_manager)
    if not any(_text_contains_token(normalized, token) for token in target_tokens):
        return False
    return bool(
        re.search(
            r"\b(?:what|which|list|show|find|give|display)\b",
            normalized,
        )
    )


def _target_filter_value(
    *,
    user_text: str,
    record: TurnRecord,
) -> str | None:
    normalized = user_text.casefold()
    for call, result in reversed(
        list(zip(record.tool_calls, record.tool_results, strict=False))
    ):
        if call.get("name") != "query":
            continue
        args = _as_dict(call.get("args"))
        filters = _as_dict(args.get("filters"))
        for value in filters.values():
            if isinstance(value, str) and value and value.casefold() in normalized:
                return value
        result_dict = _as_dict(result)
        rows = result_dict.get("data")
        if not isinstance(rows, list):
            continue
        for row in rows:
            value = _first_user_mentioned_scalar(row, normalized)
            if value is not None:
                return value

    excluded_tokens = _schema_tokens(record)
    for token in _tokenize_text(user_text):
        if token in _TARGET_VALUE_STOPWORDS or token in excluded_tokens:
            continue
        if len(token) < 3:
            continue
        return token
    return None


def _target_relation_filter_key(
    *,
    user_text: str,
    record: TurnRecord,
    target_summary: dict[str, Any],
) -> str | None:
    filters = [
        str(item)
        for item in target_summary.get("filters", [])
        if isinstance(item, str)
        and _relation_selection_from_filter_key(item) is not None
    ]
    if not filters:
        return None
    user_tokens = set(_tokenize_text(user_text))
    source_tokens = _queried_manager_tokens(record)
    scored: list[tuple[int, str]] = []
    for filter_key in filters:
        filter_tokens = set(_filter_path_tokens(filter_key))
        score = len(filter_tokens.intersection(user_tokens))
        score += 2 * len(filter_tokens.intersection(source_tokens))
        if filter_key.endswith("__icontains"):
            score += 1
        if score > 0:
            scored.append((score, filter_key))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][1]


def _target_query_fields(
    target_summary: dict[str, Any],
    filter_key: str,
) -> list[Any]:
    scalar_fields = [
        str(field)
        for field in target_summary.get("fields", [])
        if isinstance(field, str) and field
    ]
    if not scalar_fields:
        scalar_fields = ["name"]
    relation_selection = _relation_selection_from_filter_key(filter_key)
    if relation_selection is None:
        return scalar_fields
    return [*scalar_fields, relation_selection]


def _target_list_query_fields(
    *,
    user_text: str,
    target_summary: dict[str, Any],
) -> list[Any]:
    scalar_fields = [
        str(field)
        for field in target_summary.get("fields", [])
        if isinstance(field, str) and field
    ]
    if not scalar_fields:
        scalar_fields = ["name"]
    normalized = user_text.casefold()
    relation_fields = [
        {relation_name: ["name"]}
        for relation in target_summary.get("relations", [])
        if (relation_name := str(_as_dict(relation).get("name", "")))
        and any(
            _text_contains_token(normalized, token)
            for token in _tokenize_text(relation_name)
        )
    ]
    return _dedupe_field_selection([*scalar_fields, *relation_fields])


def _relation_selection_from_filter_key(filter_key: str) -> dict[str, Any] | None:
    parts = filter_key.split("__")
    if parts and parts[-1] in _LOOKUP_SUFFIXES:
        parts = parts[:-1]
    if len(parts) < 2:
        return None
    scalar = parts[-1]
    relations = parts[:-1]
    nested: list[Any] = [scalar]
    for relation in reversed(relations):
        nested = ["name", {relation: nested}]
    selection = nested[1]
    return selection if isinstance(selection, dict) else None


def _source_manager_for_filter(
    record: TurnRecord,
    target_manager: str,
    filter_key: str,
) -> str | None:
    filter_tokens = _filter_path_tokens(filter_key)
    token_positions = {token: index for index, token in enumerate(filter_tokens)}
    candidates: list[tuple[int, str]] = []
    for summary in _discovered_manager_summaries(record):
        manager = str(summary.get("manager", ""))
        if not manager or manager == target_manager:
            continue
        positions = [
            token_positions[token]
            for token in _manager_name_tokens(manager)
            if token in token_positions
        ]
        if positions:
            candidates.append((max(positions), manager))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][1]


def _has_successful_query_for_manager(record: TurnRecord, manager: str) -> bool:
    return _last_successful_query_for_manager(record, manager) != (None, None)


def _has_successful_query_for_manager_with_filter_value(
    *,
    record: TurnRecord,
    manager: str,
    value: str,
) -> bool:
    normalized_value = value.casefold()
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        args = _as_dict(call.get("args"))
        result_dict = _as_dict(result)
        if (
            call.get("name") != "query"
            or args.get("manager") != manager
            or "error" in result_dict
            or not isinstance(result_dict.get("data"), list)
        ):
            continue
        for filter_value in _as_dict(args.get("filters")).values():
            if (
                isinstance(filter_value, str)
                and filter_value
                and filter_value.casefold() == normalized_value
            ):
                return True
    return False


def _discovered_manager_summaries(record: TurnRecord) -> list[dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        if call.get("name") == "search_managers" and isinstance(result, list):
            for item in result:
                summary = _as_dict(item)
                manager = summary.get("manager")
                if isinstance(manager, str) and manager:
                    summaries.setdefault(manager, summary)
        elif call.get("name") == "get_manager_schema":
            summary = _as_dict(result)
            manager = summary.get("manager")
            if isinstance(manager, str) and manager:
                summaries.setdefault(manager, summary)
    return list(summaries.values())


def _tokenize_text(value: str) -> list[str]:
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", value)
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", spaced).casefold()
    return [_singularize_token(token) for token in normalized.split() if token]


def _singularize_token(token: str) -> str:
    if len(token) > 3 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _manager_name_tokens(manager_name: str) -> set[str]:
    base = manager_name.removesuffix("Manager")
    return set(_tokenize_text(base))


def _filter_path_tokens(filter_key: str) -> list[str]:
    return [
        token for token in _tokenize_text(filter_key) if token not in _LOOKUP_SUFFIXES
    ]


def _text_contains_token(normalized_text: str, token: str) -> bool:
    plural = f"{token}s"
    return bool(
        re.search(rf"\b(?:{re.escape(token)}|{re.escape(plural)})\b", normalized_text)
    )


def _token_position(normalized_text: str, token: str) -> int:
    plural = f"{token}s"
    match = re.search(
        rf"\b(?:{re.escape(token)}|{re.escape(plural)})\b", normalized_text
    )
    return match.start() if match else len(normalized_text)


def _schema_tokens(record: TurnRecord) -> set[str]:
    tokens: set[str] = set()
    for summary in _discovered_manager_summaries(record):
        manager = summary.get("manager")
        if isinstance(manager, str):
            tokens.update(_manager_name_tokens(manager))
        for relation in summary.get("relations", []):
            relation_name = _as_dict(relation).get("name")
            if isinstance(relation_name, str):
                tokens.update(_tokenize_text(relation_name))
        for filter_key in summary.get("filters", []):
            if isinstance(filter_key, str):
                tokens.update(_filter_path_tokens(filter_key))
    return tokens


def _queried_manager_tokens(record: TurnRecord) -> set[str]:
    tokens: set[str] = set()
    for call, result in zip(record.tool_calls, record.tool_results, strict=False):
        args = _as_dict(call.get("args"))
        result_dict = _as_dict(result)
        manager = args.get("manager")
        if (
            call.get("name") == "query"
            and isinstance(manager, str)
            and "error" not in result_dict
            and isinstance(result_dict.get("data"), list)
        ):
            tokens.update(_manager_name_tokens(manager))
    return tokens


def _first_user_mentioned_scalar(value: Any, normalized_user_text: str) -> str | None:
    if isinstance(value, dict):
        for child in value.values():
            if found := _first_user_mentioned_scalar(child, normalized_user_text):
                return found
        return None
    if isinstance(value, list):
        for child in value:
            if found := _first_user_mentioned_scalar(child, normalized_user_text):
                return found
        return None
    if isinstance(value, str) and value and value.casefold() in normalized_user_text:
        return value
    return None


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
    for key in filters:
        if not isinstance(key, str) or "__" not in key:
            continue
        parts = key.split("__")
        if len(parts) > 2:
            return True
        if parts[-1] not in _LOOKUP_SUFFIXES:
            return True
    return False


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
        query_args = _as_dict(query_call.get("args")).copy()
        relation_fields = _relation_query_fields_for_query(
            schema=schema,
            relation_name=relation,
            query_args=query_args,
        )
        relation_selection = next(
            (field for field in relation_fields if isinstance(field, dict)),
            None,
        )
        if relation_selection is not None and _rows_cover_relation_selection(
            rows,
            relation_selection,
        ):
            continue
        query_args["fields"] = relation_fields
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
    return _relation_query_fields_for_query(
        schema=schema,
        relation_name=relation_name,
        query_args={},
    )


def _relation_query_fields_for_query(
    *,
    schema: dict[str, Any],
    relation_name: str,
    query_args: dict[str, Any],
) -> list[Any]:
    scalar_fields = [
        str(field)
        for field in schema.get("fields", [])
        if isinstance(field, str) and field
    ]
    if not scalar_fields:
        scalar_fields = ["name"]
    if (
        filter_selection := _relation_selection_from_query_filters(
            query_args.get("filters"),
            relation_name,
        )
    ) is not None:
        return [*scalar_fields, filter_selection]
    return [*scalar_fields, {relation_name: ["name"]}]


def _relation_selection_from_query_filters(
    filters: Any,
    relation_name: str,
) -> dict[str, Any] | None:
    if not isinstance(filters, dict):
        return None
    for key in filters:
        if not isinstance(key, str):
            continue
        selection = _relation_selection_from_filter_key(key)
        if isinstance(selection, dict) and relation_name in selection:
            return selection
    return None


def _rows_cover_relation_selection(
    rows: list[Any],
    selection: dict[str, Any],
) -> bool:
    return any(_value_covers_selection(row, selection) for row in rows)


def _value_covers_selection(value: Any, selection: Any) -> bool:
    if isinstance(selection, str):
        return isinstance(value, dict) and selection in value
    if isinstance(selection, dict):
        if not isinstance(value, dict):
            return False
        return all(
            key in value and _value_covers_selection(value[key], child_selection)
            for key, child_selection in selection.items()
            if isinstance(key, str)
        )
    if isinstance(selection, list):
        if isinstance(value, list):
            return any(_value_covers_selection(item, selection) for item in value)
        if isinstance(value, dict):
            return all(_value_covers_selection(value, item) for item in selection)
        return False
    return False


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
    result.recovery_events = [
        event for record in records for event in record.recovery_events
    ]

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
            "recovery_events": result.recovery_events,
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
        result.recovery_events = [
            event for record in records for event in record.recovery_events
        ]
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
