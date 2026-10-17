"""Explicit ChatGPT-plan evaluation of the shipped synthetic legacy datasets."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .datasets import DATASETS
from .errors import EvalError
from .http import API, LiveTransport, json_request
from .runner import configure

MODEL = "gpt-6.1-sol"

REQUESTS_PER_TURN = 16
EVAL_REVISION = "answer-correctness-v3"


def budget_for_case(case: Any) -> int:
    turns = sum(bool(turn.get("user")) for turn in case.conversation)
    if not 1 <= turns <= 16:
        raise EvalError("invalid_case_turn_count")
    return REQUESTS_PER_TURN * turns


def score_summary(result: Any) -> dict[str, Any]:
    scores = {
        name: None if (score := getattr(result, name)) is None else score.passed
        for name in (
            "contract_score",
            "tool_score",
            "result_score",
            "answer_score",
            "result_set_score",
        )
    }
    return {
        "passed": result.passed,
        "scores": scores,
        "requests": result.requests,
        "error": sanitized_error(result.error),
        "result_set_reason": None
        if result.result_set_score is None
        else result.result_set_score.reason,
        "contract_violations": []
        if result.contract_score is None
        else result.contract_score.violations,
    }


def sanitized_error(error: str | None) -> str | None:
    allowed = {
        None,
        "turn_request_budget_exhausted",
        "invalid_or_missing_turn_expectations",
        "invalid_result_set_expectation",
        "missing_turn_answer",
    }
    return error if error in allowed else "harness_error"


class MediumTransport(LiveTransport):
    """Pin the requested reasoning effort on every request, including continuations."""

    async def stream(
        self, body: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        async with aclosing(
            super().stream({**body, "reasoning": {"effort": "medium"}})
        ) as events:
            async for event in events:
                yield event


def select_model(catalog: dict[str, Any], model: str = MODEL) -> str:
    if any(
        item.get("slug") == model and item.get("visibility") == "list"
        for item in catalog.get("models", [])
    ):
        return model
    raise EvalError("requested_model_not_available")


def save_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


async def evaluate_suite(
    transport: MediumTransport,
    datasets: tuple[str, ...],
    report: dict[str, Any],
    path: Path,
    model: str = MODEL,
) -> None:
    from .answer_scoring import evaluate_answer_suite

    await evaluate_answer_suite(transport, datasets, report, path, model)


def main() -> int:
    from .auth import login
    from .answer_judge import JUDGE_MODEL

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["large_schema", "all"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    datasets = DATASETS if args.dataset == "all" else ("large_schema",)
    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "reasoning_effort": "medium",
        "datasets": datasets,
        "max_requests_per_turn": REQUESTS_PER_TURN,
        "eval_revision": EVAL_REVISION,
        "history": "full_tool_exchanges",
        "automatic_retries": 0,
        "strategy": "legacy",
        "mutations_enabled": False,
        "completed": False,
        "results": [],
    }
    session = None
    transport = None
    try:
        configure()
        session = login()
        catalog = json_request(API + "/models", token=session.access_token())
        select_model(catalog)
        select_model(catalog, JUDGE_MODEL)
        print(
            "LOGIN_OK; requested model available; starting authorized suite.",
            flush=True,
        )
        transport = MediumTransport(session.access_token())
        asyncio.run(evaluate_suite(transport, datasets, report, args.output))
    except EvalError as error:
        report["stopped"] = str(error)
        report["diagnostic"] = error.diagnostic
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- no credential-bearing tracebacks
        report["stopped"] = "local_eval_stopped"
    finally:
        if transport is not None:
            transport.close()
        if session is not None:
            report["logout_confirmed"] = session.logout()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_report(args.output, report)
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "results"},
            sort_keys=True,
        )
    )
    return (
        0
        if report["completed"] and all(row["passed"] for row in report["results"])
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
