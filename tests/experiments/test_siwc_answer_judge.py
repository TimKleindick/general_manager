"""Answer scoring is isolated from candidate identity and retrieval strategy."""

import asyncio
import json

import pytest

from experiments.siwc_eval.answer_judge import (
    JUDGE_MODEL,
    JUDGE_PROMPT,
    AnswerJudge,
    parse_verdict,
)
from experiments.siwc_eval.errors import EvalError


class ReplyTransport:
    def __init__(self, text):
        self.text = text
        self.bodies = []

    async def stream(self, body):
        self.bodies.append(body)
        yield {"type": "response.output_text.delta", "delta": self.text}
        yield {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": self.text}],
                    }
                ]
            },
        }


@pytest.mark.parametrize("verdict", ["correct", "incorrect", "ungradable"])
def test_verdict_keeps_uncertainty_distinct_from_incorrect(verdict):
    result = parse_verdict(json.dumps({"verdict": verdict, "reason": "Evidence."}))
    assert result == {"verdict": verdict, "reason": "Evidence."}


@pytest.mark.parametrize(
    "text",
    [
        "true",
        "[]",
        '{"verdict":"correct"}',
        '{"verdict":true,"reason":"yes"}',
        '{"verdict":"correct","reason":""}',
        '{"verdict":"correct","reason":"yes","score":1}',
        '{"verdict":"maybe","reason":"unclear"}',
        '{"verdict":"incorrect","verdict":"correct","reason":"conflicting"}',
        "not JSON",
        '{"verdict":"correct","reason":"' + "x" * 5000 + '"}',
    ],
)
def test_invalid_judge_response_never_becomes_a_pass_or_candidate_failure(text):
    with pytest.raises(EvalError, match="judge_invalid_response"):
        parse_verdict(text)


def test_judge_gets_one_answer_and_reference_without_tools_or_model_identity():
    transport = ReplyTransport('{"verdict":"correct","reason":"Exact rows."}')
    judge = AnswerJudge(transport)
    reference = {"kind": "records", "expected": {"rows": [{"name": "Bolt"}]}}
    result = asyncio.run(
        judge.grade_turn(
            questions=["Show all parts", "Only the Steel parts"],
            answer="Only Bolt; Bearing does not match.",
            reference=reference,
        )
    )
    assert result["verdict"] == "correct"
    assert result["requests"] == 1
    assert len(transport.bodies) == 1
    body = transport.bodies[0]
    assert body["model"] == JUDGE_MODEL
    assert body["tools"] == []
    assert body["store"] is False
    assert body["input"][0]["content"] == JUDGE_PROMPT
    payload = json.loads(body["input"][1]["content"])
    assert payload == {
        "user_questions": ["Show all parts", "Only the Steel parts"],
        "candidate_answer": "Only Bolt; Bearing does not match.",
        "reference": reference,
    }


def test_every_turn_uses_a_fresh_judge_conversation():
    transport = ReplyTransport('{"verdict":"incorrect","reason":"Wrong row."}')
    judge = AnswerJudge(transport)

    async def run():
        first = await judge.grade_turn(
            questions=["first"], answer="SECRET_FIRST_ANSWER", reference={"expected": 1}
        )
        second = await judge.grade_turn(
            questions=["first", "second"],
            answer="second answer",
            reference={"expected": 2},
        )
        return first, second

    first, second = asyncio.run(run())
    assert first["requests"] == second["requests"] == 1
    assert "SECRET_FIRST_ANSWER" not in json.dumps(transport.bodies[1])


def test_empty_answer_is_incorrect_without_spending_a_judge_request():
    transport = ReplyTransport("must not be called")
    result = asyncio.run(
        AnswerJudge(transport).grade_turn(
            questions=["list parts"], answer=" \n", reference={"expected": ["Bolt"]}
        )
    )
    assert result["verdict"] == "incorrect"
    assert result["requests"] == 0
    assert transport.bodies == []


def test_transport_failure_is_not_swallowed_or_retried():
    class FailedTransport:
        calls = 0

        async def stream(self, body):
            self.calls += 1
            raise EvalError("http_429")
            yield  # pragma: no cover

    transport = FailedTransport()
    with pytest.raises(EvalError, match="http_429"):
        asyncio.run(
            AnswerJudge(transport).grade_turn(
                questions=["question"], answer="answer", reference={"expected": 1}
            )
        )
    assert transport.calls == 1


def test_entrypoint_imports_do_not_load_company_settings():
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(  # noqa: S603 -- fixed interpreter/module imports, no shell
        [
            sys.executable,
            "-c",
            "from experiments.siwc_eval import compare, calibrate_answer_judge; "
            "from django.conf import settings; assert not settings.configured",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
