"""Primary SIWC verdicts must reflect answers, not legacy tool-path scores."""

import asyncio
import json

import pytest

from experiments.siwc_eval.errors import EvalError
from experiments.siwc_eval.suite import MediumTransport, evaluate_suite
from general_manager.chat.evals.runner import EvalCase, EvalResult
from general_manager.chat.evals.judges.result_set import ResultSetScore


def configure_case(monkeypatch, *, verdict="correct", legacy_passed=False, turns=1):
    case = EvalCase(
        "synthetic", "", [{"user": f"question {i}"} for i in range(turns)], {}
    )
    monkeypatch.setattr(
        "experiments.siwc_eval.datasets.load_experiment_dataset", lambda _: [case]
    )
    monkeypatch.setattr(
        "experiments.siwc_eval.datasets.setup_experiment_dataset", lambda _: None
    )
    monkeypatch.setattr(
        "experiments.siwc_eval.answer_reference.build_answer_references",
        lambda _dataset, _case: [
            {"expected": {"rows": ["Bolt"]}} for _ in range(turns)
        ],
    )
    monkeypatch.setattr("general_manager.chat.tools.get_tool_definitions", lambda: [])
    monkeypatch.setattr(
        "general_manager.chat.system_prompt.build_system_prompt", lambda: "fixture"
    )
    captured = []

    class FakeJudge:
        requests = 0

        def __init__(self, transport):
            pass

        async def grade_turn(self, **kwargs):
            captured.append(kwargs)
            self.requests += 1
            current = (
                verdict[len(captured) - 1] if isinstance(verdict, list) else verdict
            )
            if isinstance(current, Exception):
                raise current
            return {"verdict": current, "reason": "Fixture assertion.", "requests": 1}

    monkeypatch.setattr("experiments.siwc_eval.answer_judge.AnswerJudge", FakeJudge)

    async def run_case(provider, case, definitions, **kwargs):
        assert kwargs["max_tool_iterations"] == 16
        kwargs["trace_writer"].write_case(
            {
                "case": case.name,
                "conversation": case.conversation,
                "passed": legacy_passed,
                "turns": [
                    {"answer": f"answer {i}", "tool_calls": [], "tool_results": []}
                    for i in range(turns)
                ],
                "tool_calls": [],
                "tool_results": [],
            }
        )
        return EvalResult(
            case=case, result_set_score=ResultSetScore(legacy_passed, "legacy")
        )

    monkeypatch.setattr("general_manager.chat.evals.runner.run_case", run_case)
    return captured


def evaluate(tmp_path):
    report = {"results": [], "completed": False}
    asyncio.run(
        evaluate_suite(
            MediumTransport("synthetic"),
            ("basic_queries",),
            report,
            tmp_path / "report.json",
        )
    )
    return report


def test_correct_answer_passes_despite_legacy_retrieval_failure(monkeypatch, tmp_path):
    captured = configure_case(monkeypatch)
    report = evaluate(tmp_path)
    row = report["results"][0]
    assert report["completed"]
    assert row["passed"] is True
    assert row["outcome"] == "passed"
    assert row["legacy_diagnostics"]["passed"] is False
    assert row["judge_requests"] == 1
    assert captured[0]["answer"] == "answer 0"
    artifact = json.loads((tmp_path / row["artifact"]).read_text())
    assert artifact["legacy_trace"]["turns"][0]["answer"] == "answer 0"
    assert artifact["references"][0]["expected"]["rows"] == ["Bolt"]
    assert artifact["answer_assessment"]["passed"] is True


def test_incorrect_answer_fails_even_if_old_keyword_checks_pass(monkeypatch, tmp_path):
    configure_case(monkeypatch, verdict="incorrect", legacy_passed=True)
    row = evaluate(tmp_path)["results"][0]
    assert row["passed"] is False
    assert row["outcome"] == "quality_failure"
    assert row["legacy_diagnostics"]["passed"] is True


def test_followups_are_judged_separately_without_previous_answers(
    monkeypatch, tmp_path
):
    captured = configure_case(monkeypatch, turns=2)
    row = evaluate(tmp_path)["results"][0]
    assert row["passed"] is True
    assert len(captured) == len(row["turns"]) == 2
    assert captured[0]["questions"] == ["question 0"]
    assert captured[1]["questions"] == ["question 0", "question 1"]
    assert captured[1]["answer"] == "answer 1"
    assert "answer 0" not in json.dumps(captured[1])


@pytest.mark.parametrize(
    "verdicts", [["incorrect", "correct"], ["correct", "incorrect"]]
)
def test_one_correct_turn_cannot_mask_another_wrong_turn(
    monkeypatch, tmp_path, verdicts
):
    configure_case(monkeypatch, turns=2, verdict=verdicts, legacy_passed=True)
    row = evaluate(tmp_path)["results"][0]
    assert row["passed"] is False
    assert [turn["verdict"] for turn in row["turns"]] == verdicts


@pytest.mark.parametrize("verdict", ["ungradable", EvalError("http_429")])
def test_judge_failure_is_unscored_and_preserves_actual_answer(
    monkeypatch, tmp_path, verdict
):
    configure_case(monkeypatch, verdict=verdict)
    report = evaluate(tmp_path)
    row = report["results"][0]
    assert row["passed"] is None
    assert row["answer_passed"] is None
    assert row["outcome"] == "judge_error"
    assert not report["completed"] and report["stopped"]
    assert row["judge_requests"] == 1
    artifact = json.loads((tmp_path / row["artifact"]).read_text())
    assert artifact["legacy_trace"]["turns"][0]["answer"] == "answer 0"


def test_missing_reference_stops_before_candidate_inference(monkeypatch, tmp_path):
    configure_case(monkeypatch)

    def missing(dataset, case):
        raise EvalError("answer_reference_missing")

    monkeypatch.setattr(
        "experiments.siwc_eval.answer_reference.build_answer_references", missing
    )

    async def never_called(*args, **kwargs):
        pytest.fail("missing ground truth must be found before live inference")

    monkeypatch.setattr("general_manager.chat.evals.runner.run_case", never_called)
    with pytest.raises(EvalError, match="answer_reference_missing"):
        evaluate(tmp_path)


def test_second_turn_transport_failure_keeps_first_completed_answer(
    monkeypatch, tmp_path
):
    from general_manager.chat.evals.runner import run_case

    captured = configure_case(monkeypatch, turns=2)
    monkeypatch.setattr("general_manager.chat.evals.runner.run_case", run_case)

    class InterruptedTransport:
        calls = 0

        async def stream(self, body):
            self.calls += 1
            if self.calls == 2:
                raise EvalError("http_429")
            answer = "Bolt, Bearing and Gear."
            yield {"type": "response.output_text.delta", "delta": answer}
            yield {
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": answer}],
                        }
                    ],
                },
            }

    report = {"results": [], "completed": False}
    asyncio.run(
        evaluate_suite(
            InterruptedTransport(), ("basic_queries",), report, tmp_path / "report.json"
        )
    )
    row = report["results"][0]
    assert not report["completed"]
    assert row["passed"] is None and row["outcome"] == "infrastructure_error"
    assert row["requests"] == 2 and row["judge_requests"] == 0
    assert captured == []
    artifact = json.loads((tmp_path / row["artifact"]).read_text())
    messages = artifact["partial_transcript"]["messages"]
    assert any(
        m["role"] == "assistant" and m["content"] == "Bolt, Bearing and Gear."
        for m in messages
    )
    assert messages[-1]["role"] == "user" and messages[-1]["content"] == "question 1"
