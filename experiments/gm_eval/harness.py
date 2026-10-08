"""Thin experiment adapter over the unchanged production planned chat paths."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict, deque
from collections.abc import Coroutine
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import monotonic
from types import SimpleNamespace
from typing import Any, Mapping, cast
from uuid import uuid4

from asgiref.sync import sync_to_async

from .catalog import REFERENCE_VERSION
from .profiles import (
    EvaluationProfile,
    ExperimentProvider,
    ProviderFactory,
    bind_provider_factory,
)
from .trace import TraceRecorder, fingerprint, sanitize, source_hashes
from .scoring import FAILURE_HIERARCHY
from .history_parity import history_messages, provider_messages, schema_history_sources


FIXED_CLOCK = "2026-10-03T12:00:00Z"
CAPABILITY_GAPS = (
    {
        "code": "clarification_without_evidence",
        "detail": "Production only accepts read/mutation plans; synthesis requires fresh resolved evidence. After a schema/identity read, structured open requirements can render a claim-free clarification without factual evidence IDs. An evidence-free planner intent remains unsupported.",
    },
    {
        "code": "prior_evidence_reuse",
        "detail": "Visible dialogue reaches all phases as context, but prior assistant claims cannot replace fresh eligible evidence.",
    },
    {
        "code": "bounded_history",
        "detail": "Production context limits apply with summarization disabled; older constraints can leave the planner window.",
    },
    {
        "code": "query_ordering",
        "detail": "Native ordering arguments can be selected from the real schema; callers must ensure stable ordering across pages.",
    },
    {
        "code": "siwc_role_reasoning_replay",
        "detail": "Production constructs a provider for each role round. The SIWC bridge preserves that boundary; encrypted reasoning from a different role or earlier round is not replayed. Durable neutral conversation history still reaches the planner.",
    },
    {
        "code": "synthesizer_context_boundary",
        "detail": "Configured application instructions are captured per turn in all phases. Visible dialogue is context only; earlier answer claims are not promoted to synthesis evidence.",
    },
)


def _history(
    trace: TraceRecorder,
    turn_index: int,
    sources: list[dict[str, Any]],
) -> list[dict[str, str]]:
    for call in trace.calls:
        if call["turn"] != turn_index:
            continue
        for message in call["messages"]:
            text = message["content"]
            if text.startswith("REFERENCE_DATA="):
                reference = json.loads(text.split("=", 1)[1])
                if "conversation_context" in reference:
                    from general_manager.chat.planned.contract import PLAN_INSTRUCTION

                    systems = [
                        {"role": "system", "content": row["content"]}
                        for row in call["messages"]
                        if row["role"] == "system"
                        and row["content"] != PLAN_INSTRUCTION
                    ]
                    return [
                        *systems,
                        *cast(
                            list[dict[str, str]],
                            history_messages(
                                reference["conversation_context"], sources
                            ),
                        ),
                    ]
    return []


def _persisted(conversation: Any) -> list[dict[str, Any]]:
    from general_manager.chat.models import get_conversation_messages

    return [
        {
            "role": item.role,
            "content": item.content,
            "tool_name": item.tool_name,
            "tool_args": item.tool_args,
            "tool_result": item.tool_result,
        }
        for item in get_conversation_messages(conversation)
    ]


def _new_conversation(scope: dict[str, Any], session_key: str) -> Any:
    from general_manager.chat.models import ChatConversation

    user = scope.get("user")
    if (
        getattr(user, "is_authenticated", False)
        and getattr(user, "pk", None) is not None
    ):
        return ChatConversation.objects.create(user=user)
    return ChatConversation.objects.create(session_key=session_key)


def _turn_record(
    index: int,
    text: str,
    events: list[dict[str, Any]],
    trace: TraceRecorder,
    rows: list[dict[str, Any]],
    previous_count: int,
    elapsed: float,
    history_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    history_sources = history_sources if history_sources is not None else []
    new_rows = rows[previous_count:]
    tools = [event for event in events if event.get("type") == "tool_result"]
    answer = "".join(
        str(event["content"]) for event in events if event.get("type") == "text_chunk"
    )
    persisted_tools = [row for row in new_rows if row["role"] == "tool"]
    # Production caches returned values but not thrown tool exceptions. Its
    # duplicate audit is authoritative; equal outputs do not identify a call.
    tool_audits = deque(
        item
        for item in trace.events
        if item.get("turn") == index and item.get("event_type") == "planned_tool_result"
    )
    pending: dict[tuple[Any, Any, Any], deque[dict[str, Any]]] = defaultdict(deque)
    expected_tools: list[str] = []
    for event in events:
        key = (event.get("task_id"), event.get("id"), event.get("name"))
        if event.get("type") == "tool_call":
            pending[key].append(event)
        elif event.get("type") == "tool_result":
            call = pending[key].popleft() if pending[key] else {}
            audit = tool_audits.popleft() if tool_audits else {}
            result = event.get("result")
            if audit.get("duplicate") or (
                isinstance(result, dict) and result.get("code") == "deadline_exceeded"
            ):
                continue
            expected_tools.append(
                fingerprint(
                    {
                        "name": call.get("name"),
                        "args": call.get("args"),
                        "result": result,
                    }
                )
            )
    saved_tools = [
        fingerprint(
            {
                "name": row.get("tool_name"),
                "args": row.get("tool_args"),
                "result": row.get("tool_result"),
            }
        )
        for row in persisted_tools
    ]
    persistent = (
        any(row["role"] == "user" and row["content"] == text for row in new_rows)
        and (
            not answer
            or any(
                row["role"] == "assistant" and row["content"] == answer
                for row in new_rows
            )
        )
        and Counter(saved_tools) == Counter(expected_tools)
    )
    terminal = next(
        (event for event in reversed(events) if event.get("type") in {"done", "error"}),
        None,
    )
    return cast(
        dict[str, Any],
        sanitize(
            {
                "turn": index,
                "user": text,
                "answer": answer,
                "history": _history(trace, index, history_sources),
                "history_schema_sources": history_sources,
                "history_source": "production_prepare_conversation_messages",
                "events": sanitize(events),
                "tool_calls": [
                    event for event in events if event.get("type") == "tool_call"
                ],
                "tool_results": tools,
                "terminal": terminal,
                "durable_messages": sanitize(new_rows),
                "persistence_verified": persistent,
                "latency_seconds": elapsed,
                "facts": None,
                "semantic_judgment": None,
                "task_quality_status": "unscored",
            }
        ),
    )


def _failure_flags(
    turns: list[dict[str, Any]],
    trace: TraceRecorder,
    observed_gaps: list[dict[str, Any]],
    tool_diagnostics: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Preserve underlying failures hidden by production's stable public errors."""
    flags: set[str] = {
        item["status"]
        for item in (tool_diagnostics or [])
        if item["status"] in FAILURE_HIERARCHY
    }
    for call in trace.calls:
        error_type, code = call.get("error_type"), str(call.get("error_code", ""))
        if "budget_exhausted" in code or error_type == "RoundBudgetExhausted":
            flags.add("budget_exhausted")
        elif error_type == "EvalError":
            flags.add(
                "harness_failure"
                if code
                in {
                    "invalid_configuration",
                    "use_explicit_local_runner",
                    "concurrent_use_not_supported",
                    "unsupported_role",
                    "missing_tool_call_id",
                }
                else "transport_failure"
            )
        elif error_type in {
            "TimeoutError",
            "ConnectError",
            "ReadTimeout",
            "ConnectTimeout",
            "OSError",
        }:
            flags.add("transport_failure")
        elif error_type and error_type not in {"CancelledError", "GeneratorExit"}:
            flags.add("harness_failure")
    if observed_gaps:
        flags.add("interface_capability_gap")
    for turn in turns:
        if turn.get("harness_failure"):
            flags.add("harness_failure")
        if not turn["persistence_verified"]:
            flags.add("harness_failure")
        terminal = turn.get("terminal") or {}
        reasons = {
            item.get("reason")
            for item in terminal.get("orchestration", {}).get("unresolved", [])
        }
        reasons.add(terminal.get("code"))
        if "budget_exhausted" in reasons:
            flags.add("budget_exhausted")
        if "deadline_exceeded" in reasons:
            # A production deadline spends the wall-clock budget. Keep its
            # precise phase/code in the terminal event, not a new top-level class.
            flags.add("budget_exhausted")
        if terminal.get("type") == "error" and not flags:
            flags.add("model_task_failure")
    return [flag for flag in FAILURE_HIERARCHY if flag in flags]


