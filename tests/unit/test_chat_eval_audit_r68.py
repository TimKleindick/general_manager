"""Differential tests of public evaluator behavior; all providers are local."""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from general_manager.chat.evals import runner
from general_manager.chat.evals.baseline import compare_to_baseline
from dataclasses import replace
from general_manager.chat.evals.readiness import build_summary, readiness_exit_code
from general_manager.chat.providers import (
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    TextChunkEvent,
    TokenUsage,
    ToolCallEvent,
)
from general_manager.chat.planned.provider_calls import (
    complete_provider_round,
    ProviderStreamError,
    ProviderRoundTimeoutError,
)
from general_manager.chat.errors import public_chat_error


def _case(dataset, name):
    return next(case for case in runner.load_dataset(dataset) if case.name == name)


def _record(answer, calls=None, results=None):
    return runner.TurnRecord(
        tool_calls=calls or [], tool_results=results or [], answer_chunks=[answer]
    )


@pytest.mark.parametrize(
    "answer",
    [
        "VehicleManager is not available. The exposed managers are MaterialManager, PartManager and ProjectManager.",
        "That requested manager is not available. MaterialManager, PartManager and ProjectManager are exposed.",
        "No manager named VehicleManager is exposed. MaterialManager and PartManager are available.",
        "You requested VehicleManager, which does not exist in the exposed schema.",
    ],
)
def test_unknown_manager_allows_truthful_unavailability(answer):
    case = _case("edge_cases", "unknown_manager")
    result = runner._score_case(
        case,
        [
            _record(
                answer,
                [{"name": "search_managers", "args": {"query": "VehicleManager"}}],
                [[]],
            )
        ],
    )
    assert result.passed


@pytest.mark.parametrize(
    "answer",
    [
        "VehicleManager is available.",
        "VehicleManager is available. Other managers are not available.",
        "VehicleManager is not unavailable.",
        "I found VehicleManager. It contains all vehicle records.",
    ],
)
def test_unknown_manager_still_rejects_false_availability(answer):
    case = _case("edge_cases", "unknown_manager")
    result = runner._score_case(
        case,
        [
            _record(
                answer,
                [{"name": "search_managers", "args": {"query": "VehicleManager"}}],
                [[]],
            )
        ],
    )
    assert not result.passed


class _ErrorProvider:
    def __init__(self, message):
        self.message = message

    async def complete(self, messages, tools):
        if False:
            yield DoneEvent(TokenUsage())
        raise ValueError(self.message)


@pytest.mark.parametrize("message", ["", "controlled failure"])
def test_caught_provider_exception_is_unscored_independent_of_its_text(message):
    case = _case("basic_queries", "search_then_query_parts")
    result = asyncio.run(runner.run_case(_ErrorProvider(message), case, []))
    assert not result.passed
    assert result.error is not None
    assert result.status == "unscored"


class _LocalStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def _adapter(provider_type, monkeypatch, completed):
    ns = SimpleNamespace
    if provider_type is OllamaProvider:
        chunks = [{"message": {"content": "Complete answer"}}]
        if completed:
            chunks.append({"done": True})
    elif provider_type is OpenAIProvider:
        chunks = [
            ns(
                choices=[
                    ns(index=0, delta=ns(content="Complete answer"), finish_reason=None)
                ],
                usage=None,
            )
        ]
        if completed:
            chunks.append(
                ns(
                    choices=[ns(index=0, delta=ns(content=None), finish_reason="stop")],
                    usage=None,
                )
            )
    elif provider_type is AnthropicProvider:
        chunks = [
            ns(
                type="content_block_delta",
                delta=ns(type="text_delta", text="Complete answer"),
            )
        ]
        if completed:
            chunks.extend(
                [
                    ns(type="message_delta", delta=ns(stop_reason="end_turn")),
                    ns(type="message_stop"),
                ]
            )
    else:
        chunks = [ns(text="Complete answer", candidates=[])]
        if completed:
            chunks.append(ns(text=None, candidates=[ns(finish_reason="STOP")]))
    stream = _LocalStream(chunks)

    async def create(*args, **kwargs):
        return stream

    client = ns(
        _request=create,
        _client=ns(aclose=stream.aclose),
        chat=ns(completions=ns(create=create)),
        messages=ns(create=create),
        aio=ns(models=ns(generate_content_stream=create)),
    )
    monkeypatch.setattr(
        provider_type, "_build_async_client", classmethod(lambda _cls, _config: client)
    )
    return provider_type.from_config({"model": "local-control"}), stream


