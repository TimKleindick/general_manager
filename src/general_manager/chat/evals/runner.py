"""Eval runner: loads datasets, executes conversations, scores via judges."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO
from typing import Any

import yaml

from general_manager.chat.evals.judges.answer_quality import (
    AnswerQualityScore,
    judge_answer_quality,
)
from general_manager.chat.evals.judges.result_accuracy import (
    ResultAccuracyScore,
    judge_result_accuracy,
)
from general_manager.chat.evals.judges.tool_sequence import (
    ToolSequenceScore,
    judge_tool_sequence,
)
from general_manager.chat.providers.base import (
    DoneEvent,
    Message,
    TextChunkEvent,
    ToolCallEvent,
    ToolDefinition,
)
from general_manager.chat.system_prompt import build_system_prompt
from general_manager.chat.tools import execute_chat_tool, get_tool_definitions

DATASETS_DIR = Path(__file__).parent / "datasets"

MAX_TOOL_ITERATIONS = 10


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    name: str
    description: str
    conversation: list[dict[str, str]]
    expectations: dict[str, Any]


@dataclass
class EvalResult:
    case: EvalCase
    tool_score: ToolSequenceScore | None = None
    result_score: ResultAccuracyScore | None = None
    answer_score: AnswerQualityScore | None = None
    error: str | None = None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        for score in (self.tool_score, self.result_score, self.answer_score):
            if score is not None and not score.passed:
                return False
        return True


@dataclass
class TurnRecord:
    """Records collected during one conversation turn."""

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    answer_chunks: list[str] = field(default_factory=list)

    @property
    def answer(self) -> str:
        return "".join(self.answer_chunks)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def list_datasets() -> list[str]:
    """Return available dataset names (without .yaml extension)."""
    return sorted(p.stem for p in DATASETS_DIR.glob("*.yaml"))


def load_dataset(name: str) -> list[EvalCase]:
    """Load eval cases from a YAML dataset file."""
    path = DATASETS_DIR / f"{name}.yaml"
    if not path.exists():
        msg = f"Dataset not found: {path}"
        raise FileNotFoundError(msg)
    with open(path) as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        msg = f"Dataset {name} must be a YAML list of eval cases."
        raise TypeError(msg)
    return [
        EvalCase(
            name=item["name"],
            description=item.get("description", ""),
            conversation=item["conversation"],
            expectations=item.get("expectations", {}),
        )
        for item in raw
    ]


# ---------------------------------------------------------------------------
# Provider tool loop
# ---------------------------------------------------------------------------


def _messages_to_provider(history: list[dict[str, str]]) -> list[Message]:
    return [Message(role=entry["role"], content=entry["content"]) for entry in history]


def _tool_defs_to_provider(
    tool_defs: list[dict[str, Any]],
) -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=td["name"],
            description=td.get("description", ""),
            input_schema=td.get("input_schema", {}),
        )
        for td in tool_defs
    ]


async def _run_turn(
    provider: Any,
    history: list[dict[str, str]],
    tool_defs: list[dict[str, Any]],
    stream: IO[str] | None = None,
) -> TurnRecord:
    """Execute one conversation turn through the provider + tool loop."""
    record = TurnRecord()
    messages = _messages_to_provider(history)
    tools = _tool_defs_to_provider(tool_defs)

    for _ in range(MAX_TOOL_ITERATIONS):
        tool_calls_this_round: list[ToolCallEvent] = []
        text_chunks: list[str] = []
        assistant_line_open = False

        async for event in provider.complete(messages, tools):
            if isinstance(event, TextChunkEvent):
                text_chunks.append(event.content)
                if stream is not None:
                    if not assistant_line_open:
                        stream.write("assistant: ")
                        assistant_line_open = True
                    stream.write(event.content)
                    stream.flush()
            elif isinstance(event, ToolCallEvent):
                if stream is not None and assistant_line_open:
                    stream.write("\n")
                    assistant_line_open = False
                tool_calls_this_round.append(event)
                _stream_line(
                    stream,
                    f"tool_call {event.name}: {_to_json(event.args)}",
                )
            elif isinstance(event, DoneEvent):
                pass

        if stream is not None and assistant_line_open:
            stream.write("\n")
            stream.flush()

        if text_chunks and not tool_calls_this_round:
            record.answer_chunks.extend(text_chunks)
            break

        if not tool_calls_this_round:
            break

        for tc in tool_calls_this_round:
            record.tool_calls.append({"name": tc.name, "args": tc.args})
            try:
                result = execute_chat_tool(tc.name, tc.args, None)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                result = {"error": str(exc)}
            record.tool_results.append(result)
            _stream_line(stream, f"tool_result {tc.name}: {_to_json(result)}")
            serialized = json.dumps(result, default=str)
            messages.append(
                Message(
                    role="assistant",
                    content=(
                        f"Called tool {tc.name}. The next message is the tool "
                        "result; answer from it exactly."
                    ),
                )
            )
            messages.append(Message(role="tool", content=serialized))
    else:
        record.answer_chunks.append("[max tool iterations reached]")
        _stream_line(stream, "assistant: [max tool iterations reached]")

    return record


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_case(case: EvalCase, records: list[TurnRecord]) -> EvalResult:
    """Score a completed eval case against its expectations."""
    expectations = case.expectations
    result = EvalResult(case=case)

    # Aggregate across all turns
    all_tool_calls = []
    all_tool_results = []
    all_answer_parts = []
    for rec in records:
        all_tool_calls.extend(rec.tool_calls)
        all_tool_results.extend(rec.tool_results)
        all_answer_parts.append(rec.answer)

    full_answer = "\n".join(all_answer_parts)

    # Tool sequence judge
    expected_tools = expectations.get("tool_calls")
    if expected_tools is not None:
        result.tool_score = judge_tool_sequence(expected_tools, all_tool_calls)

    # Result accuracy judge
    results_contain = expectations.get("results_contain")
    results_exclude = expectations.get("results_exclude", [])
    if results_contain is not None:
        # Flatten tool results that have a "data" key (query results)
        flat_results = []
        for tr in all_tool_results:
            if isinstance(tr, dict) and "data" in tr:
                flat_results.extend(tr["data"])
            elif isinstance(tr, dict):
                flat_results.append(tr)
        result.result_score = judge_result_accuracy(
            results_contain, results_exclude, flat_results
        )

    # Answer quality judge
    answer_contains = expectations.get("answer_contains")
    answer_excludes = expectations.get("answer_excludes", [])
    if answer_contains is not None:
        result.answer_score = judge_answer_quality(
            answer_contains, answer_excludes, full_answer
        )

    return result


# ---------------------------------------------------------------------------
# Suite execution
# ---------------------------------------------------------------------------


async def run_case(
    provider: Any,
    case: EvalCase,
    tool_defs: list[dict[str, Any]],
    stream: IO[str] | None = None,
) -> EvalResult:
    """Run a single eval case and return scored results."""
    system_prompt = build_system_prompt()
    history: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    records: list[TurnRecord] = []

    try:
        _stream_line(stream, f"=== {case.name} ===")
        if case.description:
            _stream_line(stream, f"description: {case.description}")
        for turn in case.conversation:
            user_text = turn.get("user", "")
            if user_text:
                _stream_line(stream, f"user: {user_text}")
                history.append({"role": "user", "content": user_text})
                record = await _run_turn(
                    provider, list(history), tool_defs, stream=stream
                )
                records.append(record)
                if record.answer:
                    history.append({"role": "assistant", "content": record.answer})
        if stream is not None:
            stream.write("\n")
            stream.flush()
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        return EvalResult(case=case, error=str(exc))

    return _score_case(case, records)


async def run_eval_suite(
    provider: Any,
    dataset_names: list[str] | None = None,
    stream: IO[str] | None = None,
) -> list[EvalResult]:
    """Run all (or selected) eval datasets and return results."""
    if dataset_names is None:
        dataset_names = list_datasets()

    tool_defs = get_tool_definitions()
    results: list[EvalResult] = []

    for ds_name in dataset_names:
        cases = load_dataset(ds_name)
        for case in cases:
            result = await run_case(provider, case, tool_defs, stream=stream)
            results.append(result)

    return results


def run_eval_suite_sync(
    provider: Any,
    dataset_names: list[str] | None = None,
    stream: IO[str] | None = None,
) -> list[EvalResult]:
    """Synchronous wrapper for ``run_eval_suite``."""
    return asyncio.run(run_eval_suite(provider, dataset_names, stream=stream))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_report(results: list[EvalResult], *, verbose: bool = False) -> str:
    """Format a summary report and return it as a string."""
    lines: list[str] = []
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    tool_pass = sum(
        1 for r in results if r.tool_score is not None and r.tool_score.passed
    )
    tool_total = sum(1 for r in results if r.tool_score is not None)
    result_pass = sum(
        1 for r in results if r.result_score is not None and r.result_score.passed
    )
    result_total = sum(1 for r in results if r.result_score is not None)
    answer_pass = sum(
        1 for r in results if r.answer_score is not None and r.answer_score.passed
    )
    answer_total = sum(1 for r in results if r.answer_score is not None)

    lines.append(f"{'Dimension':<20} {'Pass':>6} {'Total':>6} {'Rate':>8}")
    lines.append("-" * 42)
    lines.append(
        f"{'Tool selection':<20} {tool_pass:>6} {tool_total:>6} {_pct(tool_pass, tool_total):>8}"
    )
    lines.append(
        f"{'Query correctness':<20} {result_pass:>6} {result_total:>6} {_pct(result_pass, result_total):>8}"
    )
    lines.append(
        f"{'Answer quality':<20} {answer_pass:>6} {answer_total:>6} {_pct(answer_pass, answer_total):>8}"
    )
    lines.append("-" * 42)
    lines.append(f"{'Overall':<20} {passed:>6} {total:>6} {_pct(passed, total):>8}")

    if verbose:
        failures = [r for r in results if not r.passed]
        if failures:
            lines.append("")
            lines.append("Failures:")
            for r in failures:
                lines.append(f"  {r.case.name}: {r.case.description}")
                if r.error:
                    lines.append(f"    error: {r.error}")
                if r.tool_score and not r.tool_score.passed:
                    for m in r.tool_score.mismatches:
                        lines.append(f"    tool: {m}")
                if r.result_score and not r.result_score.passed:
                    if r.result_score.missing:
                        lines.append(f"    missing results: {r.result_score.missing}")
                    if r.result_score.unexpected:
                        lines.append(
                            f"    unexpected results: {r.result_score.unexpected}"
                        )
                if r.answer_score and not r.answer_score.passed:
                    lines.append(f"    answer score: {r.answer_score.score:.0%}")
                    if r.answer_score.missing:
                        lines.append(f"    missing in answer: {r.answer_score.missing}")
                    if r.answer_score.unexpected:
                        lines.append(
                            f"    unexpected in answer: {r.answer_score.unexpected}"
                        )

    return "\n".join(lines)


def _stream_line(stream: IO[str] | None, line: str) -> None:
    """Write one formatted line to the streaming output."""
    if stream is None:
        return
    stream.write(f"{line}\n")
    stream.flush()


def _to_json(payload: Any) -> str:
    """Serialize a payload compactly for streaming logs."""
    return json.dumps(payload, default=str, sort_keys=True)


def print_compare_report(
    results_by_provider: dict[str, list[EvalResult]],
) -> str:
    """Format a side-by-side comparison table across providers."""
    providers = list(results_by_provider.keys())
    lines: list[str] = []

    header = f"{'Dimension':<20}"
    for p in providers:
        header += f" {p:>14}"
    lines.append(header)
    lines.append("-" * len(header))

    for dimension, extractor in [
        ("Tool selection", lambda r: r.tool_score),
        ("Query correctness", lambda r: r.result_score),
        ("Answer quality", lambda r: r.answer_score),
        ("Overall", None),
    ]:
        row = f"{dimension:<20}"
        for p in providers:
            results = results_by_provider[p]
            if extractor is None:
                n_pass = sum(1 for r in results if r.passed)
                n_total = len(results)
            else:
                scored = [extractor(r) for r in results if extractor(r) is not None]
                n_pass = sum(1 for s in scored if s.passed)
                n_total = len(scored)
            row += f" {_pct(n_pass, n_total):>14}"
        lines.append(row)

    return "\n".join(lines)


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "N/A"
    return f"{n / total:.0%}"
