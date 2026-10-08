"""Context-bound missing decisions, without normalizing extracted topic labels."""

from copy import deepcopy

import pytest

from experiments.gm_eval import catalog, oracle
from experiments.gm_eval.scoring import _matches
from tests.experiments.test_gm_eval_scoring import control, expected, score


OUTLOOK_CASES = ["E019", "E031", "E043", "E055", "E067"]
GROUPS = [["criterion", "metric"], ["horizon"]]
DEFINED_ANALYSES = [
    case["id"]
    for case in catalog.load_catalog()
    if case["contract"] in {"clarify_development", "final"}
]


@pytest.mark.parametrize("case_id", OUTLOOK_CASES)
@pytest.mark.parametrize(
    "topics",
    [
        ["criterion", "horizon"],
        ["metric", "horizon"],
        ["criterion", "metric", "horizon"],
    ],
)
def test_open_outlook_accepts_suitable_basis_and_horizon_without_relabeling(
    case_id, topics
):
    contract = expected(case_id)
    rule = next(
        c
        for c in contract["dimensions"]["Q"]["checks"]
        if c["field"] == "clarification_topics"
    )
    assert rule["comparison"] == "required_topic_groups"
    assert rule["expected"] == GROUPS
    observation, judgment = control(contract)
    observation["facts"]["clarification_topics"] = topics
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["passed"]
    check = next(
        c
        for c in result["dimensions"]["Q"]["checks"]
        if c["field"] == "clarification_topics"
    )
    assert check["observed"] == topics
    assert observation == before


@pytest.mark.parametrize("case_id", OUTLOOK_CASES)
@pytest.mark.parametrize(
    "topics",
    [
        [],
        ["criterion"],
        ["metric"],
        ["horizon"],
        ["customer_identity", "horizon"],
        ["unit", "horizon"],
        ["criterion", "horizon", "horizon"],
        ["metric", "horizon", "invented"],
    ],
)
def test_open_outlook_rejects_missing_wrong_or_invalid_decisions(case_id, topics):
    contract = expected(case_id)
    observation, judgment = control(contract)
    observation["facts"]["clarification_topics"] = topics
    result = score(contract, observation, judgment)
    check = next(
        c
        for c in result["dimensions"]["Q"]["checks"]
        if c["field"] == "clarification_topics"
    )
    assert check["status"] == "fail" and not result["passed"]


@pytest.mark.parametrize("case_id", DEFINED_ANALYSES)
def test_defined_analysis_metric_remains_a_separate_required_decision(case_id):
    contract = expected(case_id)
    rule = next(
        c
        for c in contract["dimensions"]["Q"]["checks"]
        if c["field"] == "clarification_topics"
    )
    assert rule == {
        "field": "clarification_topics",
        "expected": ["metric", "horizon"],
        "comparison": "required_topics",
    }
    observation, judgment = control(contract)
    assert score(contract, observation, judgment)["passed"]
    observation["facts"]["clarification_topics"] = ["criterion", "horizon"]
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize("topics", [["criterion", "horizon"], ["metric", "horizon"]])
@pytest.mark.parametrize(
    "problem",
    ["unsuitable", "missing_judgment", "premature_result", "unvalidated_extraction"],
)
def test_topic_groups_cannot_bypass_independent_behavior_checks(topics, problem):
    contract = expected("E019")
    observation, judgment = control(contract)
    observation["facts"]["clarification_topics"] = topics
    if problem == "unsuitable":
        judgment["checks"]["clarification_suitable"]["passed"] = False
    elif problem == "missing_judgment":
        del judgment["checks"]["clarification_suitable"]
    elif problem == "premature_result":
        observation["facts"]["result_assertion"] = "some"
    else:
        observation["extraction"]["status"] = "unvalidated"
    assert not score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize(
    "groups",
    [
        [],
        [[]],
        ["criterion", "horizon"],
        [["criterion"], []],
        [["criterion", "criterion"], ["horizon"]],
        [["invalid"], ["horizon"]],
        [[True], ["horizon"]],
        None,
        {"criterion": "metric"},
    ],
)
def test_malformed_topic_group_contract_never_grants_success(groups):
    assert not _matches(
        groups, ["criterion", "metric", "horizon"], "required_topic_groups"
    )


@pytest.mark.parametrize(
    "actual",
    [
        None,
        "criterion,horizon",
        {"criterion": True, "horizon": True},
        [True, "horizon"],
        ["criterion", None],
        [["criterion"], "horizon"],
    ],
)
def test_group_comparison_rejects_non_topic_extraction_shapes(actual):
    assert not _matches(GROUPS, actual, "required_topic_groups")


def test_topic_groups_do_not_change_existing_required_topics_comparison():
    assert _matches(["metric", "horizon"], ["metric", "horizon"], "required_topics")
    assert not _matches(
        ["metric", "horizon"], ["criterion", "horizon"], "required_topics"
    )
    assert not _matches(["criterion"], ["metric"], "required_topics")


def test_generic_outlook_dispatch_uses_context_contract_and_never_case_id():
    case = next(c for c in catalog.load_catalog() if c["id"] == "E019")
    case["id"] = "E555"
    contract = oracle.expected_turn(case, 0)
    rule = next(
        c
        for c in contract["dimensions"]["Q"]["checks"]
        if c["field"] == "clarification_topics"
    )
    assert rule["comparison"] == "required_topic_groups" and rule["expected"] == GROUPS
    case["contract"] = "clarify_development"
    contract = oracle.expected_turn(case, 0)
    rule = next(
        c
        for c in contract["dimensions"]["Q"]["checks"]
        if c["field"] == "clarification_topics"
    )
    assert rule["comparison"] == "required_topics" and rule["expected"] == [
        "metric",
        "horizon",
    ]