@pytest.mark.parametrize(
    "provider_type", [OllamaProvider, OpenAIProvider, AnthropicProvider, GeminiProvider]
)
@pytest.mark.parametrize("completed", [False, True])
def test_every_adapter_requires_its_actual_completion_marker(
    provider_type, completed, monkeypatch
):
    provider, stream = _adapter(provider_type, monkeypatch, completed)
    case = runner.EvalCase(
        "completion", "", [{"user": "Answer"}], {"answer_contains": ["Complete answer"]}
    )
    result = asyncio.run(runner.run_case(provider, case, []))
    assert result.passed is completed
    assert result.status == ("pass" if completed else "unscored")
    assert stream.closed


class _EventProvider:
    def __init__(self, events):
        self.events = events

    async def complete(self, messages, tools):
        for event in self.events:
            yield event


@pytest.mark.parametrize(
    "events",
    [
        [TextChunkEvent("Complete answer")],
        [
            TextChunkEvent("Complete answer"),
            DoneEvent(TokenUsage()),
            DoneEvent(TokenUsage()),
        ],
        [
            TextChunkEvent("Complete answer"),
            DoneEvent(TokenUsage()),
            TextChunkEvent("late"),
        ],
    ],
)
def test_neutral_evaluator_rejects_missing_or_nonterminal_completion(events):
    case = runner.EvalCase(
        "completion", "", [{"user": "Answer"}], {"answer_contains": ["Complete answer"]}
    )
    result = asyncio.run(runner.run_case(_EventProvider(events), case, []))
    assert not result.passed
    assert result.status == "unscored"


def _strategy_result(reversed_order):
    case = _case("basic_queries", "search_then_query_parts")
    query = {"name": "query", "args": {"manager": "PartManager", "fields": ["name"]}}
    search = {"name": "search_managers", "args": {"query": "parts"}}
    pairs = [
        (search, [{"manager": "PartManager"}]),
        (query, {"data": [{"name": name} for name in ("Bolt", "Gear", "Bearing")]}),
    ]
    if reversed_order:
        pairs.reverse()
    return runner._score_case(
        case,
        [_record("Bolt, Gear, Bearing", [p[0] for p in pairs], [p[1] for p in pairs])],
    )


def _summary(result):
    return build_summary(
        gate="demo",
        run_metadata={
            "run_hash": "offline",
            "provider": "local",
            "model": "none",
            "fixture": "toy",
            "datasets": ["basic_queries"],
        },
        results=[result],
    )


def test_nonstrict_strategy_remains_passing_through_baseline_and_readiness():
    baseline = _summary(_strategy_result(False))
    result = _strategy_result(True)
    assert result.passed
    summary = _summary(result)
    comparison = compare_to_baseline(summary, baseline)
    assert not comparison.regressed
    assert (
        readiness_exit_code(
            summary=summary, comparison=comparison, fail_on_regression=True
        )
        == 0
    )


def test_missing_required_tool_remains_a_hard_readiness_failure():
    result = _strategy_result(True)
    case = result.case
    failed = runner._score_case(case, [_record("Bolt, Gear, Bearing")])
    assert not failed.passed
    assert (
        readiness_exit_code(
            summary=_summary(failed), comparison=None, fail_on_regression=False
        )
        == 1
    )


