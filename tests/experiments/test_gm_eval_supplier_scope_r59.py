"""Authorized supplier scope revision; prior contracts remain independently exact."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.gm_eval import adjudication, catalog, oracle
from experiments.gm_eval.seeds import build_seed
from tests.experiments.test_gm_eval_business_semantics_r56 import digest
from tests.experiments.test_gm_eval_scoring import case, control, score


def test_all_149_reference_113_contracts_and_blind_shapes_remain_exact():
    rows = json.loads(
        Path(__file__)
        .with_name("data")
        .joinpath("reference_1_13_hashes.json")
        .read_text()
    )
    assert len(rows) == 149
    for row in rows:
        expected = oracle.expected_turn(
            case(row["case_id"]), row["turn_index"], reference_version="1.13"
        )
        assert digest(expected) == row["contract_sha256"]
        assert digest(adjudication.fact_schemas(expected)) == row["fact_schema_sha256"]


def test_supplier_approval_is_not_an_implicit_filter():
    seed = build_seed("RANK", "supplier")
    next(row for row in seed["supplier"] if row["name"] == "Nord")["approved"] = False
    current = oracle.expected_turn(case("E086"), 0, seed, reference_version="1.14")
    assert current["facts"]["result_ids"] == ["P01"]
    assert current["facts"]["constraints"] == {
        "supplier_ids": ["SUP-N"],
        "all_materials": ["M01"],
    }
    assert "supplier_parts" in {row["role"] for row in current["source_requirements"]}
    legacy = oracle.expected_turn(case("E086"), 0, seed, reference_version="1.13")
    assert legacy["facts"]["result_ids"] == []
    assert legacy["facts"]["constraints"]["supplier_approved"] is True


def test_explicit_approval_question_remains_strict_in_the_new_reference():
    approved_question = case("E086")
    approved_question["turns"] = [
        "Welche Projekte beziehen Teile vom freigegebenen Lieferanten Nord und nutzen Kupfer?"
    ]
    # A counterfactual reviewed oracle declaration, not a runtime rule inferred
    # from supplier rows or a keyword heuristic. The actual catalog stays exact.
    approved_question["supplier_approval_required"] = True
    contract = oracle.expected_turn(approved_question, 0, reference_version="1.14")
    assert contract["facts"]["constraints"]["supplier_approved"] is True
    observation, judgment = control(contract)
    assert score(contract, observation, judgment)["passed"]
    observation["facts"]["constraints"].pop("supplier_approved")
    assert not score(contract, observation, judgment)["passed"]
    observation["facts"]["constraints"]["supplier_approved"] = False
    assert not score(contract, observation, judgment)["passed"]
    seed = build_seed("RANK", "supplier")
    next(row for row in seed["supplier"] if row["name"] == "Nord")["approved"] = False
    assert (
        oracle.expected_turn(approved_question, 0, seed, reference_version="1.14")[
            "facts"
        ]["result_ids"]
        == []
    )


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", []])
def test_reference_approval_declaration_requires_an_exact_boolean(value):
    row = case("E086")
    row["supplier_approval_required"] = value
    with pytest.raises(ValueError, match="approval"):
        oracle.expected_turn(row, 0, reference_version="1.14")


@pytest.mark.parametrize(
    "damage", [None, "supplier", "material", "project", "approval", "evidence"]
)
def test_supplier_path_still_requires_exact_relations_and_evidence(damage):
    contract = oracle.expected_turn(case("E086"), 0, reference_version="1.14")
    observation, judgment = control(contract)
    if damage == "supplier":
        observation["facts"]["constraints"]["supplier_ids"] = ["SUP-S"]
    elif damage == "material":
        observation["facts"]["constraints"]["all_materials"] = ["M03"]
    elif damage == "project":
        observation["facts"]["result_ids"] = ["P02"]
    elif damage == "approval":
        observation["facts"]["constraints"]["supplier_approved"] = True
    elif damage == "evidence":
        observation["fact_support"]["constraints"]["evidence_ids"] = []
    result = score(contract, observation, judgment)
    assert result["scored"]
    assert result["passed"] is (damage is None)
    assert result["evaluation_revision"] == "measurement-contracts-8"


def test_new_reference_changes_only_authorized_supplier_semantics():
    assert catalog.REFERENCE_VERSION == "1.14"
    assert catalog.REFERENCE_VERSIONS == ("1.10", "1.11", "1.12", "1.13", "1.14")
    for row in catalog.load_catalog():
        if row["id"] == "E086":
            continue
        for index in range(len(row["turns"])):
            old = oracle.expected_turn(row, index, reference_version="1.13")
            current = oracle.expected_turn(row, index, reference_version="1.14")
            current["schema_version"] = "1.13"
            for alternative in current.get("response_alternatives", {}).values():
                alternative["schema_version"] = "1.13"
            assert current == old
    corrections = catalog.reference_corrections()
    revised = [row for row in corrections if row["version"] == "1.14"]
    assert len(revised) == 1 and revised[0]["case_id"] == "E086"
    assert not any(
        row["version"] == "1.14"
        for row in catalog.reference_corrections(reference_version="1.13")
    )


def test_explicit_approval_obligation_is_not_erased_from_prior_contracts():
    contract = oracle.expected_turn(case("E086"), 0, reference_version="1.13")
    observation, judgment = control(contract)
    observation["facts"]["constraints"].pop("supplier_approved")
    assert not score(contract, observation, judgment)["passed"]
    original = deepcopy(observation)
    observation["facts"]["constraints"]["supplier_approved"] = False
    assert not score(contract, observation, judgment)["passed"]
    assert original["facts"]["constraints"] == {
        "supplier_ids": ["SUP-N"],
        "all_materials": ["M01"],
    }
