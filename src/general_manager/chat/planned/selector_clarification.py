"""Render concrete identity choices solely from selected complete query evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import re
from typing import Any, NoReturn

from general_manager.chat.planned.clarification import CLARIFICATION_SCHEMA
from general_manager.chat.planned.evidence import (
    EvidenceRecord,
    canonical_call_identity,
)

IDENTITY_FIELDS = ("id", "code", "name", "designation")
_MANAGER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_TEMPLATES = {
    "en": "Which {manager} record do you mean: {choices}?",
    "de": "Welchen Datensatz aus {manager} meinst du: {choices}?",
    "fr": "De quel enregistrement de {manager} s'agit-il : {choices} ?",
}

# Legacy analytical topics remain evidence-free. A record identity choice is a
# distinct grounded branch; it cannot be admitted by the generic template.
ANALYTICAL_CLARIFICATION_SCHEMA: dict[str, Any] = deepcopy(CLARIFICATION_SCHEMA)
_topics = ANALYTICAL_CLARIFICATION_SCHEMA["properties"]["clarification"]["properties"][
    "requirements"
]
_topics["items"]["enum"].remove("record_selector")
_topics["maxItems"] -= 1
SELECTOR_CLARIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["clarification"],
    "properties": {
        "clarification": {
            "type": "object",
            "additionalProperties": False,
            "required": ["language", "requirements", "selector"],
            "properties": {
                "language": {"enum": list(_TEMPLATES)},
                "requirements": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {"enum": ["record_selector"]},
                },
                "selector": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["evidence_id", "field"],
                    "properties": {
                        "evidence_id": {"type": "string"},
                        "field": {"enum": list(IDENTITY_FIELDS)},
                    },
                },
            },
        },
    },
}


def _invalid() -> NoReturn:
    message = "record selector requires a selected complete identity query"
    raise ValueError(message)


def render_selector(
    value: object, records: Sequence[EvidenceRecord]
) -> tuple[str, tuple[str, ...]]:
    """Validate the witness and derive every displayed candidate from its rows."""
    if not isinstance(value, Mapping) or set(value) != {
        "language",
        "requirements",
        "selector",
    }:
        _invalid()
    language = value["language"]
    witness = value["selector"]
    if (
        not isinstance(language, str)
        or language not in _TEMPLATES
        or value["requirements"] != ["record_selector"]
        or not isinstance(witness, Mapping)
        or set(witness) != {"evidence_id", "field"}
    ):
        _invalid()
    evidence_id, field = witness["evidence_id"], witness["field"]
    if not isinstance(evidence_id, str) or field not in IDENTITY_FIELDS:
        _invalid()
    record = next((item for item in records if item.evidence_id == evidence_id), None)
    if record is None or record.kind != "query":
        _invalid()
    identity = json.loads(record.call_identity)
    if (
        not isinstance(identity, dict)
        or set(identity) != {"name", "args"}
        or identity["name"] != "query"
    ):
        _invalid()
    args = identity["args"]
    if not isinstance(args, dict) or record.call_identity != canonical_call_identity(
        "query", args
    ):
        _invalid()
    manager = args.get("manager")
    fields = args.get("fields")
    if (
        not isinstance(manager, str)
        or not _MANAGER_NAME.fullmatch(manager)
        or record.provenance.get("manager") != manager
        or record.provenance.get("tool") != "query"
        or not isinstance(fields, list)
        or field not in fields
    ):
        _invalid()
    payload = record.payload()
    if not isinstance(payload, dict):
        _invalid()
    rows = payload.get("data")
    if (
        payload.get("complete") is not True
        or payload.get("has_more") is not False
        or not isinstance(rows, list)
        or not 2 <= len(rows) <= 20
        or type(payload.get("total_count")) is not int
        or payload["total_count"] != len(rows)
    ):
        _invalid()
    choices: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            _invalid()
        label = row.get(field)
        if type(label) is int and field == "id":
            label = str(label)
        if (
            not isinstance(label, str)
            or not label.strip()
            or len(label) > 160
            or label in choices
        ):
            _invalid()
        choices.append(label)
    rendered = ", ".join(json.dumps(label, ensure_ascii=False) for label in choices)
    return _TEMPLATES[language].format(manager=manager, choices=rendered), (
        evidence_id,
    )
