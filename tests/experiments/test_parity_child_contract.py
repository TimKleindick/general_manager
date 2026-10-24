"""Full parity checks must precede the small subprocess confirmation."""

from copy import deepcopy
import inspect
import json

import pytest

from tests.experiments import test_gm_eval_harness as owner


def _parity_result(case_id="E100", payload="schema evidence"):
    user = (
        "Welche Stahlwerkstoffe haben wir aktuell in der Datenbank?"
        if case_id == "E101"
        else "Which forecast?"
    )
    first = {
        "user": user,
        "answer": "Which metric?",
        "tool_calls": [{"name": "get_manager_schema"}],
        "tool_results": [{"result": {"schema": payload}}],
    }
    second = {
        "user": "Read the selected rows",
        "answer": "The selected rows are ready.",
        "history": [
            {"role": "user", "content": first["user"]},
            {"role": "assistant", "content": first["answer"]},
        ],
        "tool_calls": [{"name": "query"}],
        "tool_results": [
            {"result": {"data": [{"code": c} for c in ("M02", "M04", "M05")]}}
        ],
        "persistence_verified": True,
    }
    side = {"status": "infrastructure_check", "turns": [first, second]}
    return {
        "parity_passed": True,
        "compared_turns": 2,
        "scheduler": deepcopy(side),
        "consumer": deepcopy(side),
    }


@pytest.mark.parametrize("case_id", ["E100", "E101"])
def test_validated_parity_receipt_does_not_transport_full_reports(case_id):
    full = _parity_result(case_id, "x" * 1_048_576)
    before = deepcopy(full)
    receipt = owner._verified_consumer_parity_receipt(full, case_id)
    assert receipt == {"case_id": case_id, "parity_assertions_passed": True}
    assert len(json.dumps(receipt)) < 128
    assert full == before


@pytest.mark.parametrize(
    "defect",
    [
        "parity_false",
        "parity_integer",
        "scheduler_status",
        "consumer_status",
        "no_compared_turn",
        "schema_tool",
        "schema_evidence",
        "followup_history",
        "followup_query",
        "steel_rows",
        "steel_persistence",
    ],
)
def test_parity_receipt_rejects_semantic_counterevidence(defect):
    case_id = "E101" if defect.startswith("steel_") else "E100"
    full = _parity_result(case_id)
    first, second = full["consumer"]["turns"]
    if defect == "parity_false":
        full["parity_passed"] = False
    elif defect == "parity_integer":
        full["parity_passed"] = 1
    elif defect == "scheduler_status":
        full["scheduler"]["status"] = "provider_failed"
    elif defect == "consumer_status":
        full["consumer"]["status"] = "provider_failed"
    elif defect == "no_compared_turn":
        full["compared_turns"] = 0
    elif defect == "schema_tool":
        first["tool_calls"][0]["name"] = "query"
    elif defect == "schema_evidence":
        first["tool_results"][0]["result"] = {}
    elif defect == "followup_history":
        second["history"] = []
    elif defect == "followup_query":
        second["tool_calls"][0]["name"] = "get_manager_schema"
    elif defect == "steel_rows":
        second["tool_results"][0]["result"]["data"] = [{"code": "M02"}]
    elif defect == "steel_persistence":
        second["persistence_verified"] = False
    with pytest.raises(AssertionError):
        owner._verified_consumer_parity_receipt(full, case_id)


@pytest.mark.parametrize("valid", [True, False])
def test_child_requires_full_parity_validation_before_confirmation(valid):
    full = _parity_result()
    full["parity_passed"] = valid
    program = inspect.getsource(owner._verified_consumer_parity_receipt)
    program += (
        "\nimport json\n"
        f"result={full!r}\n"
        "receipt=_verified_consumer_parity_receipt(result,'E100')\n"
        "print(json.dumps(receipt))\n"
    )
    if valid:
        assert owner._child(program) == {
            "case_id": "E100",
            "parity_assertions_passed": True,
        }
    else:
        with pytest.raises(AssertionError):
            owner._child(program)


def test_optimized_child_cannot_confirm_unchecked_parity(monkeypatch):
    full = _parity_result()
    full["parity_passed"] = False
    program = inspect.getsource(owner._verified_consumer_parity_receipt)
    program += (
        "\nimport json\n"
        f"result={full!r}\n"
        "print(json.dumps(_verified_consumer_parity_receipt(result,'E100')))\n"
    )
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    with pytest.raises(AssertionError):
        owner._child(program)