def _diagnose_query_failure(runtime: Any, call: dict[str, Any]) -> dict[str, Any]:
    """Replay a failed read and prove structural mismatches against real schema.

    This never supplies evidence to the model or substitutes a successful query.
    Invalid selections/filters remain model errors; unexplained errors remain
    infrastructure failures instead of being guessed to be model mistakes.
    """
    from general_manager.chat.graphql_contract import ChatReadContractError
    from general_manager.chat.tools import ManagerNotChatExposedError
    from general_manager.api.graphql_resolvers import (
        UnsupportedExcludeNoneRelationFilterError,
    )

    arguments = call["args"]
    diagnostic: dict[str, Any] = {
        "tool": call["name"],
        "arguments": arguments,
        "status": "harness_failure",
        "independent_read_only_replay": True,
        "contract_version": 2,
    }
    try:
        runtime.tool("query", arguments)
    except Exception as error:  # noqa: BLE001 -- capture a synthetic read diagnostic only
        diagnostic.update(error_type=type(error).__name__, error=str(error))
        if isinstance(
            error,
            (
                ChatReadContractError,
                ManagerNotChatExposedError,
                UnsupportedExcludeNoneRelationFilterError,
            ),
        ):
            diagnostic["status"] = "model_task_failure"
        # Resolver/transport errors are not guessed to be model failures.
    else:
        diagnostic["detail"] = (
            "The independent read succeeded; the original failure remains unexplained."
        )
    return diagnostic


