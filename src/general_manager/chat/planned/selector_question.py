"""Immutable grounded selector questions and their persisted source witnesses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, NoReturn, cast

from general_manager.chat.planned.evidence import EvidenceRecord
from general_manager.chat.planned.selector_clarification import render_selector


def _digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _invalid() -> NoReturn:
    raise ValueError("invalid grounded selector question metadata")  # noqa: TRY003


@dataclass(frozen=True)
class SelectorQuestion:
    """A question, never business-result evidence or completed requirements."""

    answer: str
    evidence_ids: tuple[str, ...]
    metadata_json: str

    def as_metadata(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.metadata_json))


def selector_question(
    user_text: str, value: dict[str, Any], record: EvidenceRecord
) -> SelectorQuestion:
    answer, evidence_ids = render_selector(value, (record,))
    source = {
        "evidence_id": record.evidence_id,
        "task_id": record.task_id,
        "kind": record.kind,
        "call_identity": record.call_identity,
        "provenance": dict(record.provenance),
        "payload": record.payload(),
    }
    entry = {"selector": value, "source": source, "source_sha256": _digest(source)}
    metadata = {
        "gm_clarification": {
            "version": 2,
            "kind": "record_selector",
            "scope_user_text": user_text,
            "question_sha256": _digest(answer),
            "questions": [entry],
        }
    }
    return SelectorQuestion(
        answer,
        evidence_ids,
        json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def combine_questions(
    questions: Sequence[SelectorQuestion], user_text: str
) -> SelectorQuestion | None:
    if not questions:
        return None
    answer = "\n".join(question.answer for question in questions)
    entries = [
        entry
        for question in questions
        for entry in question.as_metadata()["gm_clarification"]["questions"]
    ]
    ids = tuple(eid for question in questions for eid in question.evidence_ids)
    metadata = {
        "gm_clarification": {
            "version": 2,
            "kind": "record_selector",
            "scope_user_text": user_text,
            "question_sha256": _digest(answer),
            "questions": entries,
        }
    }
    return SelectorQuestion(
        answer,
        ids,
        json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
    )


def verify_selector_metadata(metadata: object, text: str, user_text: str) -> str:
    """Re-render every persisted query witness; return its exact source binding."""
    if not isinstance(metadata, Mapping) or set(metadata) != {"gm_clarification"}:
        _invalid()
    value = metadata["gm_clarification"]
    if (
        not isinstance(value, Mapping)
        or set(value)
        != {"version", "kind", "scope_user_text", "question_sha256", "questions"}
        or type(value["version"]) is not int
        or value["version"] != 2
        or value["kind"] != "record_selector"
        or value["scope_user_text"] != user_text
        or value["question_sha256"] != _digest(text)
        or not isinstance(value["questions"], list)
        or not 1 <= len(value["questions"]) <= 6
    ):
        _invalid()
    answers = []
    sources = []
    for entry in value["questions"]:
        if not isinstance(entry, Mapping) or set(entry) != {
            "selector",
            "source",
            "source_sha256",
        }:
            _invalid()
        source = entry["source"]
        if (
            not isinstance(source, Mapping)
            or set(source)
            != {
                "evidence_id",
                "task_id",
                "kind",
                "call_identity",
                "provenance",
                "payload",
            }
            or entry["source_sha256"] != _digest(source)
        ):
            _invalid()
        record = EvidenceRecord.create(
            source["evidence_id"],
            source["task_id"],
            source["kind"],
            source["call_identity"],
            source["provenance"],
            source["payload"],
        )
        rebuilt = {
            "evidence_id": record.evidence_id,
            "task_id": record.task_id,
            "kind": record.kind,
            "call_identity": record.call_identity,
            "provenance": dict(record.provenance),
            "payload": record.payload(),
        }
        if _digest(rebuilt) != _digest(source):
            _invalid()
        answer, _ = render_selector(entry["selector"], (record,))
        answers.append(answer)
        sources.append(entry)
    if "\n".join(answers) != text:
        _invalid()
    return _digest(sources)