def test_default_tier0_selection_uses_the_runner_supported_datasets():
    path = Path(__file__).resolve().parents[2] / "scripts/run_chat_readiness_loop.py"
    spec = importlib.util.spec_from_file_location("readiness_audit_control", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected = module._datasets_for_gate("tier0")
    assert selected == runner._legacy_dataset_names(None)
    assert runner._legacy_dataset_names(selected) == selected


def test_explicit_unsupported_dataset_still_fails_before_provider_use():
    with pytest.raises(ValueError, match="not supported"):
        runner._legacy_dataset_names(["planned_orchestration"])


def test_readiness_distinguishes_unscored_from_quality_fail_on_the_full_denominator():
    good = _strategy_result(False)
    error = runner.EvalResult(case=good.case, error="")
    bad = runner._score_case(good.case, [_record("Wrong answer")])
    metadata = {
        "run_hash": "offline",
        "provider": "local",
        "model": "none",
        "fixture": "toy",
        "datasets": ["basic_queries"],
    }
    summary = build_summary(
        gate="demo", run_metadata=metadata, results=[good, error, bad]
    )
    assert (summary.total, summary.passed, summary.failed, summary.unscored) == (
        3,
        1,
        1,
        1,
    )
    assert summary.pass_rate == 1 / 3
    assert (
        readiness_exit_code(summary=summary, comparison=None, fail_on_regression=False)
        == 1
    )


def test_old_baseline_is_readable_but_cannot_authorize_a_new_contract_gate():
    current = _summary(_strategy_result(False))
    assert current.evaluation_contract == "chat-evaluation/2"
    historical = replace(current, evaluation_contract="chat-evaluation/1")
    comparison = compare_to_baseline(current, historical)
    assert not comparison.comparable
    assert (
        readiness_exit_code(
            summary=current, comparison=comparison, fail_on_regression=True
        )
        == 1
    )


def test_incomplete_provider_stream_keeps_provider_failure_provenance():
    provider = _EventProvider([TextChunkEvent("partial")])
    with pytest.raises(ProviderStreamError):
        asyncio.run(
            complete_provider_round(provider, [], [], 1, classify_provider_errors=True)
        )


def test_consumer_reports_incomplete_stream_as_provider_failure():
    from general_manager.chat.consumer import _iter_provider_events

    async def run():
        return [
            event
            async for event in _iter_provider_events(
                _EventProvider([TextChunkEvent("partial")]), [], []
            )
        ]

    with pytest.raises(ValueError) as caught:
        asyncio.run(run())
    assert public_chat_error(caught.value).code == "provider_failed"


def test_incomplete_tool_request_cannot_execute_a_tool(monkeypatch):
    executed = []
    monkeypatch.setattr(
        runner, "execute_chat_tool", lambda *args: executed.append(args)
    )
    provider = _EventProvider(
        [ToolCallEvent("partial", "query", {"manager": "PartManager"})]
    )
    result = asyncio.run(
        runner.run_case(provider, _case("basic_queries", "search_then_query_parts"), [])
    )
    assert result.status == "unscored"
    assert not result.passed
    assert not executed


def test_summary_cannot_commit_an_incomplete_provider_response():
    from general_manager.chat.context import summarize_messages_with_provider

    with pytest.raises(ValueError, match="stream_without_completion"):
        asyncio.run(
            summarize_messages_with_provider(
                _EventProvider([TextChunkEvent("partial")]), []
            )
        )


@pytest.mark.parametrize(
    "reason", ["length", "content_filter", "function_call", "future_reason"]
)
def test_openai_does_not_complete_unsupported_or_incomplete_rounds(reason, monkeypatch):
    provider, stream = _adapter(OpenAIProvider, monkeypatch, True)
    stream.chunks[-1].choices[0].finish_reason = reason
    stream.chunks[-1].choices[0].delta.function_call = SimpleNamespace(
        name="query", arguments='{"manager":"PartManager"}'
    )
    case = runner.EvalCase(
        "completion", "", [{"user": "Answer"}], {"answer_contains": ["Complete answer"]}
    )
    result = asyncio.run(runner.run_case(provider, case, []))
    assert not result.passed

    assert result.status == "unscored"
    assert stream.closed


@pytest.mark.parametrize("reason", ["length", "future_reason"])
def test_ollama_does_not_complete_explicit_unsuccessful_terminal(reason, monkeypatch):
    provider, stream = _adapter(OllamaProvider, monkeypatch, True)
    stream.chunks[-1]["done_reason"] = reason
    case = runner.EvalCase("completion", "", [{"user": "Answer"}], {})
    result = asyncio.run(runner.run_case(provider, case, []))
    assert not result.passed
    assert result.status == "unscored"
    assert stream.closed


@pytest.mark.parametrize("classify", [False, True])
def test_planned_round_preserves_observed_usage_on_cleanup_failure(classify):
    class Provider:
        async def complete(self, messages, tools):
            yield TextChunkEvent("answer")
            yield DoneEvent(TokenUsage(7, 11))
            detail = "controlled cleanup failure"
            raise OSError(detail)

    with pytest.raises(ProviderStreamError) as caught:
        asyncio.run(
            complete_provider_round(
                Provider(), [], [], 1, classify_provider_errors=classify
            )
        )
    assert caught.value.usage == TokenUsage(7, 11)
    assert public_chat_error(caught.value).code == "provider_failed"


def test_planned_deadline_retains_usage_and_closes_before_propagating():
    class Provider:
        closed = False

        async def complete(self, messages, tools):
            try:
                yield TextChunkEvent("answer")
                yield DoneEvent(TokenUsage(7, 11))
                await asyncio.sleep(10)
            finally:
                self.closed = True

    provider = Provider()
    with pytest.raises(TimeoutError) as caught:
        asyncio.run(complete_provider_round(provider, [], [], 0.01))
    assert caught.value.usage == TokenUsage(7, 11)
    assert provider.closed


def test_planned_round_closes_rejected_stream_before_returning_to_caller():
    class Provider:
        closed = False

        async def complete(self, messages, tools):
            try:
                yield TextChunkEvent("ambiguous")
                yield ToolCallEvent("call", "query", {})
                yield DoneEvent(TokenUsage())
            finally:
                self.closed = True

    async def check():
        from general_manager.chat.planned.provider_calls import (
            InvalidProviderRoundError,
        )

        provider = Provider()
        with pytest.raises(InvalidProviderRoundError):
            await complete_provider_round(provider, [], [], 1)
        # Check inside the running loop, before asyncio.run finalizes generators.
        assert provider.closed

    asyncio.run(check())


@pytest.mark.parametrize("case_name", ["budget_exhaustion", "deadline_exhaustion"])
def test_real_planned_scheduler_errors_cannot_be_pass(case_name, tmp_path):
    from general_manager.chat.evals.fixtures import planned_role_overrides
    from general_manager.chat.evals.traces import EvalTraceWriter
    import json

    case = _case("planned_orchestration", case_name)
    trace = tmp_path / "outcome.jsonl"
    result = asyncio.run(
        runner.run_case(
            None,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
            trace_writer=EvalTraceWriter(trace),
        )
    )
    assert not result.passed
    assert result.error == case.expectations["planned"]["terminal_reason"]
    assert result.status == "unscored"
    written = json.loads(trace.read_text())
    assert written["passed"] is False
    assert written["status"] == "unscored"
    summary = _summary(result)
    assert (summary.total, summary.passed, summary.failed, summary.unscored) == (
        1,
        0,
        0,
        1,
    )
    assert (
        readiness_exit_code(summary=summary, comparison=None, fail_on_regression=False)
        == 1
    )


@pytest.mark.parametrize("owner,attempts", [("planner", 3), ("synthesis", 2)])
@pytest.mark.parametrize(
    "error_type,code",
    [
        (ProviderStreamError, "provider_failed"),
        (ProviderRoundTimeoutError, "deadline_exceeded"),
    ],
)
def test_planned_stages_account_usage_from_provider_failures(
    monkeypatch, owner, attempts, error_type, code
):
    from general_manager.chat.planned.budget import RoundBudget
    from general_manager.chat.planned.planner import plan_request, InvalidPlanError
    from general_manager.chat.planned.synthesis import (
        synthesize_answer,
        SynthesisFailedError,
    )
    from tests.unit.test_chat_planned_planner import _settings as planner_settings
    from tests.unit.test_chat_planned_synthesis import (
        _settings as synthesis_settings,
        _store,
    )

    async def failed(*args, **kwargs):
        raise error_type(usage=TokenUsage(7, 11))

    monkeypatch.setattr(
        f"general_manager.chat.planned.{owner}.complete_provider_round", failed
    )
    if owner == "planner":
        request = plan_request(
            "show parts", [], planner_settings(), RoundBudget(()), {}
        )
        failure = InvalidPlanError
    else:
        request = synthesize_answer(
            "show parts", _store(), {}, synthesis_settings(), RoundBudget(())
        )
        failure = SynthesisFailedError
    with pytest.raises(failure) as caught:
        asyncio.run(request)
    assert caught.value.usage == TokenUsage(7 * attempts, 11 * attempts)
    assert len(caught.value.attempt_usages) == attempts
    assert caught.value.code == code


@pytest.mark.parametrize(
    "error_type,code,origin",
    [
        (ProviderStreamError, "provider_failed", "provider_exception"),
        (ProviderRoundTimeoutError, "deadline_exceeded", "scheduler"),
    ],
)
def test_executor_accounts_provider_failure_usage_without_tool_execution(
    monkeypatch, error_type, code, origin
):
    from tests.unit.test_chat_planned_tool_feedback import _runner

    async def failed(*args, **kwargs):
        raise error_type(usage=TokenUsage(7, 11))

    monkeypatch.setattr(
        "general_manager.chat.planned.scheduler.complete_provider_round", failed
    )
    executor = _runner(lambda *_: pytest.fail("failure must not execute a tool"))
    runtime = executor.runtimes["task_1"]
    asyncio.run(executor.run_task(runtime))
    assert executor.usage == TokenUsage(7, 11)
    assert runtime.reason == code
    assert runtime.reason_origin == origin


@pytest.mark.parametrize(
    "code", ["provider_failed", "deadline_exceeded", "synthesis_failed"]
)
def test_planned_model_declared_technical_codes_remain_model_failures(
    monkeypatch, code
):
    from tests.unit.test_chat_planned_scheduler import _Executor, _settings, _task
    from general_manager.chat.planned.scheduler import PreparedPlannedTurn
    from general_manager.chat.planned.models import ValidatedPlan
    from general_manager.chat.evals.fixtures import planned_role_overrides

    monkeypatch.setattr(
        _Executor,
        "responses_by_task",
        {"task_1": [{"action": "block", "reason": code}]},
    )
    monkeypatch.setattr(_Executor, "responses", [])

    async def prepared(*args, **kwargs):
        return PreparedPlannedTurn.for_plan(
            ValidatedPlan("read", (_task("task_1"),)),
            _settings(),
            user_text="show parts",
        )

    monkeypatch.setattr(runner, "prepare_planned_turn", prepared)
    case = _case("planned_orchestration", "deadline_exhaustion")
    from django.test.utils import override_settings
    from experiments.gm_eval.trace import TraceRecorder
    from experiments.gm_eval.harness import _failure_flags

    trace = TraceRecorder(turn=1)
    with override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "audit": {"enabled": True, "level": "tool_calls", "logger": trace.audit}
            }
        }
    ):
        result = asyncio.run(
            runner.run_case(
                None,
                case,
                [],
                strategy="planned",
                role_overrides=planned_role_overrides(case),
            )
        )
    assert result.error == code
    assert result.failure_category == "model_task_failure"
    assert not result.passed
    assert result.status == "fail"

    assert _failure_flags(
        [
            {
                "turn": 1,
                "terminal": result.trace["events"][-1],
                "persistence_verified": True,
            }
        ],
        trace,
        [],
    ) == ["model_task_failure"]


