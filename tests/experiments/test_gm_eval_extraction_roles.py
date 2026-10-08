"""Generic extraction contracts preserve restrictions and identity roles."""

from copy import deepcopy
import json
import pytest
from experiments.gm_eval.adjudication import fact_schemas
from experiments.gm_eval.scoring import _matches
from tests.experiments.test_gm_eval_adjudication import expectation


def contract():
    value = deepcopy(expectation())
    value["facts"] = {
        "result_ids": [],
        "material_ids": ["PRIVATE_TARGET_VALUE"],
        "constraints": {"all_materials": ["PRIVATE_TARGET_VALUE"]},
    }
    value["entity_identity_reference"]["fields"] = {
        "result_ids": {"manager": "Asset", "shape": "list"},
        "material_ids": {"manager": "Element", "shape": "list"},
    }
    value["entity_identity_reference"]["paths"] = [
        {
            "path": ["constraints", "all_materials"],
            "manager": "Element",
            "shape": "list",
        }
    ]
    return value


def test_neutral_scope_has_one_declared_representation_without_erasing_filters():
    value = contract()
    value["facts"]["constraints"] = {"owner": "PRIVATE_OWNER"}
    text = fact_schemas(value)["constraints"]["description"]
    assert "omit that constraint key" in text
    assert "empty object" in text
    assert "Preserve every actual narrowing" in text
    assert "PRIVATE_OWNER" not in text


@pytest.mark.parametrize(
    "scope",
    [
        {"owner": "A", "status": "ongoing"},
        {"owner": "A", "status": "all"},
        {"owner": "A", "unknown_filter": None},
        {"owner": "B"},
    ],
)
def test_true_or_unknown_extra_scope_claims_still_fail_exact_comparison(scope):
    assert not _matches({"owner": "A"}, scope)


def test_target_identity_is_distinct_from_results_and_explanations_without_gold():
    shapes = fact_schemas(contract())
    target = shapes["material_ids"]["description"]
    assert "selection targets" in target
    assert "constraints.all_materials" in target
    assert "empty" in target
    assert "explanatory" in target
    assert "Element" in target
    assert "PRIVATE_TARGET_VALUE" not in json.dumps(shapes)
    assert "members of the requested result" in shapes["result_ids"]["description"]


def test_related_identity_without_selection_path_is_not_invented_as_target():
    value = contract()
    value["entity_identity_reference"]["paths"] = []
    shapes = fact_schemas(value)
    assert "related to the requested result" in shapes["material_ids"]["description"]
    assert "selection targets" not in shapes["material_ids"]["description"]


def test_scope_override_also_preserves_the_neutral_scope_contract():
    value = contract()
    value["selection_scope_contract"] = {"claims_field": "constraints"}
    assert (
        "omit that constraint key" in fact_schemas(value)["constraints"]["description"]
    )
