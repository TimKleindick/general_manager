"""Offline checks for the explicitly authorized larger suite."""

import asyncio

import pytest

from experiments.siwc_eval.errors import EvalError
from experiments.siwc_eval.suite import MODEL, MediumTransport, select_model


def test_suite_budgets_every_user_turn_separately():
    from types import SimpleNamespace
    from experiments.siwc_eval.suite import budget_for_case

    assert budget_for_case(SimpleNamespace(conversation=[{"user": "one"}])) == 16
    assert (
        budget_for_case(
            SimpleNamespace(conversation=[{"user": "one"}, {"user": "two"}])
        )
        == 32
    )
    for count in (0, 17):
        with pytest.raises(EvalError, match="invalid_case_turn_count"):
            budget_for_case(SimpleNamespace(conversation=[{"user": "one"}] * count))


@pytest.mark.parametrize(
    ("error", "outcome"),
    [("turn_request_budget_exhausted", "budget_exhausted"), (None, "quality_failure")],
)
def test_suite_distinguishes_budget_from_quality_failures(
    monkeypatch, tmp_path, error, outcome
):
    from general_manager.chat.evals.runner import EvalCase, EvalResult
    from general_manager.chat.evals.judges.result_set import ResultSetScore
    from experiments.siwc_eval.suite import evaluate_suite

    case = EvalCase("synthetic", "", [{"user": "query"}], {})
    monkeypatch.setattr(
        "experiments.siwc_eval.datasets.load_experiment_dataset", lambda _: [case]
    )
    monkeypatch.setattr(
        "experiments.siwc_eval.datasets.setup_experiment_dataset", lambda _: None
    )
    monkeypatch.setattr(
        "experiments.siwc_eval.answer_reference.build_answer_references",
        lambda *_: [{"expected": {"rows": ["Bolt"]}}],
    )
    monkeypatch.setattr("general_manager.chat.tools.get_tool_definitions", lambda: [])

    class IncorrectJudge:
        requests = 0

        def __init__(self, transport):
            pass

        async def grade_turn(self, **kwargs):
            self.requests += 1
            return {"verdict": "incorrect", "reason": "Wrong part.", "requests": 1}

    monkeypatch.setattr(
        "experiments.siwc_eval.answer_judge.AnswerJudge", IncorrectJudge
    )

    async def run_case(provider, case, definitions, **kwargs):
        assert provider.provider_config["max_requests"] == 16
        assert kwargs["max_tool_iterations"] == 16
        kwargs["trace_writer"].write_case({"turns": [{"answer": "Bearing"}]})
        return EvalResult(
            case=case,
            error=error,
            result_set_score=ResultSetScore(False, "result_set_mismatch"),
        )

    monkeypatch.setattr("general_manager.chat.evals.runner.run_case", run_case)
    report = {"results": [], "completed": False}
    asyncio.run(
        evaluate_suite(
            MediumTransport("synthetic"),
            ("basic_queries",),
            report,
            tmp_path / "report.json",
        )
    )
    assert report["results"][0]["outcome"] == outcome
    assert report["completed"]


def test_medium_is_sent_without_mutating_payload(monkeypatch):
    seen = []

    async def stream(self, body):
        seen.append(body)
        yield {"type": "response.completed"}

    monkeypatch.setattr("experiments.siwc_eval.http.LiveTransport.stream", stream)
    body = {"model": MODEL}

    async def run():
        return [event async for event in MediumTransport("synthetic").stream(body)]

    assert asyncio.run(run()) == [{"type": "response.completed"}]
    assert seen == [{"model": MODEL, "reasoning": {"effort": "medium"}}]
    assert body == {"model": MODEL}


def test_catalog_requires_exact_requested_model():
    assert select_model({"models": [{"slug": MODEL, "visibility": "list"}]}) == MODEL
    with pytest.raises(EvalError, match="requested_model_not_available"):
        select_model({"models": [{"slug": "gpt-6-sol", "visibility": "list"}]})


@pytest.mark.parametrize("failure", ["http_429", "request_budget_exhausted"])
def test_suite_stops_on_quota_but_continues_after_case_budget(
    monkeypatch, tmp_path, failure
):
    from types import SimpleNamespace
    from experiments.siwc_eval.suite import evaluate_suite

    monkeypatch.setattr(
        "experiments.siwc_eval.datasets.setup_experiment_dataset", lambda _: None
    )
    monkeypatch.setattr(
        "experiments.siwc_eval.answer_reference.build_answer_references",
        lambda *_: [{"expected": {"rows": ["synthetic"]}}],
    )
    monkeypatch.setattr("general_manager.chat.tools.get_tool_definitions", lambda: [])
    monkeypatch.setattr(
        "general_manager.chat.system_prompt.build_system_prompt", lambda: "synthetic"
    )
    monkeypatch.setattr(
        "experiments.siwc_eval.datasets.fixture_fingerprint", lambda _: "synthetic"
    )
    monkeypatch.setattr(
        "experiments.siwc_eval.datasets.load_experiment_dataset",
        lambda _: [
            SimpleNamespace(
                name=name, conversation=[{"user": "synthetic"}], expectations={}
            )
            for name in ("one", "two")
        ],
    )

    async def run_case(*args, **kwargs):
        raise EvalError(failure)

    monkeypatch.setattr("general_manager.chat.evals.runner.run_case", run_case)
    report = {"results": [], "completed": False}
    asyncio.run(
        evaluate_suite(
            MediumTransport("synthetic"),
            ("large_schema",),
            report,
            tmp_path / "report.json",
        )
    )
    assert len(report["results"]) == (1 if failure == "http_429" else 2)
    assert report["completed"] is (failure == "request_budget_exhausted")


@pytest.mark.parametrize("valid", [True, False])
def test_missing_content_type_requires_completed_sse(monkeypatch, valid):
    import httpx
    from experiments.siwc_eval.http import LiveTransport
    from experiments.siwc_eval.provider import Provider
    from general_manager.chat.providers.base import Message, DoneEvent

    wire = (
        b'data: {"type":"response.completed","response":{"output":[]}}\n\n'
        if valid
        else b"<html>not an event stream</html>"
    )
    factory = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: factory(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=wire)
            ),
            **kwargs,
        ),
    )

    async def run():
        return [
            event
            async for event in Provider("offline", LiveTransport("synthetic")).complete(
                [Message("user", "hello")], []
            )
        ]

    if valid:
        assert isinstance(asyncio.run(run())[-1], DoneEvent)
    else:
        with pytest.raises(EvalError, match="stream_without_completion"):
            asyncio.run(run())
