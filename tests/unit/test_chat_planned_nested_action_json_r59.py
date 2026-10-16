"""Nested conversion syntax stays strict and gives actionable bounded feedback."""

import json

import pytest

from general_manager.chat.planned.action_contract import executor_action_schema
from general_manager.chat.planned.models import CalculationBinding
from general_manager.chat.planned.scheduler import (
    _executor_messages,
    _parse_action_with_feedback,
)
from general_manager.chat.planned.evidence import EvidenceStore
from tests.unit.test_chat_planned_action_feedback import _task, _reference


def nested_action():
    return {
        "action": "bind_calculation",
        "requirement_id": "weight",
        "binding": {
            "source_requirement_ids": ["rows"],
            "value_path": ["quantity"],
            "group_by": [],
            "unit_path": ["unit"],
            "conversion": {
                "factor_path": ["project", "kgPerPiece"],
                "factor_identity_path": ["project", "id"],
                "schema_requirement_ids": ["shipment_schema", "project_schema"],
                "schema_evidence_ids": ["shipment_definition", "project_definition"],
            },
        },
    }


def test_missing_outer_brace_reports_exact_syntax_location_without_repair():
    raw = json.dumps(nested_action(), separators=(",", ":"))[:-1]
    action, feedback = _parse_action_with_feedback(raw)
    assert action is None and feedback["code"] == "invalid_action_json"
    assert (
        feedback["detail"]
        == f"Expecting ',' delimiter (line 1, column {len(raw) + 1}, position {len(raw)})."
    )
    assert "kgPerPiece" not in json.dumps(feedback)
    assert _parse_action_with_feedback(raw + "}") == (nested_action(), None)


@pytest.mark.parametrize("damage", ["duplicate", "trailing", "nonfinite"])
def test_nested_action_rejection_never_repairs_or_accepts_invalid_json(damage):
    raw = json.dumps(nested_action())
    if damage == "duplicate":
        raw = raw.replace('"action":', '"action":"complete","action":', 1)
    elif damage == "trailing":
        raw += "PRIVATE"
    else:
        raw = raw.replace('"weight"', "NaN", 1)
    action, feedback = _parse_action_with_feedback(raw)
    assert action is None and feedback is not None
    assert "PRIVATE" not in json.dumps(feedback)


def test_published_nested_binding_example_roundtrips_through_real_contract():
    reference = _reference(_executor_messages("Read weights", _task(), EvidenceStore()))
    examples = reference["valid_action_examples"]
    example = next(item for item in examples if item["action"] == "bind_calculation")
    assert _parse_action_with_feedback(json.dumps(example)) == (example, None)
    binding = CalculationBinding.from_mapping(example["binding"])
    assert binding.conversion is not None
    assert binding.as_mapping() == example["binding"]
    schema = executor_action_schema()
    assert (
        next(
            branch
            for branch in schema["oneOf"]
            if branch["properties"]["action"]["const"] == "bind_calculation"
        )["additionalProperties"]
        is False
    )
