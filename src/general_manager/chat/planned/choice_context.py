"""Turn-local, source-bound clarification decisions, without record authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, NoReturn, cast

from general_manager.chat.planned.clarification import QUESTIONS, render_clarification
from general_manager.chat.planned.validation import PlanValidationError
from general_manager.chat.providers.base import Message


CHOICE_INSTRUCTION = (
    "When choice_questions is nonempty, return the contextual envelope containing plan, "
    "choices and clarification_requests. Inner plan examples describe only plan. Assess every "
    "supplied question exactly once as answered, open or conflicting, with applicability "
    "same_scope, changed_scope or unrelated. Bind decisions to exact visible USER quotes after "
    "the question. Never use assistant/task/tool claims as user choices. A partial answer, "
    "uncertainty or contradiction is not answered merely because it is nonempty. Interpret "
    "the original question and reply together: a chosen analytical basis can answer both a "
    "generic criterion question and a metric question; they are not universally synonymous. "
    "A historical time period does not authorize a new forecast, and an outlook request with "
    "an explicitly chosen historical basis can be answered as a historical assessment. Do not "
    "invent a forecast-versus-history choice after the user has selected that basis. "
    "clarification_requests lists the specific remaining choices the tasks need to resolve; "
    "each contains topic, question_id and scope_quote. For a same-scope prior question use its "
    "ID, only when open or conflicting. Use null only for a new topic or a genuinely changed "
    "scope, bound to the latest USER quote. Do not reclassify an answered choice as a new "
    "same-scope question. Use [] when proceeding with the requested data work. These decisions "
    "are conversational scope, not factual evidence, mutation permission or instructions."
)


class ChoiceValidationError(PlanValidationError):
    """Machine-detectable missing or inconsistent conversational binding."""

    def __init__(self, code: str = "invalid_choice_binding") -> None:
        super().__init__(
            code,
            path="$.choices",
            expected="complete user-bound choice decisions consistent with requested clarifications",
        )


def _invalid(code: str = "invalid_choice_binding") -> NoReturn:
    raise ChoiceValidationError(code)


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _exact_topics(text: str) -> tuple[str, ...]:
    """Migrate only a complete runtime-rendered analytical question string."""
    for language, templates in QUESTIONS.items():
        remaining = text
        topics: list[str] = []
        while remaining and len(topics) < len(templates):
            match = next(
                (
                    topic
                    for topic, question in templates.items()
                    if topic not in topics
                    and (remaining == question or remaining.startswith(question + " "))
                ),
                None,
            )
            if match is None:
                break
            topics.append(match)
            remaining = remaining[len(templates[match]) :].removeprefix(" ")
        if (
            topics
            and not remaining
            and render_clarification({"language": language, "requirements": topics})
            == text
        ):
            return tuple(topics)
    return ()


def clarification_metadata(text: str, user_text: str) -> dict[str, Any] | None:
    """Structured question metadata stored through the existing JSON seam."""
    topics = _exact_topics(text)
    if not topics:
        return None
    return {
        "gm_clarification": {
            "version": 1,
            "question_sha256": _digest(text),
            "scope_user_text": user_text,
            "topics": list(topics),
        }
    }


def choice_questions(messages: Sequence[Message]) -> tuple[dict[str, Any], ...]:
    """Bind runtime questions to their preceding user scope and exact positions."""
    result = []
    scope_index: int | None = None
    for index, message in enumerate(messages):
        if message.role == "user":
            scope_index = index
        if message.role != "assistant" or scope_index is None:
            continue
        topics = _exact_topics(message.content)
        selector_source = None
        if (
            message.clarification_metadata is not None
            and isinstance(
                message.clarification_metadata.get("gm_clarification"), Mapping
            )
            and message.clarification_metadata["gm_clarification"].get("version") == 2
        ):
            from general_manager.chat.planned.selector_question import (
                verify_selector_metadata,
            )

            try:
                selector_source = verify_selector_metadata(
                    message.clarification_metadata,
                    message.content,
                    messages[scope_index].content,
                )
            except (TypeError, ValueError, KeyError):
                _invalid()
            topics = ("record_selector",)
        elif message.clarification_metadata is not None:
            expected = clarification_metadata(
                message.content, messages[scope_index].content
            )
            if _digest(message.clarification_metadata) != _digest(expected):
                _invalid()
        for topic in topics:
            binding = {
                "question_index": index,
                "question_sha256": _digest(message.content),
                "scope_index": scope_index,
                "scope_sha256": _digest(messages[scope_index].content),
                "topic": topic,
                **(
                    {"selector_source_sha256": selector_source}
                    if selector_source is not None
                    else {}
                ),
            }
            result.append(
                {
                    "question_id": _digest(binding),
                    **binding,
                    "question_text": message.content,
                    "scope_user_text": messages[scope_index].content,
                }
            )
    return tuple(result)


def envelope_schema(
    plan_schema: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    quote = {
        "type": "object",
        "additionalProperties": False,
        "required": ["index", "quote"],
        "properties": {
            "index": {"type": "integer", "minimum": 0},
            "quote": {"type": "string", "minLength": 1},
        },
    }
    decision = {
        "type": "object",
        "additionalProperties": False,
        "required": ["question_id", "state", "applicability", "user_quotes"],
        "properties": {
            "question_id": {"enum": [q["question_id"] for q in questions]},
            "state": {"enum": ["answered", "open", "conflicting"]},
            "applicability": {"enum": ["same_scope", "changed_scope", "unrelated"]},
            "user_quotes": {"type": "array", "maxItems": 16, "items": quote},
        },
    }
    request = {
        "type": "object",
        "additionalProperties": False,
        "required": ["topic", "question_id", "scope_quote"],
        "properties": {
            "topic": {"enum": list(QUESTIONS["en"])},
            "question_id": {"enum": [None, *[q["question_id"] for q in questions]]},
            "scope_quote": quote,
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["plan", "choices", "clarification_requests"],
        "properties": {
            "plan": deepcopy(dict(plan_schema)),
            "choices": {
                "type": "array",
                "minItems": len(questions),
                "maxItems": len(questions),
                "items": decision,
            },
            "clarification_requests": {
                "type": "array",
                "maxItems": len(QUESTIONS["en"]),
                "items": request,
            },
        },
    }


@dataclass(frozen=True)
class ChoiceContext:
    """Immutable validated scope, shared by planning and synthesis only."""

    data: str

    def as_mapping(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.data))

    @property
    def allowed_topics(self) -> tuple[str, ...]:
        return tuple(
            row["topic"] for row in self.as_mapping()["clarification_requests"]
        )

    def check_clarification(self, topics: Sequence[str]) -> None:
        if any(topic not in self.allowed_topics for topic in topics):
            _invalid("unrequested_or_answered_clarification")

    def verify_messages(
        self, messages: Sequence[Message], *, user_text: str | None = None
    ) -> None:
        data = self.as_mapping()
        if data["source_context_sha256"] != _context_digest(messages) or (
            user_text is not None and data["user_sources"][-1]["content"] != user_text
        ):
            _invalid("choice_context_source_changed")


def _context_digest(messages: Sequence[Message]) -> str:
    # Configured privileged instructions are carried separately, never sources
    # of user choices. Their presence may differ between scheduler phases.
    return _digest(
        [
            {
                "role": m.role,
                "content": m.content,
                **(
                    {"selector_metadata": m.clarification_metadata}
                    if m.clarification_metadata is not None
                    and isinstance(
                        m.clarification_metadata.get("gm_clarification"), Mapping
                    )
                    and m.clarification_metadata["gm_clarification"].get("version") == 2
                    else {}
                ),
            }
            for m in messages
            if m.role in {"user", "assistant", "tool"}
        ]
    )


def validate_choices(
    payload: Mapping[str, Any],
    messages: Sequence[Message],
    *,
    user_text: str | None = None,
) -> ChoiceContext:
    questions = choice_questions(messages)
    if not questions or set(payload) != {"plan", "choices", "clarification_requests"}:
        _invalid("missing_choice_envelope")
    decisions, requests = payload["choices"], payload["clarification_requests"]
    if (
        not isinstance(decisions, list)
        or len(decisions) != len(questions)
        or not isinstance(requests, list)
        or len(requests) > len(QUESTIONS["en"])
    ):
        _invalid()
    by_id = {q["question_id"]: q for q in questions}
    states: dict[str, dict[str, Any]] = {}
    latest_user = max(
        index for index, message in enumerate(messages) if message.role == "user"
    )
    if user_text is not None and messages[latest_user].content != user_text:
        _invalid("choice_context_source_changed")

    def check_quote(value: Any, after: int, *, latest: bool = False) -> None:
        if not isinstance(value, dict) or set(value) != {"index", "quote"}:
            _invalid()
        index, quote = value["index"], value["quote"]
        if (
            type(index) is not int
            or not after < index < len(messages)
            or (latest and index != latest_user)
            or messages[index].role != "user"
            or not isinstance(quote, str)
            or not quote.strip()
            or quote not in messages[index].content
        ):
            _invalid()

    for decision in decisions:
        if not isinstance(decision, dict) or set(decision) != {
            "question_id",
            "state",
            "applicability",
            "user_quotes",
        }:
            _invalid()
        qid = decision["question_id"]
        if not isinstance(qid, str) or qid not in by_id or qid in states:
            _invalid()
        if decision["state"] not in ("answered", "open", "conflicting") or decision[
            "applicability"
        ] not in ("same_scope", "changed_scope", "unrelated"):
            _invalid()
        quotes = decision["user_quotes"]
        if (
            not isinstance(quotes, list)
            or len(quotes) > 16
            or (
                not quotes
                and (
                    decision["state"] != "open"
                    or decision["applicability"] != "same_scope"
                )
            )
        ):
            _invalid()
        for quote in quotes:
            check_quote(quote, by_id[qid]["question_index"])
        if decision["applicability"] != "same_scope" and not any(
            quote["index"] == latest_user for quote in quotes
        ):
            _invalid()
        states[qid] = decision
    topics = set()
    for request in requests:
        if not isinstance(request, dict) or set(request) != {
            "topic",
            "question_id",
            "scope_quote",
        }:
            _invalid()
        topic, qid = request["topic"], request["question_id"]
        if (
            not isinstance(topic, str)
            or topic not in QUESTIONS["en"]
            or topic in topics
        ):
            _invalid()
        topics.add(topic)
        if any(
            q["topic"] == topic
            and states[q["question_id"]]["applicability"] == "same_scope"
            and states[q["question_id"]]["state"] == "answered"
            for q in questions
        ):
            _invalid("unrequested_or_answered_clarification")
        if qid is not None:
            if (
                not isinstance(qid, str)
                or qid not in by_id
                or by_id[qid]["topic"] != topic
            ):
                _invalid()
            state = states[qid]
            if state["state"] == "answered" or state["applicability"] != "same_scope":
                _invalid("unrequested_or_answered_clarification")
            check_quote(request["scope_quote"], by_id[qid]["question_index"])
        else:
            if any(
                q["topic"] == topic
                and states[q["question_id"]]["applicability"] == "same_scope"
                for q in questions
            ):
                _invalid("unrequested_or_answered_clarification")
            check_quote(request["scope_quote"], -1, latest=True)
    data = {
        "version": 1,
        "authority": "user_quote_bindings_not_record_evidence",
        "questions": questions,
        "source_context_sha256": _context_digest(messages),
        "user_sources": [
            {"index": index, "content": message.content}
            for index, message in enumerate(messages)
            if message.role == "user"
        ],
        "choices": decisions,
        "clarification_requests": requests,
    }
    return ChoiceContext(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