@pytest.mark.parametrize(
    "events",
    [
        [],
        [{"type": "text_chunk", "content": "partial"}],
        [{"type": "done"}, {"type": "text_chunk", "content": "late"}],
    ],
)
def test_planned_evaluator_requires_one_terminal_event(monkeypatch, events):
    from general_manager.chat.evals.fixtures import planned_role_overrides

    async def streamed(*args, **kwargs):
        for event in events:
            yield event

    monkeypatch.setattr(runner, "iter_planned_read_events", streamed)
    case = _case("planned_orchestration", "deadline_exhaustion")
    result = asyncio.run(
        runner.run_case(
            None,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
        )
    )
    assert not result.passed
    assert result.status == "unscored"


def test_tool_budget_recovery_cannot_accept_eof_as_final_completion(monkeypatch):
    class Provider:
        calls = 0

        async def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield ToolCallEvent(
                    "query", "query", {"manager": "PartManager", "fields": ["name"]}
                )
                yield DoneEvent(TokenUsage())
            else:
                yield TextChunkEvent("Bolt")

    monkeypatch.setattr(runner, "MAX_TOOL_ITERATIONS", 1)
    monkeypatch.setattr(
        runner, "execute_chat_tool", lambda *_args: {"data": [{"name": "Bolt"}]}
    )
    case = runner.EvalCase(
        "recovery", "", [{"user": "Show parts"}], {"answer_contains": ["Bolt"]}
    )
    provider = Provider()
    result = asyncio.run(
        runner.run_case(
            provider,
            case,
            [{"name": "query", "description": "query"}],
            recover_missing_tools=True,
        )
    )
    assert provider.calls == 2
    assert not result.passed
    assert result.status == "unscored"


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("close_failure", [False, True])
def test_optional_trace_write_failure_closes_the_owned_stream_immediately(
    recovery, close_failure
):
    class Provider:
        closed = False

        async def complete(self, messages, tools):
            try:
                yield TextChunkEvent("answer")
                yield DoneEvent(TokenUsage())
            finally:
                self.closed = True
                if close_failure:
                    raise ValueError()

    class BrokenOutput:
        def write(self, value):
            raise OSError()

        def flush(self):
            pass

    async def check():
        provider = Provider()
        with pytest.raises(OSError):
            if recovery:
                await runner._collect_model_final_answer(
                    provider=provider, messages=[], stream=BrokenOutput()
                )
            else:
                await runner._run_turn(provider, [], [], stream=BrokenOutput())
        assert provider.closed

    asyncio.run(check())


