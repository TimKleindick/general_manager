"""Contract tests for immutable planned-chat evidence."""

from __future__ import annotations

import json

import pytest

from general_manager.chat.planned.evidence import (
    DuplicateEvidenceError,
    EvidenceRecord,
    EvidenceStore,
    IncompatibleEvidenceError,
    canonical_call_identity,
)
from general_manager.chat.planned.models import EvidenceRequirement


def record(
    evidence_id: str = "ev-1",
    task_id: str = "task-1",
    kind: str = "query",
    payload: object | None = None,
) -> EvidenceRecord:
    return EvidenceRecord.create(
        evidence_id,
        task_id,
        kind,
        canonical_call_identity("query", {"manager": "Part"}),
        {"tool": "query"},
        {"data": []} if payload is None else payload,
    )


def test_evidence_payload_is_immutable_snapshot() -> None:
    payload = {"data": [{"value": 3}]}
    evidence = record(payload=payload)

    payload["data"][0]["value"] = 9
    first_read = evidence.payload()
    first_read["data"][0]["value"] = 10

    assert evidence.payload() == {"data": [{"value": 3}]}
    assert evidence.payload_json == '{"data":[{"value":3}]}'


def test_evidence_record_is_frozen_and_provenance_is_snapshot() -> None:
    provenance = {"tool": "query"}
    evidence = record()
    provenance["task"] = "changed"

    assert evidence.provenance == {"tool": "query"}
    with pytest.raises(TypeError):
        evidence.provenance["task"] = "not allowed"  # type: ignore[index]


@pytest.mark.parametrize(
    "key",
    ["cookie", "set-cookie", "session", "session_id", "private_key", "unknown_secret"],
)
def test_provenance_redacts_unallowlisted_or_secret_like_values(key: str) -> None:
    evidence = EvidenceRecord.create(
        "ev-1",
        "task-1",
        "query",
        "call",
        {key: "sensitive-value"},
        {"data": []},
    )

    assert evidence.provenance[key] == "[redacted]"


def test_provenance_preserves_allowlisted_framework_values() -> None:
    evidence = EvidenceRecord.create(
        "ev-1",
        "task-1",
        "query",
        "call",
        {
            "tool": "query",
            "manager": "PartManager",
            "operation": "sum",
            "calculator": "framework",
            "kind": "query",
            "relation": "parts",
            "direction": "forward",
            "field": "quantity",
        },
        {"data": []},
    )

    assert evidence.provenance["tool"] == "query"
    assert evidence.provenance["manager"] == "PartManager"
    assert evidence.provenance["field"] == "quantity"


def test_call_identity_is_canonical_across_argument_order() -> None:
    left = canonical_call_identity("query", {"b": 2, "a": {"d": 4, "c": 3}})
    right = canonical_call_identity("query", {"a": {"c": 3, "d": 4}, "b": 2})

    assert left == right
    assert left == '{"args":{"a":{"c":3,"d":4},"b":2},"name":"query"}'


def test_call_identity_preserves_sequence_order_and_rejects_non_json_values() -> None:
    assert canonical_call_identity("query", {"fields": ["name", "id"]}) != (
        canonical_call_identity("query", {"fields": ["id", "name"]})
    )
    with pytest.raises(TypeError):
        canonical_call_identity("query", {"bad": {"not", "json"}})


def test_evidence_store_enforces_unique_ids_and_task_filtering() -> None:
    store = EvidenceStore()
    first = record("ev-1", "task-1")
    second = record("ev-2", "task-2")
    store.add(first)
    store.add(second)

    assert store.get("ev-1") == first
    assert store.get("missing") is None
    assert store.for_task("task-1") == (first,)
    with pytest.raises(DuplicateEvidenceError):
        store.add(first)


def test_store_rejects_incompatible_requirement_links() -> None:
    store = EvidenceStore()
    evidence = record(kind="query")
    requirement = EvidenceRequirement(
        requirement_id="schema-1",
        kind="schema",
        description="Inspect the schema.",
        operation=None,
    )

    with pytest.raises(IncompatibleEvidenceError):
        store.add(evidence, requirement=requirement)
    store.add(evidence)
    with pytest.raises(IncompatibleEvidenceError):
        store.link(requirement, evidence.evidence_id)


def test_store_link_accepts_matching_requirement() -> None:
    store = EvidenceStore()
    evidence = record(kind="query")
    store.add(evidence)
    requirement = EvidenceRequirement(
        requirement_id="query-1",
        kind="query",
        description="Run the query.",
        operation=None,
    )

    assert store.link(requirement, evidence.evidence_id) == evidence
    assert store.for_requirement(requirement) == (evidence,)


def test_record_payload_json_must_be_valid_json() -> None:
    with pytest.raises(ValueError):
        EvidenceRecord("ev-1", "task-1", "query", "call", {}, "not json")


def test_record_payload_can_round_trip_json_values() -> None:
    evidence = record(payload={"ok": True, "none": None, "number": 1.5})

    assert json.loads(evidence.payload_json) == evidence.payload()
