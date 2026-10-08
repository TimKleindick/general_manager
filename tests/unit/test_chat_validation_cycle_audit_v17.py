"""Audit proof requires typed pass ordering and exposes hashes only."""

from copy import deepcopy
import pytest
from general_manager.chat.audit import _sanitize_planned_audit_payload


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "missing_delivery",
        "boolean_delivery",
        "early_delivery",
        "late_delivery",
        "raw_content",
        "bad_hash",
    ],
)
def test_validation_cycle_audit_rejects_missing_or_forged_proof(damage):
    proof = {
        "first_pass": 1,
        "feedback_delivered_pass": 2,
        "repeated_pass": 3,
        "action_sha256": "a" * 64,
        "feedback_sha256": "b" * 64,
        "evidence_sha256": "c" * 64,
    }
    if damage == "missing_delivery":
        proof.pop("feedback_delivered_pass")
    elif damage == "boolean_delivery":
        proof["feedback_delivered_pass"] = True
    elif damage == "early_delivery":
        proof["feedback_delivered_pass"] = 1
    elif damage == "late_delivery":
        proof["feedback_delivered_pass"] = 4
    elif damage == "raw_content":
        proof["action"] = "must not leak"
    elif damage == "bad_hash":
        proof["action_sha256"] = "?" * 64
    payload = {"reason_origin": "scheduler_validation_cycle", "validation_cycle": proof}
    before = deepcopy(payload)
    result = _sanitize_planned_audit_payload("task_progress", payload)
    assert payload == before
    if damage is None:
        assert result["validation_cycle"] == proof
    else:
        assert "validation_cycle" not in result