def test_external_cancellation_survives_failing_stream_cleanup():
    async def check():
        entered = asyncio.Event()
        closed = []

        class Provider:
            async def complete(self, messages, tools):
                try:
                    entered.set()
                    await asyncio.Event().wait()
                    yield DoneEvent(TokenUsage())
                finally:
                    closed.append(True)
                    raise OSError()

        task = asyncio.create_task(complete_provider_round(Provider(), [], [], 1))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == [True]

    asyncio.run(check())


def test_provider_cannot_turn_pending_cancellation_into_success():
    async def check():
        entered = asyncio.Event()

        class Provider:
            async def complete(self, messages, tools):
                try:
                    entered.set()
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    yield TextChunkEvent("late answer")
                    yield DoneEvent(TokenUsage())

        task = asyncio.create_task(complete_provider_round(Provider(), [], [], 1))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(check())


@pytest.mark.parametrize("stage", ["planning", "synthesis"])
def test_actual_stage_deadline_retains_observed_usage_without_another_attempt(
    monkeypatch, stage
):
    from dataclasses import replace
    from general_manager.chat.planned import planner, scheduler, synthesis
    from general_manager.chat.planned.budget import RoundBudget
    from tests.unit.test_chat_planned_planner import _settings as planner_settings
    from tests.unit.test_chat_planned_synthesis import _settings, _store

    class Provider:
        calls = 0
        closed = 0

        async def complete(self, messages, tools):
            self.calls += 1
            try:
                yield TextChunkEvent(
                    '{"answer":"No parts found.","evidence_ids":["ev-query-1"]}'
                )
                yield DoneEvent(TokenUsage(7, 11))
                await asyncio.Event().wait()
            finally:
                self.closed += 1

    provider = Provider()
    accounted = []
    if stage == "planning":
        monkeypatch.setattr(planner, "build_profile_provider", lambda *_: provider)
        request = scheduler.prepare_planned_turn(
            "show parts",
            [],
            replace(planner_settings(), evidence_timeout_seconds=0.05),
            {},
            callbacks=scheduler.SchedulerCallbacks(
                enforce_rate_limit=lambda *_args, **kwargs: accounted.append(kwargs)
            ),
        )
        expected = TimeoutError
    else:
        monkeypatch.setattr(synthesis, "build_profile_provider", lambda *_: provider)
        request = synthesis.synthesize_answer(
            "show parts",
            _store(),
            {},
            replace(_settings(), synthesis_timeout_seconds=0.05),
            RoundBudget(()),
        )
        expected = synthesis.SynthesisFailedError
    with pytest.raises(expected) as caught:
        asyncio.run(request)
    assert provider.calls == provider.closed == 1
    if stage == "planning":
        assert accounted == [
            {"input_tokens": 7, "output_tokens": 11, "count_request": False}
        ]
    else:
        assert caught.value.code == "deadline_exceeded"
        assert caught.value.usage == TokenUsage(7, 11)
        assert caught.value.attempt_usages == (TokenUsage(7, 11),)


