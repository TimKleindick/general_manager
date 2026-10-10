"""Run only built-in synthetic cases; never import a company's Django settings."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
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


class ReplayTransport:
    """Synthetic wire fixture, not a model quality baseline."""

    def __init__(self, steel: bool) -> None:
        self.steel = steel
        self.calls = 0

    async def stream(
        self, body: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        self.calls += 1
        output: list[dict[str, Any]]
        if self.calls == 1:
            args: dict[str, Any] = {"manager": "MaterialManager", "fields": ["name"]}
            if self.steel:
                args["filters"] = {"name": "Steel"}
            output = [
                {
                    "type": "function_call",
                    "call_id": "synthetic_call",
                    "namespace": "gm",
                    "name": "query",
                    "arguments": json.dumps(args),
                }
            ]
        else:
            answer = "Steel" if self.steel else "Steel, Aluminum, Cobalt"
            yield {"type": "response.output_text.delta", "delta": answer}
            output = [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": answer, "annotations": []}
                    ],
                }
            ]
        yield {
            "type": "response.completed",
            "response": {
                "output": output,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        }


class NeutralFixture:
    """Independent fixture exercising the harness's neutral provider interface."""

    def __init__(self, steel: bool, *, wrong: bool = False) -> None:
        self.steel = steel
        self.wrong = wrong
        self.requests = 0

    async def complete(self, messages: Any, tools: Any) -> AsyncIterator[Any]:
        from general_manager.chat.providers.base import (
            ToolCallEvent,
            TextChunkEvent,
            DoneEvent,
            TokenUsage,
        )

        self.requests += 1
        if self.requests == 1:
            args: dict[str, Any] = {"manager": "MaterialManager", "fields": ["name"]}
            if self.steel:
                args["filters"] = {"name": "Steel"}
            yield ToolCallEvent("fixture_call", "query", args)
        else:
            answer = "Steel" if self.steel else "Steel, Aluminum, Cobalt"
            yield TextChunkEvent("unrelated answer" if self.wrong else answer)
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
        result = await run_case(provider, case, definitions)
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
            ("responses-replay", Provider("offline-fixture", ReplayTransport(steel))),
            ("negative-control", NeutralFixture(steel, wrong=True)),
        ]:
            rows.append(await evaluate(provider, case, label))
    return {"mode": "offline", "quality_comparison": False, "results": rows}


def confirm(message: str) -> None:
    if input(message + "\nZur Bestätigung exakt JA eingeben: ") != "JA":
        raise EvalError("user_declined")


def live(case_index: int) -> dict[str, Any]:
    from .provider import Provider
    from .auth import login
    from .http import API, LiveTransport, json_request

    confirm(
        "Nur auf deinem eigenen Rechner ausführen. ChatGPT-Anmeldung und Registrierung erlauben? "
        "Host-/Kontozuordnung wird lokal gespeichert; Tokens bleiben nur im Speicher. "
        "Keine Firmendaten verwenden."
    )
    session = login()
    transport = None
    try:
        catalog = json_request(API + "/models", token=session.access_token())
        models = [
            item["slug"]
            for item in catalog.get("models", [])
            if item.get("visibility") == "list"
            and isinstance(item.get("slug"), str)
            and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,100}", item["slug"])
        ]
        if not models:
            raise EvalError("no_eligible_models")
        print("Verfügbare Modell-Slugs:")
        for index, slug in enumerate(models, 1):
            print(f"{index}: {slug}")
        selected = int(input("Modellnummer: ")) - 1
        if not 0 <= selected < len(models):
            raise EvalError("invalid_model_selection")
        model = models[selected]
        confirm(
            f"Ein synthetischer Fall, maximal 3 Inferenz-Requests mit {model}. "
            "Verbraucht ChatGPT-Plan-Kontingent; Limits vorher in ChatGPT Settings prüfen. Starten?"
        )
        transport = LiveTransport(session.access_token())
        provider = Provider(model, transport, max_requests=3)
        result = asyncio.run(evaluate(provider, cases()[case_index], "siwc"))
        return {
            "mode": "live",
            "model": model,
            "quality_comparison": False,
            "settings": {
                "store": False,
                "stream": True,
                "automatic_retries": 0,
                "max_requests": 3,
                "unsupported_sampling_parameters": "omitted",
            },
            "results": [result],
        }
    finally:
        if transport:
            transport.close()
        if not session.logout():
            print(
                "Lokale Tokens verworfen; Remote-Widerruf nicht bestätigt. App in ChatGPT Settings trennen."
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicit local interactive OAuth and one synthetic case",
    )
    parser.add_argument("--case", choices=["all", "steel"], default="all")
    args = parser.parse_args()
    try:
        configure()
        report = (
            live(0 if args.case == "all" else 1)
            if args.live
            else asyncio.run(offline())
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        if args.live:
            return 0 if report["results"][0]["passed"] else 1
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
