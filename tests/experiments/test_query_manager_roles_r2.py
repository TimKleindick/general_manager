"""Typed manager objects and pagination wrappers have distinct evidence roles."""

from copy import deepcopy

import pytest

from tests.experiments.test_gm_eval_evidence_normalization import relation_control
from tests.experiments.test_gm_eval_scoring import bind_control_trace, score


def manager_relation_control(name, collection, structured, nested=False):
    contract, observation, judgment = relation_control("single", structured)
    schema, query = observation["trace"]["tool_calls"]
    entity = query["output"]["data"][0]["project"]
    selection = query["arguments"]["fields"][-1]
    children = selection["fields" if structured else "project"]
    signature = "[ProjectType!]!" if collection else "ProjectType!"
    if nested:
        schema_project = {
            "id": "schema-project",
            "name": "get_manager_schema",
            "arguments": {"manager": "Project"},
            "output": {
                "manager": "Project",
                "contract_version": 2,
                "type": "ProjectType",
                "relations": [{"name": name, "path": [name], "target": "Project"}],
                "types": {
                    "ProjectType": {
                        "kind": "object",
                        "fields": {name: {"type": signature}},
                    }
                },
            },
            "error": False,
        }
        # Self-manager references terminate in a separate native reference type.
        schema_project["output"]["types"]["ProjectType"]["fields"][name]["type"] = (
            "[ProjectReference!]!" if collection else "ProjectReference!"
        )
        schema_project["output"]["types"]["ProjectReference"] = {
            "kind": "reference",
            "manager": "Project",
        }
        observation["trace"]["tool_calls"].insert(1, schema_project)
        children.append(
            {"field": name, "fields": ["id", "code", "name"]}
            if structured
            else {name: ["id", "code", "name"]}
        )
        inner = deepcopy(entity)
        entity[name] = [inner] if collection else inner
    else:
        query["output"]["data"][0].pop("project")
        query["output"]["data"][0][name] = [entity] if collection else entity
        query["arguments"]["fields"][-1] = (
            {"field": name, "fields": children} if structured else {name: children}
        )
        schema["output"]["relations"][0].update(name=name, path=[name])
        schema["output"]["types"]["ShipmentType"]["fields"] = {
            name: {"type": signature}
        }
    bind_control_trace(observation, judgment)
    return contract, observation, judgment


@pytest.mark.parametrize("name", ["items", "edges"])
@pytest.mark.parametrize("collection", [False, True])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_declared_manager_relation_names_are_not_pagination_wrappers(
    name, collection, structured, nested
):
    contract, observation, judgment = manager_relation_control(
        name, collection, structured, nested
    )
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["passed"], result["identity_normalization"]
    assert result["dimensions"]["R"]["status"] == "pass"
    assert result["identity_normalization"]["normalized_facts"]["result_ids"] == ["P01"]
    assert observation == before


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_schema",
        "schema_after",
        "wrong_target",
        "untyped",
        "hidden",
        "unselected",
        "failed",
        "incomplete_root",
    ],
)
def test_manager_role_cannot_replace_schema_selection_or_query_evidence(mutation):
    contract, observation, judgment = manager_relation_control("edges", True, False)
    schema, query = observation["trace"]["tool_calls"]
    if mutation == "missing_schema":
        observation["trace"]["tool_calls"].remove(schema)
    elif mutation == "schema_after":
        observation["trace"]["tool_calls"].reverse()
    elif mutation == "wrong_target":
        schema["output"]["relations"][0]["target"] = "Customer"
    elif mutation == "untyped":
        schema["output"]["types"]["ProjectType"].pop("manager")
    elif mutation == "hidden":
        query["managers"].remove("Project")
    elif mutation == "unselected":
        query["arguments"]["fields"].pop()
    elif mutation == "failed":
        query["error"] = True
    else:
        query["output"]["total_count"] = 2
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["identity_normalization"]["mappings"] == []
    assert result["dimensions"]["R"]["status"] != "pass"
    assert not result["passed"]


@pytest.mark.parametrize("name", ["items", "edges"])
@pytest.mark.parametrize("shape", ["page", "connection"])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("total", [1, 2])
def test_manager_field_named_like_wrapper_still_checks_actual_child_collection(
    name, shape, structured, total
):
    contract, observation, judgment = relation_control(shape, structured)
    schema, query = observation["trace"]["tool_calls"]
    entity = query["output"]["data"][0].pop("projects")
    query["output"]["data"][0][name] = entity
    selection = query["arguments"]["fields"][-1]
    children = selection["fields" if structured else "projects"]
    query["arguments"]["fields"][-1] = (
        {"field": name, "fields": children} if structured else {name: children}
    )
    relation = schema["output"]["relations"][0]
    relation.update(name=name, path=[name, *relation["path"][1:]])
    root_fields = schema["output"]["types"]["ShipmentType"]["fields"]
    root_fields[name] = root_fields.pop("projects")
    entity["pageInfo"]["totalCount"] = total
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["passed"] is (total == 1)
    if total != 1:
        assert result["identity_normalization"]["mappings"] == []
        assert result["dimensions"]["R"]["status"] != "pass"


def test_manager_coordinates_do_not_create_missing_evidence_records():
    contract, observation, judgment = manager_relation_control("edges", True, False)
    observation["trace"]["evidence"] = [
        e for e in observation["trace"]["evidence"] if e["manager"] != "Project"
    ]
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["identity_normalization"]["mappings"] == []
    assert result["dimensions"]["R"]["status"] != "pass"
    assert not result["passed"]


@pytest.mark.parametrize("name", ["items", "edges"])
@pytest.mark.parametrize("shape", ["page", "connection"])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("duplicate", ["compound", "mixed"])
def test_duplicate_manager_field_cannot_hide_partial_child_wrapper(
    name, shape, structured, duplicate
):
    from tests.experiments.test_gm_eval_scoring import expected, identity_control

    contract = expected("E106")
    observation, judgment = identity_control(
        contract,
        "Customer",
        [{"id": 407, "code": "C01"}, {"id": 822, "code": "C02"}],
        [407, 822],
    )
    query = observation["trace"]["tool_calls"][-1]
    rows_field = "items" if shape == "page" else "edges"
    child_fields = ["id"] if shape == "page" else [{"node": ["id"]}]
    children = (
        [
            {"field": rows_field, "fields": child_fields},
            {"field": "pageInfo", "fields": ["totalCount"]},
        ]
        if structured
        else [{rows_field: child_fields}, {"pageInfo": ["totalCount"]}]
    )
    selected = {"field": name, "fields": children} if structured else {name: children}
    query["arguments"]["fields"].extend(
        [
            selected,
            deepcopy(selected) if duplicate == "compound" else name,
        ]
    )
    query["manager_fields"]["Customer"] = deepcopy(query["arguments"]["fields"])
    returned = [{"id": 99}] if shape == "page" else [{"node": {"id": 99}}]
    for row in query["output"]["data"]:
        row[name] = {rows_field: returned, "pageInfo": {"totalCount": 2}}
    bind_control_trace(observation, judgment)
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["identity_normalization"]["mappings"] == []
    assert result["dimensions"]["R"]["status"] != "pass"
    assert not result["passed"]
    assert observation == before