def test_partial_done_with_real_provider_failure_is_unscored(monkeypatch):
    from general_manager.chat.evals.fixtures import planned_role_overrides

    case = _case("planned_orchestration", "partial_independent_answer")
    scripts = case.expectations["planned"]["scripts"]
    scripts["executor"] = [
        step for step in scripts["executor"] if step.get("task_id") != "task_2"
    ]
    terminals = []
    original = runner.iter_planned_read_events

    async def tapped(prepared, *args, **kwargs):
        async for event in original(prepared, *args, **kwargs):
            terminals.append(event)
            yield event

    monkeypatch.setattr(runner, "iter_planned_read_events", tapped)
    result = asyncio.run(
        runner.run_case(
            None,
            case,
            [],
            strategy="planned",
            role_overrides=planned_role_overrides(case),
        )
    )
    assert terminals[-1]["type"] == "done"
    assert terminals[-1]["orchestration"]["coverage"] == {"resolved": 1, "total": 2}
    assert result.status == "unscored"
    assert not result.passed


@pytest.mark.parametrize("stage,attempts", [("planning", 3), ("synthesis", 2)])
def test_stage_observation_and_error_usage_are_accounted_once(
    monkeypatch, stage, attempts
):
    from general_manager.chat.planned import planner, synthesis
    from general_manager.chat.planned.budget import RoundBudget
    from tests.unit.test_chat_planned_planner import _settings as planner_settings
    from tests.unit.test_chat_planned_synthesis import _settings, _store

    class Provider:
        async def complete(self, messages, tools):
            yield TextChunkEvent("reply")
            yield DoneEvent(TokenUsage(7, 11))
            raise OSError()

    if stage == "planning":
        monkeypatch.setattr(planner, "build_profile_provider", lambda *_: Provider())
        request = planner.plan_request(
            "show parts", [], planner_settings(), RoundBudget(()), {}
        )
        failure = planner.InvalidPlanError
    else:
        monkeypatch.setattr(synthesis, "build_profile_provider", lambda *_: Provider())
        request = synthesis.synthesize_answer(
            "show parts", _store(), {}, _settings(), RoundBudget(())
        )
        failure = synthesis.SynthesisFailedError
    with pytest.raises(failure) as caught:
        asyncio.run(request)
    assert caught.value.usage == TokenUsage(7 * attempts, 11 * attempts)
    assert caught.value.attempt_usages == (TokenUsage(7, 11),) * attempts


