"""Primary answer assessment and diagnostic artifacts for the SIWC suite."""

from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from general_manager.chat.evals.runner import EvalCase
from general_manager.chat.evals.traces import EvalTraceWriter
from general_manager.chat.providers.base import (
    ChatEvent,
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
    ToolDefinition,
)

from . import answer_judge, answer_reference, datasets
from .errors import EvalError
from .provider import Provider, Transport
from .suite import (
    EVAL_REVISION,
    REQUESTS_PER_TURN,
    budget_for_case,
    save_report,
    score_summary,
)


class _Capture(EvalTraceWriter):
    """Capture the existing runner's transcript without changing its messages."""

    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None

    def write_case(self, payload: dict[str, Any]) -> None:
        self.payload = deepcopy(payload)


class _RecordingProvider(Provider):
    """Keep neutral messages on failures; never persist wire/auth/reasoning data."""

    def __init__(
        self,
        model: str,
        transport: Transport,
        *,
        max_requests: int,
        artifact: dict[str, Any],
        path: Path,
    ) -> None:
        super().__init__(model, transport, max_requests=max_requests)
        self.artifact = artifact
        self.path = path

    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[ChatEvent]:
        partial: dict[str, Any] = {
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "tool_call_id": message.tool_call_id,
                    "tool_name": message.tool_name,
                    "tool_calls": [
                        {"id": call.id, "name": call.name, "args": call.args}
                        for call in message.tool_calls
                    ],
                }
                for message in messages
            ],
            "assistant_reply": {"content": "", "tool_calls": []},
            "response_completed": False,
            "requests": self.requests,
        }
        self.artifact["partial_transcript"] = partial
        save_report(self.path, self.artifact)
        try:
            async for event in super().complete(messages, tools):
                if isinstance(event, TextChunkEvent):
                    partial["assistant_reply"]["content"] += event.content
                if isinstance(event, ToolCallEvent):
                    partial["assistant_reply"]["tool_calls"].append(
                        {"id": event.id, "name": event.name, "args": event.args}
                    )
                if isinstance(event, DoneEvent):
                    partial["response_completed"] = True
                yield event
        finally:
            partial["requests"] = self.requests
            save_report(self.path, self.artifact)


def _prepare_cases(
    names: tuple[str, ...],
) -> list[tuple[str, EvalCase, list[dict[str, Any]]]]:
    """Validate every reference before the first candidate or judge request."""
    prepared = []
    for dataset in names:
        datasets.setup_experiment_dataset(dataset)
        for case in datasets.load_experiment_dataset(dataset):
            try:
                references = answer_reference.build_answer_references(dataset, case)
            except answer_reference.AnswerReferenceError:
                raise EvalError("answer_reference_error") from None
            count = sum(bool(turn.get("user")) for turn in case.conversation)
            if (
                len(references) != count
                or not references
                or any(not isinstance(item, dict) or not item for item in references)
            ):
                raise EvalError("answer_reference_error")
            budget_for_case(case)
            prepared.append((dataset, case, references))
    return prepared


async def _grade_turns(
    judge: answer_judge.AnswerJudge,
    case: EvalCase,
    trace: dict[str, Any],
    references: list[dict[str, Any]],
    row: dict[str, Any],
    artifact: dict[str, Any],
    artifact_path: Path,
) -> None:
    questions = [turn["user"] for turn in case.conversation if turn.get("user")]
    turns = trace.get("turns")
    if not isinstance(turns, list) or len(turns) != len(questions):
        raise EvalError("missing_candidate_turn_trace")
    for index, (turn, reference) in enumerate(zip(turns, references, strict=True)):
        if not isinstance(turn.get("answer"), str):
            raise EvalError("missing_candidate_turn_trace")
        verdict = await judge.grade_turn(
            questions=questions[: index + 1],
            answer=turn["answer"],
            reference=reference,
        )
        row["turns"].append({"turn": index + 1, **verdict})
        artifact["answer_assessment"] = {
            "passed": None,
            "turns": row["turns"],
            "status": "pending",
        }
        save_report(artifact_path, artifact)
        if verdict["verdict"] == "ungradable":
            raise EvalError("judge_ungradable")
    row["answer_passed"] = all(t["verdict"] == "correct" for t in row["turns"])
    row["passed"] = row["answer_passed"]
    row["outcome"] = "passed" if row["passed"] else "quality_failure"


