"""Blind extraction controls; these fixtures do not measure model accuracy."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval.oracle import expected_turn
from tests.experiments.test_gm_eval_adjudication import api, response, saved_run


LEGACY_JUDGE_SHA256 = "b420a98e32c85cb01ae1b2f22f57b5b5d1cf831c0855b0d3ace7e972ffd983a6"
LEGACY_RANK_DESCRIPTION = (
    "The answer's visible presentation order of requested result entities: "
    "an ordered list or table row sequence is sufficient without a ranking word. "
    "Preserve the asserted order, missing/extra identities and unresolved names. "
    "Never fill a missing list or replace its order from tools or a reference."
    " Include only entities asserted as members of the requested result; "
    "an explicitly excluded entity in an explanatory tie/boundary note is not a member. "
    "Preserve extra entities actually included in the requested result. "
    "All explanatory claims remain subject to answer_supported."
)
WINNER_ANSWER = (
    "Aurora (P01) is the winner with 120 pieces during calendar year 2025, "
    "according to Shipment S01."
)


def _contract(version="1.14"):
    case = next(row for row in load_catalog() if row["id"] == "E004")
    contract = expected_turn(case, 0, reference_version=version)
    # An explicit offline top-one control, not a change to any catalog case.
    contract["facts"]["ranked_ids"] = ["P01"]
    contract["dimensions"]["C"]["checks"].append(
        {"field": "ranked_ids", "expected": ["P01"], "comparison": "exact"}
    )
    contract["entity_identity_reference"]["fields"]["ranked_ids"] = {
        "manager": "Project",
        "shape": "list",
    }
    return contract


def _run(answer=WINNER_ANSWER):
    run = saved_run()
    turn = run["turns"][0]
    turn["user"] = "Which project shipped the most pieces in calendar year 2025?"
    turn["answer"] = answer
    for event in turn["events"]:
        if event["type"] == "text_chunk":
            event["content"] = answer
    return run


def _request(version="1.14", answer=WINNER_ANSWER):
    return api().build_adjudication_request(_contract(version), _run(answer), 0)


def test_new_rank_request_delivers_consistent_single_prose_winner_instructions():
    request = _request()
    instruction = api().judge_messages(request)[0].content
    description = request["packet"]["fact_fields"]["ranked_ids"]["description"]
    for text in (instruction, description):
        assert "single winner" in text.lower()
        assert "one-item" in text
        assert "mere mention" in text.lower()
    assert instruction.count("When ranked_ids is requested") == 1
    assert "entities in an ordered list or table row sequence" not in instruction


@pytest.mark.parametrize("version", ["1.10", "1.11", "1.12", "1.13"])
def test_legacy_rank_shapes_and_delivered_system_text_remain_exact(version):
    request = _request(version)
    assert request["packet"]["fact_fields"]["ranked_ids"]["description"] == (
        LEGACY_RANK_DESCRIPTION
    )
    assert sha256(api().judge_messages(request)[0].content.encode()).hexdigest() == (
        LEGACY_JUDGE_SHA256
    )


def _restore_legacy_rank_description(node):
    if isinstance(node, dict):
        properties = node.get("properties", {})
        if "ranked_ids" in properties:
            properties["ranked_ids"]["description"] = LEGACY_RANK_DESCRIPTION
        for value in node.values():
            _restore_legacy_rank_description(value)
    elif isinstance(node, list):
        for value in node:
            _restore_legacy_rank_description(value)


def test_archived_114_shape_keeps_its_legacy_system_text_under_new_code():
    request = _request()
    request["packet"]["fact_fields"]["ranked_ids"]["description"] = (
        LEGACY_RANK_DESCRIPTION
    )
    _restore_legacy_rank_description(request["packet"]["response_schema"])
    request["request_sha256"] = api()._request_digest(request)
    assert sha256(api().judge_messages(request)[0].content.encode()).hexdigest() == (
        LEGACY_JUDGE_SHA256
    )


def test_nonranking_114_request_keeps_its_original_system_text():
    case = next(row for row in load_catalog() if row["id"] == "E004")
    contract = expected_turn(case, 0, reference_version="1.14")
    request = api().build_adjudication_request(contract, saved_run(), 0)
    assert sha256(api().judge_messages(request)[0].content.encode()).hexdigest() == (
        LEGACY_JUDGE_SHA256
    )


def test_ranking_instructions_and_shapes_do_not_reveal_gold_winner_or_count():
    contract = _contract()
    changed = deepcopy(contract)
    changed["facts"]["ranked_ids"] = ["SECRET_GOLD_A", "SECRET_GOLD_B"]
    changed["dimensions"]["C"]["checks"][-1]["expected"] = [
        "SECRET_GOLD_A",
        "SECRET_GOLD_B",
    ]
    first = api().build_adjudication_request(contract, _run(), 0)
    second = api().build_adjudication_request(changed, _run(), 0)
    assert first["packet"] == second["packet"]
    assert first["request_sha256"] == second["request_sha256"]
    assert (
        api().judge_messages(first)[0].content
        == api().judge_messages(second)[0].content
    )


@pytest.mark.parametrize(
    ("answer", "claimed_order", "support_status", "passes_rank"),
    [
        (WINNER_ANSWER, ["P01"], "present", True),
        (
            "Borealis (P02) is the winner; Aurora (P01) shipped 120 pieces "
            "according to Shipment S01.",
            ["P02"],
            "unsupported",
            False,
        ),
        (
            "1. Borealis (P02). 2. Aurora (P01), with 120 pieces "
            "according to Shipment S01.",
            ["P02", "P01"],
            "unsupported",
            False,
        ),
        (
            "Aurora (P01) shipped 120 pieces according to Shipment S01.",
            None,
            "absent",
            False,
        ),
    ],
)
def test_saved_extraction_preserves_correct_wrong_extra_and_unranked_claims(
    answer, claimed_order, support_status, passes_rank
):
    from experiments.gm_eval.scoring import score_turn

    request = _request(answer=answer)
    raw = json.loads(response(request))
    raw["facts"]["ranked_ids"] = claimed_order
    evidence = [row["id"] for row in request["packet"]["evidence"]]
    raw["fact_support"]["ranked_ids"] = {
        "status": support_status,
        "answer_quotes": [] if support_status == "absent" else [answer],
        "evidence_ids": evidence if support_status == "present" else [],
    }
    parsed = api().parse_adjudication_response(
        request, json.dumps(raw), judge_id="offline-ranking-contract"
    )
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"]["ranked_ids"] == claimed_order
    scored = score_turn(
        _contract(),
        parsed["observation"],
        registry={"Shipment": "Database", "Project": "Database"},
        semantic_judgment=parsed["semantic_judgment"],
    )
    rank = next(
        check
        for check in scored["dimensions"]["C"]["checks"]
        if check["field"] == "ranked_ids"
    )
    assert rank["status"] == ("pass" if passes_rank else "fail")