@pytest.mark.parametrize("ending", ["stage_timeout", "cleanup_failure", "success"])
def test_executor_observation_survives_own_stage_cancel_and_is_accounted_once(
    monkeypatch, ending
):
    from general_manager.chat.planned import scheduler
    from tests.unit.test_chat_planned_tool_feedback import _runner

    class Provider:
        calls = 0
        closed = 0

        async def complete(self, messages, tools):
            self.calls += 1
            try:
                yield TextChunkEvent('{"action":"block","reason":"manager_unresolved"}')
                yield DoneEvent(TokenUsage(7, 11))
                if ending == "stage_timeout":
                    await asyncio.Event().wait()
                elif ending == "cleanup_failure":
                    raise OSError()
            finally:
                self.closed += 1

    provider = Provider()
    monkeypatch.setattr(scheduler, "build_profile_provider", lambda *_: provider)
    accounted = []
    executor = _runner(lambda *_: pytest.fail("no tool is requested"))
    executor.callbacks = replace(
        executor.callbacks,
        enforce_rate_limit=lambda *_args, **kwargs: accounted.append(kwargs),
    )

    async def check():
        runtime = executor.runtimes["task_1"]
        if ending == "stage_timeout":
            executor.clock = asyncio.get_running_loop().time
            executor.deadline = executor.clock() + 0.05
            await executor.run()
            assert runtime.reason == "deadline_exceeded"
        else:
            await executor._execute_one_pass(runtime, ())
        assert provider.calls == provider.closed == 1
        assert executor.usage == TokenUsage(7, 11)
        assert accounted == [
            {"input_tokens": 7, "output_tokens": 11, "count_request": False}
        ]

    asyncio.run(check())
