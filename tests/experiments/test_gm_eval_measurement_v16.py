"""Versioned measurement controls; no new candidate or judge performance."""

from copy import deepcopy
import pytest
from tests.experiments.test_gm_eval_scoring import expected, control, score
from tests.experiments.test_gm_eval_adjudication import saved_run, expectation
from experiments.gm_eval import adjudication, scoring


@pytest.mark.parametrize("identifier", ["E007", "E018", "E030", "E042", "E054", "E066"])
def test_explicit_full_ranking_includes_all_known_projects(identifier):
    facts = expected(identifier, 1)["facts"]
    assert facts["ranked_ids"] == [
        "P02",
        "P03",
        "P06",
        "P01",
        "P04",
        "P05",
        "P07",
        "P08",
        "P11",
    ]
    assert facts["values"]["P11"] == "0.00"


def test_typed_clarification_topics_allow_only_relevant_canonical_extras():
    contract = expected("E007")
    shapes = adjudication.fact_schemas(contract)
    assert set(shapes["clarification_topics"]["items"]["enum"]) == {
        "criterion",
        "metric",
        "horizon",
        "customer_identity",
        "unit",
    }
    assert contract["facts"]["result_assertion"] == "none"
    assert "result_ids" not in contract["facts"]
    observation, judgment = control(contract)
    observation["facts"]["clarification_topics"] = ["criterion", "horizon"]
    assert score(contract, observation, judgment)["passed"]
    judgment["checks"]["clarification_suitable"]["passed"] = False
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize(
    "actual", [["criterion", "nonsense"], ["horizon"], ["criterion", "criterion"], None]
)
def test_bad_topics_do_not_pass(actual):
    assert not scoring._matches(["criterion"], actual, "required_topics")


@pytest.mark.parametrize("assertion", [None, "some", "unknown"])
def test_absent_or_result_claim_is_not_no_result_assertion(assertion):
    contract = expected("E007")
    observation, judgment = control(contract)
    observation["facts"]["result_assertion"] = assertion
    assert not score(contract, observation, judgment)["passed"]


def test_piece_spelling_normalizes_only_explicit_unit_fields():
    contract = expected("E008", 1)
    observation, judgment = control(contract)
    observation["facts"]["unit"] = "Stück"
    observation["facts"]["context"]["unit"] = "Stück"
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert observation["facts"]["unit"] == "Stück"
    assert result["unit_normalization"]["context"]["changed"]
    observation["facts"]["unit"] = "kg"
    assert not score(contract, observation, judgment)["passed"]


def test_plan_totals_and_extra_breakdown_claims_are_separate_obligations():
    contract = expected("E008", 1)
    assert "aggregate" in adjudication.fact_schemas(contract)["plan"]["description"]
    observation, judgment = control(contract)
    judgment["checks"]["additional_breakdowns_supported"]["passed"] = False
    assert not score(contract, observation, judgment)["passed"]


def test_unknown_query_count_is_valid_observed_data_but_not_completeness():
    run = saved_run()
    output = run["turns"][0]["events"][1]["result"]
    output.update(total_count=None, has_more=None, complete=False)
    run["turns"][0]["durable_messages"][0]["tool_result"] = deepcopy(output)
    request = adjudication.build_adjudication_request(expectation(), run, 0)
    call = request["observation"]["trace"]["tool_calls"][0]
    assert call["output"]["complete"] is False
    assert call["output"]["total_count"] is None


def history_packet():
    return {
        "visible_history": [
            {"role": "user", "content": "For customer XYZ, use shipped pieces."}
        ],
        "evidence": [{"id": "e1"}],
    }


def support():
    return {
        "status": "present",
        "answer_quotes": ["These projects"],
        "evidence_ids": ["e1"],
        "history_quotes": [{"index": 0, "quote": "customer XYZ"}],
    }


def test_visible_scope_carryover_has_auditable_user_quote_and_actual_evidence():
    adjudication._validate_history_support(history_packet(), "customer_ids", support())
    adjudication._validate_history_support(history_packet(), "context", support())


@pytest.mark.parametrize(
    "defect",
    [
        "invisible",
        "invented_quote",
        "assistant",
        "no_source",
        "wrong_source",
        "absent",
        "numeric_claim",
    ],
)
def test_carryover_rejects_unbounded_or_unsupported_reconstruction(defect):
    packet = history_packet()
    value = support()
    name = "customer_ids"
    if defect == "invisible":
        value["history_quotes"][0]["index"] = 9
    elif defect == "invented_quote":
        value["history_quotes"][0]["quote"] = "customer C02"
    elif defect == "assistant":
        packet["visible_history"][0]["role"] = "assistant"
    elif defect == "no_source":
        value["evidence_ids"] = []
    elif defect == "wrong_source":
        value["evidence_ids"] = ["foreign"]
    elif defect == "absent":
        value["status"] = "absent"
    else:
        name = "plan"
    with pytest.raises(adjudication.AdjudicationError):
        adjudication._validate_history_support(packet, name, value)
