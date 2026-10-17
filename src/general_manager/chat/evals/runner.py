"""Eval runner: loads datasets, executes conversations, scores via judges."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO
from typing import Any
from typing import NoReturn
from typing import Protocol

import yaml

from general_manager.chat.evals.diagnostics import (
    classify_result,
    planned_eval_diagnostics,
    sanitize_planned_trace,
    summarize_diagnostics,
)
from general_manager.chat.evals.fingerprints import EVALUATION_CONTRACT, stable_hash
from general_manager.chat.evals.outcomes import terminal_failure_categories
from general_manager.chat.evals.execution import EvalExecution, bind_execution
from general_manager.chat.schema_index import planner_catalog_summary
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
from general_manager.chat.evals.judges.result_set import (
    ResultSetScore,
    judge_result_set,
)
from general_manager.chat.evals.judges.tool_sequence import (
    ToolSequenceScore,
    judge_tool_sequence,
)
from general_manager.chat.providers._shared import (
    closing_async_resource,
)
from general_manager.chat.providers.base import (
    Message,
    ToolCallEvent,
)
from general_manager.chat.planned.schema_projection import with_historical_schema
from general_manager.chat.planned.catalog import ManagerCatalog, ManagerCatalogEntry
from general_manager.chat.planned.config import (
    PlannedChatSettings,
    ProviderProfile,
    get_planned_chat_settings,
)
from general_manager.chat.planned.resolver import ManagerResolver
from general_manager.chat.planned.planner import InvalidPlanError
from general_manager.chat.planned.scheduler import (
    SchedulerCallbacks,
    iter_planned_read_events,
    prepare_planned_turn,
)
from types import MappingProxyType
from general_manager.chat.system_prompt import build_system_prompt
from general_manager.chat.tools import get_tool_definitions
from general_manager.chat.evals.traces import EvalTraceWriter

DATASETS_DIR = Path(__file__).parent / "datasets"
DETERMINISTIC_DATASETS = frozenset({"planned_orchestration"})

DEFAULT_MAX_PROVIDER_REQUESTS = 10


class _PassScore(Protocol):
    @property
    def passed(self) -> bool: ...


# Historical report classification only; no recovery actions execute here.
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


class PlannedEvalConfigurationError(ValueError):
    """Raised when deterministic planned-eval role pinning is invalid."""


@dataclass(frozen=True)
class PlannedEvalRoleOverride:
    """One role-pinned provider used only by the deterministic eval adapter."""

    provider_path: str
    provider_config: dict[str, Any]
    trust_group: str

    def with_trust_group(self, trust_group: str) -> "PlannedEvalRoleOverride":
        """Return a test override with a different trust group."""
        return PlannedEvalRoleOverride(
            self.provider_path, dict(self.provider_config), trust_group
        )


@dataclass
class EvalResult:
    case: EvalCase
    contract_score: ProductContractScore | None = None
    tool_score: ToolSequenceScore | None = None
    result_score: ResultAccuracyScore | None = None
    answer_score: AnswerQualityScore | None = None
    error: str | None = None
    recovery_events: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    result_set_score: ResultSetScore | None = None
    turn_results: list[EvalResult] = field(default_factory=list)
    requests: int = 0
    fingerprint: str = ""
    trace: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    failure_category: str | None = None

    @property
    def passed(self) -> bool:
        if self.error is not None or any(not turn.passed for turn in self.turn_results):
            return False
        if self.result_set_score is not None and not self.result_set_score.passed:
            return False
        if self.contract_score is not None and not self.contract_score.passed:
            return False
        for score in (self.result_score, self.answer_score):
            if score is not None and not score.passed:
                return False
        if self.contract_score is None and self.tool_score is not None:
            return self.tool_score.passed
        return True

    @property
    def status(self) -> str:
        """Separate ungradable execution from a completed quality failure."""
        if (
            self.error is not None
            and self.error != "missing_turn_answer"
            and self.failure_category != "model_task_failure"
        ) or any(turn.status == "unscored" for turn in self.turn_results):
            return "unscored"
        return "pass" if self.passed else "fail"


@dataclass
class TurnRecord:
    """Records collected during one conversation turn."""

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    answer_chunks: list[str] = field(default_factory=list)
    error: str | None = None
    requests: int = 0
    recovery_events: list[str] = field(default_factory=list)
    failure_category: str | None = None

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


def execution_dataset_names(dataset_names: list[str] | None = None) -> list[str]:
    """Return provider-backed datasets; deterministic fixtures require explicit pins."""
    if dataset_names is None:
        return [name for name in list_datasets() if name not in DETERMINISTIC_DATASETS]

    unsupported = sorted(set(dataset_names) & DETERMINISTIC_DATASETS)
    if unsupported:
        names = ", ".join(unsupported)
        msg = f"Dataset(s) require explicit deterministic role overrides: {names}."
        raise ValueError(msg)
    return dataset_names


# ---------------------------------------------------------------------------


_MANAGER_DISCOVERY_MARKERS = (
    "access to",
    "data model",
    "explore",
    "what data",
    "what kinds of managers",
    "managers can i ask",
)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _expectation_error(case: EvalCase) -> str | None:
    """Reject malformed strict contracts before spending provider requests."""
    expectations = case.expectations
    groups = [expectations]
    if "turns" in expectations:
        turns = expectations["turns"]
        count = sum(bool(turn.get("user")) for turn in case.conversation)
        if (
            not isinstance(turns, list)
            or not turns
            or len(turns) != count
            or any(
                not isinstance(turn, dict) or not turn or "turns" in turn
                for turn in turns
            )
        ):
            return "invalid_or_missing_turn_expectations"
        groups.extend(turns)
    for group in groups:
        if "result_set" in group:
            spec = group["result_set"]
            if (
                not isinstance(spec, dict)
                or judge_result_set(spec, [], []).reason
                == "invalid_result_set_expectation"
            ):
                return "invalid_result_set_expectation"
    return None


def _score_case(
    case: EvalCase, records: list[TurnRecord], *, strict_answer: bool = False
) -> EvalResult:
    """Score a completed eval case against its expectations."""
    expectations = case.expectations
    result = EvalResult(case=case)
    if expectation_error := _expectation_error(case):
        result.error = expectation_error
        return result
    result.recovery_events = [
        event for record in records for event in record.recovery_events
    ]

    result.requests = sum(record.requests for record in records)
    failures = [record for record in records if record.error is not None]
    if failures:
        # A technical failure anywhere keeps a multi-turn run unscored.
        failure = next(
            (
                record
                for record in failures
                if record.failure_category != "model_task_failure"
            ),
            failures[0],
        )
        result.error = failure.error
        result.failure_category = failure.failure_category
    turn_expectations = expectations.get("turns")
    if turn_expectations is not None:
        user_turns = [turn for turn in case.conversation if turn.get("user")]
        if len(records) != len(user_turns):
            result.error = result.error or "invalid_or_missing_turn_expectations"
        for index, (turn, record) in enumerate(
            zip(turn_expectations, records, strict=False)
        ):
            turn_case = EvalCase(
                name=f"{case.name}/turn-{index + 1}",
                description=case.description,
                conversation=[user_turns[index]],
                expectations=turn,
            )
            scored = _score_case(turn_case, [record], strict_answer=True)
            if not record.answer.strip():
                scored.error = scored.error or "missing_turn_answer"
            result.turn_results.append(scored)

    all_tool_calls, all_tool_results, full_answer = _aggregate_records(records)
    exact_result_set = expectations.get("result_set")
    if exact_result_set is not None:
        if not isinstance(exact_result_set, dict) or len(records) != 1:
            result.result_set_score = ResultSetScore(
                False, "invalid_result_set_expectation"
            )
        else:
            result.result_set_score = judge_result_set(
                exact_result_set, all_tool_calls, all_tool_results
            )

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
            answer_contains, answer_excludes, full_answer, require_all=strict_answer
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
        if rec.answer:
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
            "status": result.status,
            "failure_category": result.failure_category,
            "evaluation_contract": EVALUATION_CONTRACT,
            "requests": result.requests,
            "result_set": (
                None
                if result.result_set_score is None
                else {
                    "passed": result.result_set_score.passed,
                    "reason": result.result_set_score.reason,
                }
            ),
            "turns": [
                {
                    "turn": index + 1,
                    "tool_calls": record.tool_calls,
                    "tool_results": record.tool_results,
                    "answer": record.answer,
                    "error": record.error,
                    "requests": record.requests,
                    "passed": (
                        result.turn_results[index].passed
                        if index < len(result.turn_results)
                        else None
                    ),
                    "result_set_reason": (
                        score.reason
                        if index < len(result.turn_results)
                        and (score := result.turn_results[index].result_set_score)
                        is not None
                        else None
                    ),
                }
                for index, record in enumerate(records)
            ],
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
# Production scheduler evaluation adapter
# ---------------------------------------------------------------------------


_PLANNED_EVAL_ROLES = (
    "planner",
    "executor",
    "synthesizer",
    "fallback",
)


def _planned_eval_settings(
    role_overrides: dict[str, PlannedEvalRoleOverride] | None,
    planned: dict[str, Any],
) -> PlannedChatSettings:
    """Build isolated planned settings from explicit evaluation-only role pins."""
    if role_overrides is None:
        _planned_eval_error("planned evals require role overrides.")
    missing = [role for role in _PLANNED_EVAL_ROLES if role not in role_overrides]
    if missing:
        _planned_eval_error("missing role overrides: " + ", ".join(missing))
    unexpected = sorted(set(role_overrides) - set(_PLANNED_EVAL_ROLES))
    if unexpected:
        _planned_eval_error("unknown role overrides: " + ", ".join(unexpected))
    trust_groups = {role_overrides[role].trust_group for role in _PLANNED_EVAL_ROLES}
    if len(trust_groups) != 1:
        _planned_eval_error("planned eval role overrides must use one trust_group.")
    profiles = {
        role: ProviderProfile(
            name=role,
            provider_path=role_overrides[role].provider_path,
            provider_config=MappingProxyType(
                dict(role_overrides[role].provider_config)
            ),
            trust_group=role_overrides[role].trust_group,
        )
        for role in _PLANNED_EVAL_ROLES
    }
    return PlannedChatSettings(
        enabled=True,
        profiles=MappingProxyType(profiles),
        roles=MappingProxyType({role: role for role in _PLANNED_EVAL_ROLES}),
        catalog_source=None,
        max_concurrent_tasks=1,
        evidence_timeout_seconds=float(planned.get("evidence_timeout_seconds", 90)),
    )


def _planned_eval_error(detail: str) -> NoReturn:
    raise PlannedEvalConfigurationError(detail)


def _validate_execution_mode(strategy: str, recover_missing_tools: bool) -> None:
    """Reject removed execution modes at both case and suite entry points."""
    if strategy != "planned":
        _planned_eval_error(
            "Legacy eval execution was removed; strategy must be 'planned'."
        )
    if recover_missing_tools:
        _planned_eval_error(
            "Evaluator-injected recovery was removed; use native orchestration."
        )


def _planned_tool_callback(
    case: EvalCase, executions: list[tuple[str, Any]]
) -> Callable[[str, Any, Any], Any]:
    """Only explicit deterministic fixtures supply fixed tool outcomes."""
    planned = case.expectations.get("planned", {})
    configured = planned.get("tool_results", {}) if isinstance(planned, dict) else {}

    def execute(name: str, args: Any, _context: Any) -> Any:
        executions.append((name, args))
        manager = args.get("manager") if isinstance(args, dict) else None
        if isinstance(configured, dict):
            result = configured.get(str(manager), configured.get(name))
            if result is not None:
                return result
        return {"status": "success", "data": []}

    return execute


async def _run_planned_case(
    case: EvalCase,
    *,
    settings: PlannedChatSettings,
    execution: EvalExecution,
    deterministic: bool,
    stream: IO[str] | None,
    trace_writer: EvalTraceWriter | None,
    run_metadata: dict[str, Any] | None,
) -> EvalResult:
    """Observe the real production scheduler; only explicit fixtures replace IO."""
    planned = case.expectations.get("planned", {})
    resolver = _planned_eval_resolver() if deterministic else None
    callbacks = SchedulerCallbacks()
    history = [Message(role="system", content=build_system_prompt())]
    records: list[TurnRecord] = []
    events: list[dict[str, Any]] = []
    execution_results = []
    tool_executions: list[tuple[str, Any]] = []
    if deterministic:
        callbacks = SchedulerCallbacks(
            execute_tool=_planned_tool_callback(case, tool_executions),
            enforce_rate_limit=None,
        )
    callbacks = replace(
        callbacks,
        enforce_rate_limit=execution.accounting_observer(callbacks.enforce_rate_limit),
    )
    _stream_line(stream, f"=== {case.name} ===")
    for turn in case.conversation:
        user_text = turn.get("user", "")
        if not user_text:
            continue
        execution.begin_turn()
        record = TurnRecord()
        records.append(record)
        terminals: list[dict[str, Any]] = []
        pending_calls: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
        history.append(Message(role="user", content=user_text))
        turn_history = list(history)
        _stream_line(stream, f"user: {user_text}")
        try:
            catalog = {} if deterministic else planner_catalog_summary(settings)
            prepared = await prepare_planned_turn(
                user_text,
                turn_history,
                settings,
                catalog,
                resolver=resolver,
                callbacks=callbacks,
            )
            subtree_round_limit = (
                planned.get("subtree_round_limit") if deterministic else None
            )
            if isinstance(subtree_round_limit, int) and subtree_round_limit > 0:
                prepared.budget.subtree_limit = subtree_round_limit
            if prepared.mutation_plan is not None:
                record.error = "read_only_eval_mutation_plan"
                record.failure_category = "harness_failure"
                break
            async with closing_async_resource(
                iter_planned_read_events(
                    prepared,
                    scope={},
                    conversation=None,
                    messages=turn_history,
                    callbacks=callbacks,
                )
            ) as event_stream:
                async for event in event_stream:
                    events.append(event)
                    if terminals:
                        record.error = "planned_events_after_completion"
                        record.failure_category = "harness_failure"
                    if event["type"] in {"error", "done"}:
                        terminals.append(event)
                    if event["type"] == "tool_call":
                        record.tool_calls.append(
                            {"name": event["name"], "args": dict(event["args"])}
                        )
                        key = (event.get("task_id"), event.get("id"), event["name"])
                        pending_calls[key] = event
                        _stream_line(
                            stream,
                            f"tool_call {event['name']}: {_to_json(event['args'])}",
                        )
                    elif event["type"] == "tool_result":
                        record.tool_results.append(event["result"])
                        key = (event.get("task_id"), event.get("id"), event["name"])
                        call = pending_calls.pop(key)
                        call_id = f"eval:{len(records)}:{len(record.tool_results)}"
                        history.append(
                            Message(
                                role="assistant",
                                content="",
                                tool_calls=(
                                    ToolCallEvent(
                                        call_id, call["name"], dict(call["args"])
                                    ),
                                ),
                            )
                        )
                        history.append(
                            with_historical_schema(
                                Message(
                                    role="tool",
                                    content=json.dumps(event["result"]),
                                    tool_call_id=call_id,
                                    tool_name=call["name"],
                                    tool_result=event["result"],
                                ),
                                tool_name=call["name"],
                                tool_result=event["result"],
                                binding={
                                    "case": case.name,
                                    "turn": len(records),
                                    "tool_call_id": call_id,
                                    "tool_args": call["args"],
                                },
                            )
                        )
                    elif event["type"] == "text_chunk":
                        record.answer_chunks.append(str(event["content"]))
                        if stream is not None:
                            stream.write(str(event["content"]))
                            stream.flush()
                    elif event["type"] == "error" and record.error is None:
                        code = str(event.get("code", "planned_error"))
                        record.error = code
                        record.failure_category = terminal_failure_categories(
                            event
                        ).get(code, "harness_failure")
            if not terminals:
                record.error = "planned_stream_without_completion"
                record.failure_category = "harness_failure"
            elif len(terminals) == 1 and record.failure_category != "harness_failure":
                terminal_event = terminals[0]
                origins = (
                    prepared.result.reason_origins
                    if prepared.result is not None
                    else {}
                )
                terminal_origin = None
                if (
                    prepared.result is not None
                    and terminal_event.get("type") == "error"
                ):
                    first_task = next(iter(prepared.result.reasons), None)
                    if first_task is not None and prepared.result.reasons[
                        first_task
                    ] == terminal_event.get("code"):
                        terminal_origin = origins.get(first_task)
                categories = terminal_failure_categories(
                    terminal_event,
                    terminal_origin=terminal_origin,
                    reason_origins=origins,
                )
                if categories:
                    reason, category = next(
                        (
                            (reason, category)
                            for reason, category in categories.items()
                            if category != "model_task_failure"
                        ),
                        next(iter(categories.items())),
                    )
                    record.error = reason
                    record.failure_category = category
            if prepared.result is not None:
                execution_results.append(prepared.result)
            if record.answer:
                history.append(Message(role="assistant", content=record.answer))
        except Exception as exc:  # noqa: BLE001 - same boundary as production transports
            record.error = str(exc) or type(exc).__name__
            record.failure_category = "harness_failure"
            if isinstance(exc, InvalidPlanError) and execution.provider_error is None:
                record.failure_category = terminal_failure_categories(
                    {"type": "error", "code": exc.code}
                ).get(exc.code, "model_task_failure")
        finally:
            record.requests = execution.turn_requests
            if execution.budget_exhausted:
                record.error = "turn_request_budget_exhausted"
                record.failure_category = "harness_failure"
            elif execution.provider_error is not None and record.error is not None:
                record.error = (
                    str(execution.provider_error)
                    or type(execution.provider_error).__name__
                )
                record.failure_category = "provider_failure"
        if record.error is not None:
            break
    result = _score_case(case, records)
    result.tool_calls, result.tool_results, result.answer = _aggregate_records(records)
    result.trace = sanitize_planned_trace(events)
    result.diagnostics = planned_eval_diagnostics(
        events, {role: settings.roles[role] for role in _PLANNED_EVAL_ROLES}
    )
    if execution_results:
        completed_execution = execution_results[-1]
        terminal = next(
            (
                event.get("code")
                for event in reversed(events)
                if event["type"] == "error"
            ),
            None,
        )
        result.diagnostics["orchestration"] = {
            "coverage": {
                "resolved": completed_execution.coverage.resolved,
                "total": completed_execution.coverage.total,
            },
            "unresolved": [
                {"task_id": task_id, "reason": reason}
                for task_id, reason in completed_execution.coverage.unresolved
            ],
            "terminal_reason": terminal
            or (
                completed_execution.coverage.unresolved[0][1]
                if len(completed_execution.coverage.unresolved) == 1
                else None
            ),
        }
    result.diagnostics["tool_executions"] = (
        len(tool_executions) if deterministic else len(result.tool_results)
    )
    result.usage = (
        {
            "input_tokens": execution.usage.input_tokens,
            "output_tokens": execution.usage.output_tokens,
        }
        if execution.usage_observations
        else {}
    )
    result.fingerprint = stable_hash(
        {"case": case.name, "strategy": "planned", "trace": result.trace}
    )
    _write_trace(
        trace_writer,
        case=case,
        records=records,
        result=result,
        run_metadata=run_metadata,
    )
    return result


def _planned_eval_resolver() -> ManagerResolver:
    """Build a fixed two-manager catalog that exercises alias-based routing."""
    schema_index = {
        "MaterialManager": {"description": "materials", "fields": []},
        "PartManager": {"description": "parts", "fields": []},
    }
    catalog = ManagerCatalog(
        entries=MappingProxyType(
            {
                "MaterialManager": ManagerCatalogEntry(
                    domain="manufacturing",
                    aliases=("materials",),
                    use_when="find materials",
                    distinguish_from=(),
                ),
                "PartManager": ManagerCatalogEntry(
                    domain="manufacturing",
                    aliases=("parts",),
                    use_when="find parts",
                    distinguish_from=(),
                ),
            }
        ),
        fingerprint="planned-eval-catalog",
    )
    return ManagerResolver(schema_index, catalog)


# ---------------------------------------------------------------------------
# Suite execution
# ---------------------------------------------------------------------------


async def run_case(
    provider: Any,
    case: EvalCase,
    tool_defs: list[dict[str, Any]] | None = None,
    stream: IO[str] | None = None,
    trace_writer: EvalTraceWriter | None = None,
    run_metadata: dict[str, Any] | None = None,
    recover_missing_tools: bool = False,
    strategy: str = "planned",
    role_overrides: dict[str, PlannedEvalRoleOverride] | None = None,
    max_tool_iterations: int | None = None,
    *,
    max_provider_requests: int | None = None,
) -> EvalResult:
    """Run reads through production orchestration with an explicit request ceiling.

    ``max_tool_iterations`` remains a request-cap alias, counting every phase.
    Legacy strategy and evaluator-injected recovery are no longer executable.
    Tool definitions are scheduler-owned; omit ``tool_defs``. An empty list or
    the exact canonical legacy list remains a compatibility sentinel only.
    """
    _validate_execution_mode(strategy, recover_missing_tools)
    if max_tool_iterations is not None and max_provider_requests is not None:
        _planned_eval_error(
            "Specify only max_provider_requests, not both request-cap names."
        )
    limit = (
        max_provider_requests
        if max_provider_requests is not None
        else max_tool_iterations
    )
    limit = DEFAULT_MAX_PROVIDER_REQUESTS if limit is None else limit
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 64:
        _planned_eval_error("max_provider_requests must be between 1 and 64")
    if expectation_error := _expectation_error(case):
        return EvalResult(case=case, error=expectation_error)
    if tool_defs not in (None, [], get_tool_definitions()):
        _planned_eval_error(
            "Custom eval tools were removed; omit tool_defs to use native chat tools."
        )
    planned = case.expectations.get("planned", {})
    if not isinstance(planned, dict):
        _planned_eval_error("planned eval expectations must be a mapping.")
    deterministic = role_overrides is not None
    if deterministic:
        settings = _planned_eval_settings(role_overrides, planned)
    else:
        if provider is None:
            _planned_eval_error(
                "A provider instance or explicit role overrides are required."
            )
        settings = get_planned_chat_settings()
    with bind_execution(None if deterministic else provider, settings, limit) as (
        execution,
        bound,
    ):
        return await _run_planned_case(
            case,
            settings=bound,
            execution=execution,
            deterministic=deterministic,
            stream=stream,
            trace_writer=trace_writer,
            run_metadata=run_metadata,
        )


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
    _validate_execution_mode("planned", recover_missing_tools)
    dataset_names = execution_dataset_names(dataset_names)

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
    scored_results = [
        item for result in results for item in [result, *result.turn_results]
    ]
    contract_pass = sum(
        1
        for r in scored_results
        if r.contract_score is not None and r.contract_score.passed
    )
    contract_total = sum(1 for r in scored_results if r.contract_score is not None)
    tool_pass = sum(
        1 for r in scored_results if r.tool_score is not None and r.tool_score.passed
    )
    tool_total = sum(1 for r in scored_results if r.tool_score is not None)
    result_pass = sum(
        1
        for r in scored_results
        if r.result_score is not None and r.result_score.passed
    )
    result_total = sum(1 for r in scored_results if r.result_score is not None)
    answer_pass = sum(
        1
        for r in scored_results
        if r.answer_score is not None and r.answer_score.passed
    )
    answer_total = sum(1 for r in scored_results if r.answer_score is not None)
    exact_scores = [
        item.result_set_score
        for item in scored_results
        if item.result_set_score is not None
    ]
    sense_pass = sum(
        1
        for r in scored_results
        if r.contract_score is not None and r.contract_score.answer_sense.passed
    )
    sense_total = sum(1 for r in scored_results if r.contract_score is not None)

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
    if exact_scores:
        exact_pass = sum(score.passed for score in exact_scores)
        lines.append(
            f"{'Exact result sets':<20} {exact_pass:>6} {len(exact_scores):>6} {_pct(exact_pass, len(exact_scores)):>8}"
        )
    lines.append("-" * 42)
    lines.append(f"{'Overall':<20} {passed:>6} {total:>6} {_pct(passed, total):>8}")
    lines.append(
        f"Outcomes: {passed} PASS, {sum(r.status == 'fail' for r in results)} FAIL, "
        f"{sum(r.status == 'unscored' for r in results)} UNSCORED ({EVALUATION_CONTRACT})"
    )

    if verbose:
        failures = [r for r in results if not r.passed]
        if failures:
            lines.append("")
            lines.append("Failures:")
            for r in failures:
                lines.append(f"  {r.case.name}: {r.case.description}")
                if r.error:
                    lines.append(f"    error: {r.error}")
                if r.result_set_score and not r.result_set_score.passed:
                    lines.append(f"    result set: {r.result_set_score.reason}")
                for index, turn in enumerate(r.turn_results, start=1):
                    if not turn.passed:
                        lines.append(f"    turn {index}: failed")
                        if turn.error:
                            lines.append(f"      error: {turn.error}")
                        if turn.result_set_score and not turn.result_set_score.passed:
                            lines.append(
                                f"      result set: {turn.result_set_score.reason}"
                            )
                        if turn.answer_score and not turn.answer_score.passed:
                            lines.append(
                                f"      answer: missing={turn.answer_score.missing}; "
                                f"unexpected={turn.answer_score.unexpected}"
                            )
                        if turn.contract_score and not turn.contract_score.passed:
                            for violation in turn.contract_score.violations:
                                lines.append(f"      contract: {violation}")
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

    score_extractors: list[
        tuple[str, Callable[[EvalResult], _PassScore | None] | None]
    ] = [
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
    ]
    for dimension, extractor in score_extractors:
        row = f"{dimension:<20}"
        for p in providers:
            results = results_by_provider[p]
            if extractor is None:
                n_pass = sum(1 for r in results if r.passed)
                n_total = len(results)
            else:
                scored = [score for r in results if (score := extractor(r)) is not None]
                n_pass = sum(1 for s in scored if s.passed)
                n_total = len(scored)
            row += f" {_pct(n_pass, n_total):>14}"
        lines.append(row)

    return "\n".join(lines)


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{n / total:.0%}"
