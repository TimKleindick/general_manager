"""Run the three user-selected medium suites in order using a saved session."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .datasets import DATASETS
from .answer_judge import JUDGE_MODEL, judge_metadata
from .errors import EvalError
from .http import API, json_request
from .persistent_auth import PersistentSession, saved_session
from .runner import configure
from .suite import (
    EVAL_REVISION,
    REQUESTS_PER_TURN,
    MediumTransport,
    evaluate_suite,
    save_report,
    select_model,
)

MODELS = ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-luna")


class SavedTransport(MediumTransport):
    def __init__(self, session: PersistentSession) -> None:
        self.session = session

    async def stream(
        self, body: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        transport = MediumTransport(self.session.access_token())
        try:
            async with aclosing(transport.stream(body)) as events:
                async for event in events:
                    yield event
        finally:
            transport.close()

    def close(self) -> None:
        pass  # Each request clears its own transient token reference.


def main() -> int:
    root = (
        Path(__file__).parent
        / "results"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    summary: dict[str, Any] = {
        "models": list(MODELS),
        "reasoning_effort": "medium",
        "eval_revision": EVAL_REVISION,
        "primary_metric": "per_turn_answer_correctness",
        "judge": judge_metadata(),
        "runs": [],
        "completed": False,
    }
    try:
        configure()
        with saved_session() as session:
            catalog = json_request(API + "/models", token=session.access_token())
            for model in dict.fromkeys((*MODELS, JUDGE_MODEL)):
                select_model(catalog, model)
            print(
                "LOGIN_OK: Sitzung gespeichert, alle drei Modelle verfügbar.",
                flush=True,
            )
            for model in MODELS:
                report: dict[str, Any] = {
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "model": model,
                    "reasoning_effort": "medium",
                    "datasets": list(DATASETS),
                    "max_requests_per_turn": REQUESTS_PER_TURN,
                    "eval_revision": EVAL_REVISION,
                    "history": "full_tool_exchanges",
                    "automatic_retries": 0,
                    "strategy": "legacy",
                    "mutations_enabled": False,
                    "completed": False,
                    "results": [],
                }
                path = root / f"{model}-medium.json"
                print(f"MODEL_START {model}", flush=True)
                try:
                    asyncio.run(
                        evaluate_suite(
                            SavedTransport(session), DATASETS, report, path, model
                        )
                    )
                finally:
                    report["finished_at"] = datetime.now(timezone.utc).isoformat()
                    save_report(path, report)
                summary["runs"].append(
                    {
                        "model": model,
                        "report": str(path),
                        "completed": report["completed"],
                        "passed": sum(
                            row["passed"] is True for row in report["results"]
                        ),
                        "graded_cases": sum(
                            row["passed"] is not None for row in report["results"]
                        ),
                        "ungraded_cases": sum(
                            row["passed"] is None for row in report["results"]
                        ),
                        "cases": len(report["results"]),
                        "budget_exhausted": sum(
                            row.get("outcome") == "budget_exhausted"
                            for row in report["results"]
                        ),
                        "quality_failures": sum(
                            row.get("outcome") == "quality_failure"
                            for row in report["results"]
                        ),
                        "requests": sum(row["requests"] for row in report["results"]),
                        "judge_requests": sum(
                            row["judge_requests"] for row in report["results"]
                        ),
                    }
                )
                save_report(root / "comparison.json", summary)
                if report.get("stopped"):
                    summary["stopped"] = report["stopped"]
                    break
            else:
                summary["completed"] = True
    except EvalError as error:
        summary["stopped"] = str(error)
        summary["diagnostic"] = error.diagnostic
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- no secrets in traceback
        summary["stopped"] = "local_comparison_stopped"
    finally:
        save_report(root / "comparison.json", summary)
    print(f"COMPARISON_REPORT {root / 'comparison.json'}", flush=True)
    return 0 if summary["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
