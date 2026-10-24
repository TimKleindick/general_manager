"""Run only built-in synthetic cases; never import a company's Django settings."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from .errors import EvalError


def configure() -> None:
    from django.conf import settings

    if settings.configured:
        raise EvalError("run_in_fresh_process_without_project_settings")
    settings.configure(
        SECRET_KEY=secrets.token_hex(32),
        INSTALLED_APPS=[],
        GENERAL_MANAGER={"CHAT": {"enabled": True}},
    )
    import django

    django.setup()


def cases() -> list[Any]:
    from general_manager.chat.evals.runner import EvalCase

    return [
        EvalCase(
            name="synthetic_" + name,
            description="Synthetic toy MaterialManager query",
            conversation=[{"user": prompt}],
            expectations={
                "tool_calls": [
                    {"name": "query", "args_contain": {"manager": "MaterialManager"}}
                ],
                "results_contain": expected,
                "answer_contains": expected,
                "answer_excludes": excluded,
            },
        )
        for name, prompt, expected, excluded in [
            (
                "all",
                "List all synthetic materials with their names.",
                ["Steel", "Aluminum", "Cobalt"],
                [],
            ),
            (
                "steel",
                "Find the synthetic material named Steel.",
                ["Steel"],
                ["Aluminum", "Cobalt"],
            ),
        ]
    ]


def synthetic_steps(
    *,
    manager: str = "MaterialManager",
    answer: str,
    filters: dict[str, Any] | None = None,
) -> list[Any]:
    """Offline neutral responses for the real planner/executor/synthesizer protocol."""
    from general_manager.chat.providers.base import TextChunkEvent, ToolCallEvent

    plan = {
        "intent": "read",
        "tasks": [
            {
                "task_id": "task_1",
                "objective": "Read " + manager,
                "depends_on": [],
                "requirements": [
                    {
                        "requirement_id": "records",
                        "kind": "query",
                        "description": "Requested names",
                        "operation": None,
                    }
                ],
                "completion_criteria": ["records"],
                "routing_features": [],
            }
        ],
    }
    args: dict[str, Any] = {"manager": manager, "fields": ["name"]}
    if filters:
        args["filters"] = filters
    return [
        TextChunkEvent(json.dumps(plan)),
        ToolCallEvent(
            "schema", "get_manager_schema", {"manager": manager, "view": "full"}
        ),
        ToolCallEvent("query", "query", args),
        TextChunkEvent(
            json.dumps({"action": "complete", "evidence_ids": ["task_1:query:1"]})
        ),
        TextChunkEvent(
            json.dumps({"answer": answer, "evidence_ids": ["task_1:query:1"]})
        ),
    ]


def synthetic_wire(step: Any) -> list[dict[str, Any]]:
    """Encode one offline response; this never executes an application tool."""
    from general_manager.chat.providers.base import ToolCallEvent

    if isinstance(step, ToolCallEvent):
        return [
            {
                "type": "function_call",
                "call_id": step.id,
                "namespace": "gm",
                "name": step.name,
                "arguments": json.dumps(step.args),
            }
        ]
    return [
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": step.content, "annotations": []}
            ],
        }
    ]


class ReplayTransport:
    """Synthetic wire fixture for current orchestration, never a quality baseline."""

    def __init__(self, steel: bool) -> None:
        self.calls = 0
        self.steps = synthetic_steps(
            answer="Steel" if steel else "Steel, Aluminum, Cobalt",
            filters={"name": "Steel"} if steel else None,
        )

    async def stream(
        self, body: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        step = self.steps[self.calls]
        self.calls += 1
        yield {
            "type": "response.completed",
            "response": {
                "output": synthetic_wire(step),
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }


class NeutralFixture:
    """Independent neutral fixture driving the same production execution path."""

    def __init__(self, steel: bool, *, wrong: bool = False) -> None:
        self.requests = 0
        self.steps = synthetic_steps(
            answer="unrelated answer"
            if wrong
            else ("Steel" if steel else "Steel, Aluminum, Cobalt"),
            filters={"name": "Steel"} if steel else None,
        )

    async def complete(self, messages: Any, tools: Any) -> AsyncIterator[Any]:
        from general_manager.chat.providers.base import DoneEvent, TokenUsage

        step = self.steps[self.requests]
        self.requests += 1
        yield step
        yield DoneEvent(TokenUsage())


async def evaluate(provider: Any, case: Any, label: str) -> dict[str, Any]:
    from general_manager.chat.evals.fixtures import setup_toy_schema
    from general_manager.chat.evals.runner import run_case
    from general_manager.chat.tools import get_tool_definitions

    setup_toy_schema()
    definitions = [tool for tool in get_tool_definitions() if tool["name"] != "mutate"]
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "conversation": case.conversation,
                "expectations": case.expectations,
                "tools": definitions,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    details = {}
    try:
        result = await run_case(provider, case)
        passed = result.passed
        error = "harness_error" if result.error else None
    except EvalError as failure:
        passed, error = False, str(failure)
        details = failure.diagnostic
    # Do not emit answers, tool args/results, trace payloads or remote exceptions.
    return {
        "case": case.name,
        "provider": label,
        "passed": passed,
        "requests": provider.requests,
        "error": error,
        "fixture_sha256": fingerprint,
        "diagnostic": details,
    }


async def offline() -> dict[str, Any]:
    from .provider import Provider

    rows = []
    for case in cases():
        steel = case.name.endswith("steel")
        for label, provider in [
            ("neutral-fixture", NeutralFixture(steel)),
            (
                "responses-replay",
                Provider("offline-fixture", ReplayTransport(steel), max_requests=5),
            ),
            ("negative-control", NeutralFixture(steel, wrong=True)),
        ]:
            rows.append(await evaluate(provider, case, label))
    return {"mode": "offline", "quality_comparison": False, "results": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--case", choices=["all", "steel"], default=None, help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if args.live or args.case is not None:
        parser.error(
            "The legacy standalone live evaluator was removed; use the active "
            "experiments.gm_eval harness (see experiments/gm_eval/README.md)."
        )
    try:
        configure()
        report = asyncio.run(offline())
        print(json.dumps(report, indent=2, sort_keys=True))
        return (
            0
            if all(
                row["passed"] == (row["provider"] != "negative-control")
                for row in report["results"]
            )
            else 1
        )
    except EvalError as failure:
        print(json.dumps({"error": str(failure), "diagnostic": failure.diagnostic}))
        return 2
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- final no-secret boundary
        # No traceback: SDK/HTTP/OAuth exceptions can contain credentials or data.
        print(
            '{"error":"local_eval_stopped","detail":"Check local setup, consent, account eligibility and preview restrictions."}'
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
