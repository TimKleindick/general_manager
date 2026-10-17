"""Offline extraction controls, not measurements of a live judge's accuracy."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from experiments.gm_eval import adjudication, catalog
from experiments.gm_eval.oracle import expected_turn
from experiments.gm_eval.scoring import score_turn
from experiments.gm_eval.seeds import build_seed
from experiments.gm_eval.semantic_contracts import context_digest
from tests.experiments.test_gm_eval_adjudication import saved_run


ARCHIVE_HASHES = {
    "1.10": "b1283d9f0715bea738164774021b8a699e2bb4ac7399052e08bf968fede1732f",
    "1.11": "e5cb8a25afd1e4e475d2b34ffb1b12e30bedd1fc05bd3d8e3e7ae1cda383749f",
    "1.12": "0b9d4e33d390ca4883a2cd93a0158c7d2ab7e3a2c7a280126f96db3386b493aa",
    "1.13": "38b56a3165cce05108fb1792879cd94b99e4c98de61fae081112d519e8b761c6",
    "1.14": "6f06c99720909a4c6c447177d2e61063a4d6c68fa0d6c3464b238013cb63a25f",
}


def _case():
    return next(c for c in catalog.load_catalog() if c["id"] == "E099")


def _contract(version="1.15", turn=1, seed=None):
    return expected_turn(_case(), turn, seed_data=seed, reference_version=version)


@pytest.mark.parametrize("version", ARCHIVE_HASHES)
def test_all_149_archived_contracts_and_blind_shapes_stay_exact(version):
    rows = []
    for c in catalog.load_catalog(reference_version=version):
        for i in range(len(c["turns"])):
            e = expected_turn(c, i, reference_version=version)
            rows.append({"expectation": e, "shapes": adjudication.fact_schemas(e)})
    assert len(rows) == 149
    encoded = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    assert sha256(encoded).hexdigest() == ARCHIVE_HASHES[version]


def test_reference_115_keeps_every_existing_numeric_and_selection_check():
    assert catalog.REFERENCE_VERSION == "1.15"
    for c in catalog.load_catalog():
        for i in range(len(c["turns"])):
            old = expected_turn(c, i, reference_version="1.14")
            new = expected_turn(c, i, reference_version="1.15")
            assert new["facts"] == old["facts"]
            assert new["dimensions"] == old["dimensions"]
            assert new["source_requirements"] == old["source_requirements"]
            assert new["entity_identity_reference"] == old["entity_identity_reference"]
            if c["id"] != "E099" or i == 0:
                assert adjudication.fact_schemas(new) == adjudication.fact_schemas(old)
                assert new["semantic_checks"] == old["semantic_checks"]


@pytest.mark.parametrize("turn", [1, 2])
def test_single_winner_fields_exclude_only_clearly_explanatory_comparisons(turn):
    contract = _contract(turn=turn)
    assert contract["result_claim_scope"] == "single_winner_with_comparisons"
    shapes = adjudication.fact_schemas(contract)
    for field in ["result_ids", "ranked_ids", "values"]:
        text = shapes[field]["description"]
        assert "single winner" in text
        assert "comparison" in text
        assert "contradict" in text
        assert "answer_supported" in text
    support = next(
        c for c in contract["semantic_checks"] if c["id"] == "answer_supported"
    )
    assert "every comparison" in support["instruction"]
    assert "complete" in support["instruction"]
    assert "wrong winner" in support["instruction"]


def _run(answer, seed=None):
    seed = build_seed() if seed is None else seed
    run = saved_run()
    run["case_id"] = "E099"
    run["trace"]["provider_calls"][0]["turn"] = 2
    turn = run["turns"][0]
    # These independently annotated comparisons are not copied live answers.
    turn["turn"] = 2
    turn["user"] = _case()["turns"][1]
    turn["answer"] = answer
    turn["history"] = [
        {"role": "user", "content": _case()["turns"][0]},
        {"role": "assistant", "content": "Quel critère dois-je utiliser ?"},
    ]
    projects = [{"code": r["code"], "name": r["name"]} for r in seed["project"]]
    shipments = [
        {
            "code": r["code"],
            "quantity": r["quantity"],
            "shippedAt": r["shipped_at"],
            "unit": r["unit"],
            "source": r["source"],
            "project": {"code": r["project_code"]},
        }
        for r in seed["shipment"]
    ]
    events = []
    for n, manager, fields, rows in [
        (1, "Project", ["code", "name"], projects),
        (
            2,
            "Shipment",
            ["code", "quantity", "shippedAt", "unit", "source", {"project": ["code"]}],
            shipments,
        ),
    ]:
        events.extend(
            [
                {
                    "type": "tool_call",
                    "task_id": "read",
                    "id": f"q{n}",
                    "name": "query",
                    "args": {
                        "manager": manager,
                        "fields": fields,
                        **(
                            {
                                "filters": {
                                    "shippedAt_Gte": "2025-01-01",
                                    "shippedAt_Lt": "2026-01-01",
                                }
                            }
                            if manager == "Shipment"
                            else {}
                        ),
                    },
                },
                {
                    "type": "tool_result",
                    "task_id": "read",
                    "id": f"q{n}",
                    "name": "query",
                    "result": {
                        "data": rows,
                        "total_count": len(rows),
                        "has_more": False,
                        "complete": True,
                    },
                },
            ]
        )
    turn["events"] = [
        *events,
        {"type": "text_chunk", "content": answer},
        {"type": "done"},
    ]
    turn["tool_calls"] = [e for e in events if e["type"] == "tool_call"]
    turn["tool_results"] = [e for e in events if e["type"] == "tool_result"]
    run["turns"].insert(
        0,
        {
            **deepcopy(turn),
            "turn": 1,
            "user": _case()["turns"][0],
            "answer": "Quel critère dois-je utiliser ?",
            "history": [],
            "events": [
                {"type": "text_chunk", "content": "Quel critère dois-je utiliser ?"},
                {"type": "done"},
            ],
            "tool_calls": [],
            "tool_results": [],
            "durable_messages": [],
        },
    )
    return run


def _raw(request, winner, amount, *, supported=True):
    answer = request["packet"]["answer"]
    evidence = [r["id"] for r in request["packet"]["evidence"]]
    facts = {
        "response_mode": "answer",
        "result_ids": [winner],
        "metric": "gross_shipped_quantity",
        "unit": "pieces",
        "years": [2025],
        "source_kinds": ["actual"],
        "ranked_ids": [winner],
        "values": {winner: amount},
        "context": {
            "metric": "gross_shipped_quantity",
            "unit": "pieces",
            "years": [2025],
        },
    }
    checks = {
        c["id"]: {
            "passed": True,
            "reason": "Independent annotated extraction control.",
            "evidence_ids": ["answer", *evidence],
        }
        for c in request["packet"]["semantic_checks"]
    }
    checks["answer_supported"]["passed"] = supported
    checks["no_repeated_clarification"].update(
        context_sha256=context_digest(
            {
                "current_question": request["packet"]["current_question"],
                "visible_history": request["packet"]["visible_history"],
            }
        ),
        repetitions=[],
    )
    return {
        "schema_version": request["schema_version"],
        "status": "completed",
        "request_sha256": request["request_sha256"],
        "answer_sha256": request["packet"]["answer_sha256"],
        "facts": facts,
        "fact_support": {
            k: {
                "status": "present",
                "answer_quotes": [answer],
                "evidence_ids": evidence,
            }
            for k in facts
        },
        "citations": [],
        "semantic_checks": checks,
    }


def test_winner_policy_is_bound_and_blind_to_gold():
    answer = "Borealis (P02) wins with 500 pieces in 2025, ahead of Cobalt (P03), 200."
    contract = _contract()
    run = _run(answer)
    first = adjudication.build_adjudication_request(contract, run, 1)
    altered = deepcopy(contract)
    altered["facts"]["result_ids"] = ["SECRET_WINNER"]
    altered["facts"]["ranked_ids"] = ["SECRET_WINNER"]
    altered["facts"]["values"] = {"SECRET_WINNER": "918273"}
    for dimension in altered["dimensions"].values():
        for check in dimension["checks"]:
            if check["field"] in altered["facts"]:
                check["expected"] = altered["facts"][check["field"]]
    second = adjudication.build_adjudication_request(altered, run, 1)
    assert first["packet"] == second["packet"]
    assert first["request_sha256"] == second["request_sha256"]
    instruction = adjudication.judge_messages(first)[0].content
    assert "single winner" in instruction and "comparison table" in instruction
    assert "SECRET_WINNER" not in json.dumps(first)
    assert "918273" not in instruction


@pytest.mark.parametrize(
    ("answer", "winner", "amount", "supported", "passes"),
    [
        (
            "Borealis (P02) wins with 500 gross actual pieces in 2025; Cobalt (P03) follows with 200.",
            "P02",
            500,
            True,
            True,
        ),
        (
            "Borealis (P02) wins with 500 gross actual pieces in 2025. Comparison table: P02 500; P03 200.",
            "P02",
            500,
            True,
            True,
        ),
        (
            "Cobalt (P03) wins with 200 gross actual pieces in 2025, ahead of Borealis (P02) at 500.",
            "P03",
            200,
            False,
            False,
        ),
        (
            "Cobalt (P03) wins with 200 gross actual pieces in 2025, ahead of Borealis (P02) at 500.",
            "P03",
            200,
            True,
            False,
        ),
        (
            "Borealis (P02) wins with 499 gross actual pieces in 2025, ahead of Cobalt (P03) at 200.",
            "P02",
            499,
            False,
            False,
        ),
        (
            "Borealis (P02) wins with 499 gross actual pieces in 2025, ahead of Cobalt (P03) at 200.",
            "P02",
            499,
            True,
            False,
        ),
        (
            "Borealis (P02) wins with 500 gross actual pieces in 2025; Cobalt (P03) follows with 201.",
            "P02",
            500,
            False,
            False,
        ),
        (
            "Borealis (P02) wins with 500 gross actual pieces in 2025. Comparison table: P02 499; P03 200.",
            "P02",
            500,
            False,
            False,
        ),
        (
            "Borealis (P02) wins with 500 gross actual pieces in 2025. This complete comparison table covers all projects: P02 500; P03 200.",
            "P02",
            500,
            False,
            False,
        ),
    ],
)
def test_annotated_positive_and_negative_claims_retain_strict_scoring(
    answer, winner, amount, supported, passes
):
    contract = _contract()
    request = adjudication.build_adjudication_request(contract, _run(answer), 1)
    raw = _raw(request, winner, amount, supported=supported)
    parsed = adjudication.parse_adjudication_response(
        request, json.dumps(raw), judge_id="offline-annotated-r63-control"
    )
    assert parsed["status"] == "completed"
    assert parsed["observation"]["facts"]["values"] == {winner: amount}
    score = score_turn(
        contract,
        parsed["observation"],
        registry={"Project": "Database", "Shipment": "Database"},
        semantic_judgment=parsed["semantic_judgment"],
    )
    assert score["passed"] is passes


def test_extra_asserted_winner_still_fails_even_with_positive_semantics():
    answer = "Both Borealis (P02), 500, and Cobalt (P03), 200, are the winners for gross actual pieces in 2025."
    contract = _contract()
    request = adjudication.build_adjudication_request(contract, _run(answer), 1)
    raw = _raw(request, "P02", 500)
    raw["facts"].update(
        result_ids=["P02", "P03"],
        ranked_ids=["P02", "P03"],
        values={"P02": 500, "P03": 200},
    )
    parsed = adjudication.parse_adjudication_response(
        request, json.dumps(raw), judge_id="offline-extra-winner-control"
    )
    score = score_turn(
        contract,
        parsed["observation"],
        registry={"Project": "Database", "Shipment": "Database"},
        semantic_judgment=parsed["semantic_judgment"],
    )
    assert score["scored"] and not score["passed"]
    assert parsed["observation"]["facts"]["ranked_ids"] == ["P02", "P03"]


def test_counterfactual_source_keeps_a_different_correct_winner():
    seed = build_seed()
    next(r for r in seed["shipment"] if r["project_code"] == "P01")["quantity"] = 800
    answer = "Aurora (P01) wins with 800 gross actual pieces in 2025; Borealis (P02) follows with 500."
    contract = _contract(seed=seed)
    request = adjudication.build_adjudication_request(contract, _run(answer, seed), 1)
    raw = _raw(request, "P01", 800)
    parsed = adjudication.parse_adjudication_response(
        request, json.dumps(raw), judge_id="offline-counterfactual-control"
    )
    score = score_turn(
        contract,
        parsed["observation"],
        registry={"Project": "Database", "Shipment": "Database"},
        semantic_judgment=parsed["semantic_judgment"],
    )
    assert score["passed"]
    assert score["reference_version"] == "1.15"
    assert score["evaluation_revision"] == "measurement-contracts-9"
    parsed["observation"]["extraction"]["reference_version"] = "1.14"
    stale = score_turn(
        contract,
        parsed["observation"],
        registry={"Project": "Database", "Shipment": "Database"},
        semantic_judgment=parsed["semantic_judgment"],
    )
    assert not stale["scored"] and not stale["passed"]


def test_policy_does_not_reinterpret_an_archived_reference():
    old = _contract(version="1.14")
    altered = deepcopy(old)
    altered["result_claim_scope"] = "single_winner_with_comparisons"
    assert adjudication.fact_schemas(altered) == adjudication.fact_schemas(old)
    assert "result_claim_scope" not in _contract(turn=0)
