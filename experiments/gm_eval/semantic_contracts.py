"""Strict, answer/context-bound witnesses for conversational behavior judgments."""

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

ADJUDICATION_SCHEMA_VERSION = "1.5"
RANKING_KNOWN_VALUES_ONLY = "known_values_only"
RANKING_COVERAGE = {
    RANKING_KNOWN_VALUES_ONLY: (
        "The answer explicitly limits the ranking or winner to candidates with known "
        "values and qualifies missing competitors separately."
    ),
    "all_candidates": (
        "The answer asserts an unconditional ranking or winner for the entire "
        "requested candidate population."
    ),
    "unknown": (
        "The answer explicitly leaves the ranking coverage indeterminate. "
        "An absent coverage claim instead uses null with absent support."
    ),
}


def context_digest(context: Mapping[str, Any]) -> str:
    """Bind only the actual model-visible conversation, including the current user."""
    return sha256(
        json.dumps(
            context,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def repetition_schema(base: dict[str, Any]) -> dict[str, Any]:
    """Extend a semantic judgment with its context and exact repetition witnesses."""
    schema = deepcopy(base)
    schema["required"].extend(["context_sha256", "repetitions"])
    schema["properties"].update(
        context_sha256={"type": "string"},
        repetitions={
            "type": "array",
            "uniqueItems": True,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["answer_quote", "resolved_by"],
                "properties": {
                    "answer_quote": {"type": "string"},
                    "resolved_by": {
                        "oneOf": [
                            {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["source", "index", "quote"],
                                "properties": {
                                    "source": {"const": source},
                                    "index": {"type": "number"}
                                    if source == "visible_history"
                                    else {"type": "null"},
                                    "quote": {"type": "string"},
                                },
                            }
                            for source in ("visible_history", "current_question")
                        ]
                    },
                },
            },
        },
    )
    return schema


def valid_repetition_judgment(check: object, answer: object, context: object) -> bool:
    """Validate provenance and Boolean/witness consistency, never infer semantics."""
    if (
        not isinstance(check, Mapping)
        or set(check)
        != {"passed", "reason", "evidence_ids", "context_sha256", "repetitions"}
        or type(check.get("passed")) is not bool
        or not isinstance(check.get("reason"), str)
        or not check["reason"].strip()
        or not isinstance(answer, str)
        or not isinstance(context, Mapping)
        or set(context) != {"current_question", "visible_history"}
        or not isinstance(context["current_question"], str)
        or not isinstance(context["visible_history"], list)
    ):
        return False
    history = context["visible_history"]
    if any(
        not isinstance(row, Mapping)
        or set(row) != {"role", "content"}
        or not isinstance(row["role"], str)
        or row["role"] not in {"user", "assistant", "system"}
        or not isinstance(row["content"], str)
        for row in history
    ):
        return False
    refs, witnesses = check["evidence_ids"], check["repetitions"]
    if (
        not isinstance(refs, list)
        or any(not isinstance(ref, str) for ref in refs)
        or "answer" not in refs
        or len(refs) != len(set(refs))
        or check["context_sha256"] != context_digest(context)
        or not isinstance(witnesses, list)
        or check["passed"] != (len(witnesses) == 0)
    ):
        return False
    seen = set()
    for witness in witnesses:
        if not isinstance(witness, Mapping) or set(witness) != {
            "answer_quote",
            "resolved_by",
        }:
            return False
        quote, resolved = witness["answer_quote"], witness["resolved_by"]
        if (
            not isinstance(quote, str)
            or not quote.strip()
            or quote not in answer
            or not isinstance(resolved, Mapping)
            or set(resolved) != {"source", "index", "quote"}
        ):
            return False
        index = resolved["index"]
        if resolved["source"] == "current_question" and index is None:
            text = context["current_question"]
        elif (
            resolved["source"] == "visible_history"
            and type(index) is int
            and 0 <= index < len(history)
            and history[index]["role"] == "user"
        ):
            text = history[index]["content"]
        else:
            return False
        context_quote = resolved["quote"]
        if (
            not isinstance(context_quote, str)
            or not context_quote.strip()
            or context_quote not in text
        ):
            return False
        key = (quote, resolved["source"], index, context_quote)
        if key in seen:
            return False
        seen.add(key)
    return True
