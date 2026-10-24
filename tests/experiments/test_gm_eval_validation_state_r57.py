"""State equality is one strict subpredicate, never a full cycle certificate."""

from copy import deepcopy
from hashlib import sha256
import importlib
import json
from pathlib import Path

import pytest

from general_manager.chat.planned import scheduler


def fixture():
    return json.loads(
        Path(__file__)
        .with_name("data")
        .joinpath("e078_validation_state_r57.json")
        .read_text()
    )


def test_saved_syntax_pair_keeps_invalid_json_and_exact_runtime_state_digest():
    data = fixture()
    first, repeated = data["references"]
    assert data["actions"][0] == data["actions"][1]
    parsed, feedback = scheduler._parse_action_with_feedback(data["actions"][0])
    assert parsed is None and feedback["code"] == "invalid_action_json"
    historical_feedback = repeated["action_validation_error"]
    assert {key: feedback[key] for key in historical_feedback} == historical_feedback
    assert set(feedback) - set(historical_feedback) == {"detail"}
    assert (
        feedback["detail"]
        == "Expecting ',' delimiter (line 1, column 426, position 425)."
    )
    assert (
        sha256(data["actions"][0].encode()).hexdigest()
        == data["validation_cycle"]["action_sha256"]
    )
    assert hasattr(scheduler, "execution_state_signature")
    state = scheduler.execution_state_signature(first)
    assert state == scheduler.execution_state_signature(repeated)
    assert (
        sha256(state.encode()).hexdigest()
        == data["validation_cycle"]["evidence_sha256"]
    )


@pytest.mark.parametrize(
    "field", ["action_validation_error", "manager_candidates", "last_tool_round"]
)
def test_only_operational_top_level_fields_are_excluded_without_mutating_input(field):
    assert hasattr(scheduler, "execution_state_signature")
    reference = {
        "task": {"task_id": "read", "last_tool_round": "nested material value"},
        "task_evidence": [{"payload": "A"}],
        field: {"old": True},
    }
    before = deepcopy(reference)
    changed = deepcopy(reference)
    changed[field] = {"new": False}
    assert scheduler.execution_state_signature(
        reference
    ) == scheduler.execution_state_signature(changed)
    assert reference == before
    changed["task"]["last_tool_round"] = "changed nested material value"
    assert scheduler.execution_state_signature(
        reference
    ) != scheduler.execution_state_signature(changed)


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "task",
        "evidence",
        "dependency",
        "history",
        "request",
        "unknown_field",
        "schema",
        "deferred",
        "digest",
        "missing_digest",
    ],
)
def test_eval_state_subpredicate_requires_equal_material_state_and_audit_digest(damage):
    module = importlib.util.find_spec("experiments.gm_eval.validation_cycles")
    assert module is not None
    api = importlib.import_module(module.name)
    data = fixture()
    first, repeated = data["references"]
    digest = data["validation_cycle"]["evidence_sha256"]
    if damage == "digest":
        digest = "0" * 64
    elif damage == "missing_digest":
        digest = None
    elif damage is not None:
        key = {
            "task": "task",
            "evidence": "task_evidence",
            "dependency": "dependency_evidence",
            "history": "conversation_context",
            "request": "original_request",
            "unknown_field": "future_evidence",
            "schema": "required_action_schema",
            "deferred": "deferred_calculations",
        }[damage]
        repeated[key] = {"changed": True}
    assert api.validation_cycle_state_matches(first, repeated, digest) is (
        damage is None
    )


def test_shared_signature_is_byte_exact_with_the_previous_runtime_projection():
    reference = {
        "task": {"text": "Größe 铜", "last_tool_round": 1},
        "numbers": [True, 1, 1.0, None],
        "unknown": {"payload": "é"},
        "last_tool_round": {"evidence_or_links_changed": True},
        "action_validation_error": {"code": "invalid_action_json"},
        "manager_candidates": [{"manager": "ChangedCandidate"}],
    }
    old_projection = deepcopy(reference)
    for key in ("action_validation_error", "manager_candidates", "last_tool_round"):
        old_projection.pop(key, None)
    assert scheduler.execution_state_signature(reference) == json.dumps(
        old_projection, sort_keys=True
    )
    for old, new in ((True, 1), (1, 1.0)):
        first = {"retained": old}
        repeated = {"retained": new}
        assert scheduler.execution_state_signature(
            first
        ) != scheduler.execution_state_signature(repeated)