async def _evaluate_case(
    transport: Transport,
    judge: answer_judge.AnswerJudge,
    dataset: str,
    case: EvalCase,
    references: list[dict[str, Any]],
    path: Path,
    model: str,
) -> dict[str, Any]:
    from general_manager.chat.evals.runner import run_case
    from general_manager.chat.system_prompt import build_system_prompt
    from general_manager.chat.tools import get_tool_definitions

    datasets.setup_experiment_dataset(dataset)
    definitions = [tool for tool in get_tool_definitions() if tool["name"] != "mutate"]
    prompt = build_system_prompt()
    max_requests = budget_for_case(case)
    capture = _Capture()
    artifact_path = path.parent / f"{path.stem}-traces" / f"{dataset}--{case.name}.json"
    row: dict[str, Any] = {
        "dataset": dataset,
        "case": case.name,
        "fixture_sha256": datasets.fixture_fingerprint(dataset),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "case_sha256": hashlib.sha256(
            json.dumps(
                {
                    "conversation": case.conversation,
                    "expectations": case.expectations,
                    "tools": definitions,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest(),
        "behavior_sha256": answer_judge.fingerprint(
            {
                "conversation": case.conversation,
                "prompt": prompt,
                "tools": definitions,
                "fixture": datasets.fixture_fingerprint(dataset),
            }
        ),
        "reference_sha256": answer_judge.fingerprint(references),
        "max_requests_per_case": max_requests,
        "artifact": str(artifact_path.relative_to(path.parent)),
        "passed": None,
        "answer_passed": None,
        "outcome": "pending",
        "turns": [],
        "error": None,
    }
    artifact = {
        "eval_revision": EVAL_REVISION,
        "model": model,
        "dataset": dataset,
        "case": case.name,
        "judge": answer_judge.judge_metadata(),
        "reference_sha256": row["reference_sha256"],
        "references": references,
        "candidate_input": {
            "system_prompt": prompt,
            "tools": definitions,
            "conversation": case.conversation,
        },
        "legacy_trace": None,
        "answer_assessment": {"passed": None, "turns": [], "status": "pending"},
    }
    save_report(artifact_path, artifact)
    provider = _RecordingProvider(
        model,
        transport,
        max_requests=max_requests,
        artifact=artifact,
        path=artifact_path,
    )
    before = judge.requests
    phase = "candidate"
    try:
        result = await run_case(
            provider,
            case,
            definitions,
            trace_writer=capture,
            run_metadata={
                "model": model,
                "dataset": dataset,
                "score_authority": "legacy_diagnostics_only",
                "eval_revision": EVAL_REVISION,
            },
            max_tool_iterations=REQUESTS_PER_TURN,
        )
        row["legacy_diagnostics"] = {
            **score_summary(result),
            "turns": [score_summary(turn) for turn in result.turn_results],
        }
        artifact["legacy_trace"] = capture.payload
        save_report(artifact_path, artifact)
        if result.error:
            row["error"] = row["legacy_diagnostics"]["error"]
            row["outcome"] = (
                "budget_exhausted"
                if result.error == "turn_request_budget_exhausted"
                else "harness_error"
            )
        elif capture.payload is None:
            row["error"] = "missing_candidate_trace"
            row["outcome"] = "harness_error"
        else:
            phase = "judge"
            await _grade_turns(
                judge, case, capture.payload, references, row, artifact, artifact_path
            )
    except EvalError as error:
        row["error"] = str(error)
        row["diagnostic"] = error.diagnostic
        row["outcome"] = (
            "judge_error"
            if phase == "judge"
            else "budget_exhausted"
            if str(error) == "request_budget_exhausted"
            else "infrastructure_error"
        )
    finally:
        row["requests"] = provider.requests
        row["judge_requests"] = judge.requests - before
        artifact["answer_assessment"] = {
            "passed": row["answer_passed"],
            "turns": row["turns"],
            "status": row["outcome"],
            "error": row["error"],
            "judge_requests": row["judge_requests"],
        }
        save_report(artifact_path, artifact)
    return row


async def evaluate_answer_suite(
    transport: Transport,
    names: tuple[str, ...],
    report: dict[str, Any],
    path: Path,
    model: str,
) -> None:
    prepared = _prepare_cases(names)
    judge = answer_judge.AnswerJudge(transport)
    report["eval_revision"] = EVAL_REVISION
    report["primary_metric"] = "per_turn_answer_correctness"
    report["judge"] = answer_judge.judge_metadata()
    report["judge_rubric"] = answer_judge.JUDGE_PROMPT
    report["legacy_scores_are_diagnostic"] = True
    for dataset, case, references in prepared:
        print(f"START {dataset}/{case.name}", flush=True)
        row = await _evaluate_case(
            transport, judge, dataset, case, references, path, model
        )
        report["results"].append(row)
        if row["outcome"] in {"infrastructure_error", "harness_error", "judge_error"}:
            report["stopped"] = row["error"]
        save_report(path, report)
        print(
            f"END {case.name}: {row['outcome']} "
            f"candidate_requests={row['requests']} judge_requests={row['judge_requests']}",
            flush=True,
        )
        if report.get("stopped"):
            return
    report["completed"] = True