def _failed_tool_calls(turns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    failed: dict[str, dict[str, Any]] = {}
    for turn in turns:
        pending: dict[tuple[Any, Any, Any], deque[dict[str, Any]]] = defaultdict(deque)
        for event in turn["events"]:
            key = (event.get("task_id"), event.get("id"), event.get("name"))
            if event.get("type") == "tool_call":
                pending[key].append(event)
            elif event.get("type") == "tool_result" and pending[key]:
                call = pending[key].popleft()
                result = event.get("result")
                if isinstance(result, dict) and result.get("code") == "tool_failed":
                    failed[
                        fingerprint({"tool": call["name"], "arguments": call["args"]})
                    ] = call
    return failed


def consumer_watchdog_seconds(settings: Any) -> float:
    """Allow the production evidence + synthesis budgets and transport cleanup."""
    return float(
        settings.evidence_timeout_seconds + settings.synthesis_timeout_seconds + 5.0
    )


def _harness_error(error: BaseException) -> dict[str, str]:
    return {
        "origin": "harness",
        "error_type": type(error).__name__,
        "code": "harness_failure",
    }


def _watchdog_remaining(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _require_output_type(output: Mapping[str, Any], expected: str) -> None:
    if output["type"] != expected:
        message = "unexpected_consumer_output"
        raise RuntimeError(message)


async def _communicator_operation(operation: Coroutine[Any, Any, Any]) -> Any:
    """Isolate ASGI's legacy timeout cancellation from the caller's task."""
    pending = asyncio.create_task(operation)
    try:
        # External cancellation must still propagate even if ASGI wait swallows
        # cancellation internally. Always finish our subordinate operation.
        return await asyncio.shield(pending)
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def _consume_events(
    communicator: Any, events: list[dict[str, Any]], timeout: float
) -> dict[str, str] | None:
    """Keep received events when the ASGI application fails or is cancelled."""
    deadline = monotonic() + timeout
    try:
        while True:
            output = await _communicator_operation(
                communicator.receive_output(timeout=_watchdog_remaining(deadline))
            )
            _require_output_type(output, "websocket.send")
            event = json.loads(output["text"])
            events.append(event)
            if event.get("type") in {"done", "error"}:
                return None
    except asyncio.CancelledError as error:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise
        failure = _harness_error(error)
    except Exception as error:  # noqa: BLE001 -- retain infrastructure failures as observations
        failure = _harness_error(error)
    events.append({"type": "error", "code": "harness_failure"})
    return failure


async def _scheduler_turn(
    text: str,
    conversation: Any,
    scope: dict[str, Any],
    planned_settings: Any,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    from general_manager.chat.consumer import ChatConsumer
    from general_manager.chat.context import prepare_conversation_messages
    from general_manager.chat.models import append_chat_message
    from general_manager.chat.planned.scheduler import (
        SchedulerCallbacks,
        iter_planned_read_events,
        prepare_planned_turn,
    )
    from general_manager.chat.rate_limits import enforce_chat_rate_limit

    if events is None:
        events = []
    admitted = await sync_to_async(enforce_chat_rate_limit)(scope)
    if admitted is not None:
        events.append({"type": "error", "code": "rate_limited"})
        return events
    await sync_to_async(append_chat_message)(conversation, role="user", content=text)
    messages = await prepare_conversation_messages(
        conversation, ExperimentProvider(), allow_summarization=False, scope=scope
    )
    callbacks = SchedulerCallbacks()
    prepared = await prepare_planned_turn(
        text,
        messages,
        planned_settings,
        ChatConsumer._planned_catalog_summary(planned_settings),
        callbacks=callbacks,
        scope=scope,
    )
    if prepared.mutation_plan is not None:
        # An evaluator must never invoke the consumer's legacy mutation fallback.
        events.append({"type": "error", "code": "read_only_eval_mutation_plan"})
        return events
    async for event in iter_planned_read_events(
        prepared,
        scope=scope,
        conversation=conversation,
        messages=messages,
        callbacks=callbacks,
    ):
        events.append(event)
    return events


async def run_case(
    runtime: Any,
    case: Mapping[str, Any],
    profile: EvaluationProfile,
    provider_factory: ProviderFactory,
    *,
    route: str = "scheduler",
    mode: str = "offline",
) -> dict[str, Any]:
    """Run one isolated synthetic conversation; scoring is deliberately separate."""
    from django.conf import settings as django_settings
    from django.test.utils import override_settings
    from general_manager.chat.planned.config import get_planned_chat_settings
    from general_manager.chat.settings import validate_chat_settings

    if route not in {"scheduler", "consumer"}:
        raise ValueError("unknown_harness_route")
    if mode not in {"offline", "live"}:
        raise ValueError("unknown_harness_mode")
    if mode == "live" and getattr(provider_factory, "offline_only", False):
        raise ValueError("scripted_provider_is_offline_only")
    trace = TraceRecorder()
    scope = dict(runtime.scope)
    session_key = "gm-eval-" + uuid4().hex
    scope["session_key"] = session_key
    scope["session"] = SimpleNamespace(session_key=session_key)
    scope.update(
        type="websocket",
        path="/chat/",
        raw_path=b"/chat/",
        headers=[],
        query_string=b"",
        subprotocols=[],
        client=("127.0.0.1", 1),
        server=("127.0.0.1", 80),
    )
    configuration = dict(getattr(django_settings, "GENERAL_MANAGER", {}))
    chat = dict(configuration.get("CHAT", {}))
    catalog = getattr(runtime, "catalog", chat.get("planned", {}).get("catalog"))
    chat.update(
        profile.chat_settings(
            catalog=catalog,
            audit_sink=trace.audit,
            max_results=2 if case.get("fixture_variant") == "page2" else 200,
        )
    )
    chat["system_prompt"] = (
        str(chat.get("system_prompt", ""))
        + f"\nSynthetic business clock: {FIXED_CLOCK}. Calendar years and business dates use UTC."
        + "\n"
        + str(getattr(runtime, "glossary", ""))
    )
    configuration["CHAT"] = chat
    turns: list[dict[str, Any]] = []
    # Independent capability probes never enter the model conversation. Their
    # exact failed inputs distinguish an existing interface gap from bad output.
    capability_checks = await sync_to_async(runtime.verify)()
    communicator: Any = None
    conversation = await sync_to_async(_new_conversation)(scope, session_key)
    previous_count = 0
    with (
        override_settings(GENERAL_MANAGER=configuration),
        bind_provider_factory(provider_factory, trace),
    ):
        validate_chat_settings()
        planned = get_planned_chat_settings()
        connection_failure = None
        if route == "consumer":
            from asgiref.testing import ApplicationCommunicator
            from general_manager.chat.consumer import ChatConsumer

            consumer_application = ChatConsumer.as_asgi()

            async def bound_application(
                app_scope: Any, receive: Any, send: Any
            ) -> None:
                # ASGI applications begin in a fresh Context. Bind the explicit
                # test provider here instead of patching any production module.
                with bind_provider_factory(provider_factory, trace):
                    await consumer_application(app_scope, receive, send)

            communicator = cast(Any, ApplicationCommunicator)(bound_application, scope)
            try:
                await communicator.send_input({"type": "websocket.connect"})
                connected = await _communicator_operation(
                    communicator.receive_output(timeout=5)
                )
                _require_output_type(connected, "websocket.accept")
            except Exception as error:  # noqa: BLE001 -- include connection failure in report
                connection_failure = _harness_error(error)
        try:
            for index, text in enumerate(case["turns"], start=1):
                trace.turn = index
                started = monotonic()
                events = []
                turn_failure = connection_failure
                if connection_failure is not None:
                    events.append({"type": "error", "code": "harness_failure"})
                elif communicator is None:
                    try:
                        events = await _scheduler_turn(
                            text, conversation, scope, planned, events
                        )
                    except Exception as error:  # noqa: BLE001 -- use the consumer's public error boundary
                        from general_manager.chat.errors import public_chat_error

                        public_error = public_chat_error(error)
                        events.append(public_error.as_event())
                        if public_error.code == "chat_error":
                            turn_failure = _harness_error(error)
                else:
                    try:
                        await communicator.send_input(
                            {
                                "type": "websocket.receive",
                                "text": json.dumps({"type": "message", "text": text}),
                            }
                        )
                        turn_failure = await _consume_events(
                            communicator, events, consumer_watchdog_seconds(planned)
                        )
                    except Exception as error:  # noqa: BLE001 -- persist send failures too
                        events.append({"type": "error", "code": "harness_failure"})
                        turn_failure = _harness_error(error)
                    await asyncio.sleep(0)
                rows = await sync_to_async(_persisted)(conversation)
                history_sources = await sync_to_async(schema_history_sources)(
                    conversation, previous_count
                )
                turns.append(
                    _turn_record(
                        index,
                        text,
                        events,
                        trace,
                        rows,
                        previous_count,
                        monotonic() - started,
                        history_sources,
                    )
                )
                previous_count = len(rows)
                if turn_failure is not None:
                    turns[-1]["harness_failure"] = turn_failure
                    trace.record("harness_failure", turn_failure)
                    break
        finally:
            if communicator is not None:
                try:
                    if communicator.future.done():
                        communicator.future.result()
                    else:
                        await communicator.send_input(
                            {"type": "websocket.disconnect", "code": 1000}
                        )
                        await _communicator_operation(communicator.wait(timeout=5))
                except asyncio.CancelledError as error:
                    task = asyncio.current_task()
                    if task is not None and task.cancelling():
                        raise
                    if turns:
                        turns[-1].setdefault("harness_failure", _harness_error(error))
                except Exception as error:  # noqa: BLE001 -- cleanup must not erase the completed record
                    if turns:
                        turns[-1].setdefault("harness_failure", _harness_error(error))
    module_names = (
        "general_manager.chat.consumer",
        "general_manager.chat.context",
        "general_manager.chat.models",
        "general_manager.chat.tools",
        "general_manager.chat.tools.query",
        "general_manager.chat.schema_index",
        "general_manager.chat.graphql_contract",
        "general_manager.chat.schema_inspection",
        "general_manager.chat.tool_metadata",
        "general_manager.chat.system_prompt",
        "general_manager.chat.views",
        "general_manager.chat.provider_profiles",
        "general_manager.api.graphql",
        "general_manager.chat.planned.scheduler",
        "general_manager.chat.planned.schema_projection",
        "general_manager.chat.planned.planner_context",
        "general_manager.chat.planned.provider_calls",
        "general_manager.chat.providers.base",
        "general_manager.chat.planned.planner",
        "general_manager.chat.planned.contract",
        "general_manager.chat.planned.models",
        "general_manager.chat.planned.validation",
        "general_manager.chat.planned.synthesis",
        "general_manager.chat.planned.clarification",
        "general_manager.chat.planned.selector_clarification",
        "general_manager.chat.planned.config",
        "general_manager.chat.planned.budget",
        "general_manager.chat.planned.resolver",
        "general_manager.chat.planned.evidence",
        "general_manager.chat.planned.evidence_selection",
        "general_manager.chat.planned.calculations",
        "general_manager.chat.planned.calculation_scope",
        "general_manager.chat.planned.conversion",
        "general_manager.interface.unit_contract",
        "general_manager.interface.base_interface",
        "general_manager.api.graphql_units",
        "general_manager.bootstrap",
        "general_manager.chat.planned.deferred_calculations",
        "general_manager.chat.planned.selector_question",
        "general_manager.chat.planned.action_contract",
        "general_manager.chat.mutation_inputs",
        "experiments.gm_eval.harness",
        "experiments.gm_eval.history_parity",
        "experiments.gm_eval.profiles",
        "experiments.gm_eval.scripted",
        "experiments.gm_eval.trace",
        "experiments.gm_eval.runtime",
        "experiments.gm_eval.fixtures",
        "experiments.gm_eval.seeds",
        "experiments.siwc_eval.provider",
        "experiments.siwc_eval.http",
    )
    sources = [
        Path(module.__file__)
        for name in module_names
        if (module := sys.modules.get(name)) is not None and module.__file__
    ]
    failed_calls = _failed_tool_calls(turns)
    observed_gaps = [
        gap
        for gap in capability_checks["gaps"]
        if fingerprint({"tool": gap["tool"], "arguments": gap["arguments"]})
        in failed_calls
    ]
    known = {
        fingerprint({"tool": gap["tool"], "arguments": gap["arguments"]})
        for gap in observed_gaps
    }
    tool_diagnostics = []
    for identity, call in failed_calls.items():
        if identity not in known and call["name"] == "query":
            diagnostic = await sync_to_async(_diagnose_query_failure)(runtime, call)
            tool_diagnostics.append(diagnostic)
            if diagnostic["status"] == "interface_capability_gap":
                observed_gaps.append(diagnostic)
    from general_manager.interface import (
        CalculationInterface,
        DatabaseInterface,
        ReadOnlyInterface,
    )

    interfaces = {
        name: "ReadOnly"
        if issubclass(manager.Interface, ReadOnlyInterface)
        else "Database"
        if issubclass(manager.Interface, DatabaseInterface)
        else "Calculation"
        if issubclass(manager.Interface, CalculationInterface)
        else "unknown"
        for name, manager in runtime.managers.items()
    }
    failure_flags = _failure_flags(turns, trace, observed_gaps, tool_diagnostics)
    for turn in turns:
        local_trace = TraceRecorder(
            calls=[call for call in trace.calls if call["turn"] == turn["turn"]]
        )
        local_gaps = [
            gap
            for gap in observed_gaps
            if any(
                call["name"] == gap["tool"] and call["args"] == gap["arguments"]
                for call in turn["tool_calls"]
            )
        ]
        local_diagnostics = [
            item
            for item in tool_diagnostics
            if any(
                call["name"] == item["tool"] and call["args"] == item["arguments"]
                for call in turn["tool_calls"]
            )
        ]
        turn["failure_flags"] = _failure_flags(
            [turn], local_trace, local_gaps, local_diagnostics
        )
    hashes = source_hashes(sources)
    return {
        "case_id": case["id"],
        "core_id": case.get("core_id"),
        "manager_count": len(runtime.managers),
        "snapshot": runtime.snapshot,
        "variant": runtime.variant,
        "seed": runtime.seed,
        "reference_version": REFERENCE_VERSION,
        "status": failure_flags[0] if failure_flags else "infrastructure_check",
        "failure_flags": failure_flags,
        "task_quality_status": "unscored",
        "mode": mode,
        "model_performance_measured": mode == "live",
        "route": route,
        "profile": asdict(profile),
        "turns": turns,
        "trace": trace.as_dict(),
        "gaps": list(CAPABILITY_GAPS),
        "observed_capability_gaps": sanitize(observed_gaps),
        "tool_failure_diagnostics": sanitize(tool_diagnostics),
        "capability_checks": sanitize(capability_checks),
        "clock": FIXED_CLOCK,
        "business_glossary": sanitize(str(getattr(runtime, "glossary", "")).strip()),
        "glossary_sha256": fingerprint(getattr(runtime, "glossary", "")),
        "schema_index": sanitize(runtime.schema_index),
        "registry_interfaces": interfaces,
        "schema_sha256": fingerprint(runtime.schema_index),
        "catalog_sha256": fingerprint(catalog),
        "case_sha256": fingerprint(case),
        "prompt_sha256": fingerprint(case["turns"]),
        "fixture_sha256": fingerprint(runtime.seed_data),
        "source_hashes": hashes,
        "source_sha256": fingerprint(hashes),
        "census": sanitize(runtime.census()),
    }


async def compare_consumer_parity(
    runtime: Any, case: Mapping[str, Any], profile: EvaluationProfile
) -> dict[str, Any]:
    """Compare real ASGI consumer and direct scheduler with identical model scripts."""
    from .scripted import make_script

    scheduler = await run_case(runtime, case, profile, make_script(case, runtime))
    consumer = await run_case(
        runtime, case, profile, make_script(case, runtime), route="consumer"
    )
    keys = (
        "user",
        "answer",
        "history",
        "tool_calls",
        "tool_results",
        "terminal",
        "durable_messages",
        "persistence_verified",
    )
    differences: list[dict[str, Any]] = []
    if any(
        set(side["failure_flags"]) & {"harness_failure", "transport_failure"}
        for side in (scheduler, consumer)
    ):
        differences.append({"turn": None, "field": "infrastructure_failure"})
    for index, (left, right) in enumerate(
        zip(scheduler["turns"], consumer["turns"], strict=True), start=1
    ):
        for key in keys:
            if left[key] != right[key]:
                differences.append({"turn": index, "field": key})
        if not left["persistence_verified"] or not right["persistence_verified"]:
            differences.append({"turn": index, "field": "persistence_failure"})
    provider_keys = (
        "turn",
        "role",
        "model",
        "strong",
        "messages",
        "tools_sha256",
        "text",
        "tool_calls",
        "reported_usage",
        "completed",
        "error_type",
        "error_code",
    )
    role_traces = []
    for side in (scheduler, consumer):
        calls = []
        for call in side["trace"]["provider_calls"]:
            compared = {key: call.get(key) for key in provider_keys}
            try:
                sources = side["turns"][call["turn"] - 1]["history_schema_sources"]
                compared["messages"] = provider_messages(call, sources)
            except (ValueError, KeyError, IndexError, TypeError):
                differences.append(
                    {"turn": call["turn"], "field": "invalid_schema_history_origin"}
                )
            calls.append(compared)
        role_traces.append(calls)
    if role_traces[0] != role_traces[1]:
        differences.append({"turn": None, "field": "provider_role_traces"})
    flags = set(scheduler["failure_flags"]) | set(consumer["failure_flags"])
    failure_flags = [flag for flag in FAILURE_HIERARCHY if flag in flags]
    return {
        "case_id": case["id"],
        "status": failure_flags[0] if failure_flags else "infrastructure_check",
        "failure_flags": failure_flags,
        "parity_passed": not differences,
        "differences": differences,
        "compared_turns": len(scheduler["turns"]),
        "scheduler": scheduler,
        "consumer": consumer,
    }


def offline_check(
    runtime: Any, case_id: str, *, profile: str = "weak-fallback", parity: bool = False
) -> dict[str, Any]:
    """Synchronous CLI entry; all candidate replies are offline test scripts."""
    from .catalog import load_catalog
    from .profiles import make_profile
    from .scripted import make_script

    case = next(item for item in load_catalog() if item["id"] == case_id)
    selected = make_profile(profile)
    if parity:
        result = asyncio.run(compare_consumer_parity(runtime, case, selected))
        result["model_performance_measured"] = False
        result["gaps"] = list(CAPABILITY_GAPS)
        return result
    return asyncio.run(run_case(runtime, case, selected, make_script(case, runtime)))
