"""Selected completeness evidence for supported Page and Legacy Connection paths."""

from copy import deepcopy

import pytest

from experiments.gm_eval.query_completeness import complete_identity_queries
from tests.experiments.test_gm_eval_evidence_normalization import relation_control
from tests.experiments.test_gm_eval_scoring import bind_control_trace, score


def selected_metadata_control(shape, structured, metadata, info_name="pageInfo"):
    contract, observation, judgment = relation_control(shape, structured)
    query = observation["trace"]["tool_calls"][-1]
    schema = observation["trace"]["tool_calls"][0]["output"]
    wrapper = "ProjectPage" if shape == "page" else "ProjectConnection"
    schema["types"][wrapper]["fields"].pop("pageInfo", None)
    schema["types"][wrapper]["fields"][info_name] = {"type": "ProjectPageInfo!"}
    schema["types"]["ProjectPageInfo"]["fields"] = {
        key: {"type": "Boolean!" if "Page" in key or "page" in key else "Int!"}
        for key in metadata
    }
    selection = query["arguments"]["fields"][-1]
    children = selection["fields"] if structured else selection["projects"]
    children[:] = [
        f
        for f in children
        if (f.get("field") if structured else next(iter(f)))
        not in {"pageInfo", "page_info"}
    ]
    children.append(
        {"field": info_name, "fields": list(metadata)}
        if structured
        else {info_name: list(metadata)}
    )
    nested = query["output"]["data"][0]["projects"]
    nested.pop("pageInfo", None)
    nested[info_name] = deepcopy(metadata)
    entity = nested["items"][0] if shape == "page" else nested["edges"][0]["node"]
    entity["id"] = 905
    observation["facts"].update(
        result_ids=[905], values={"905": 30}, constraints={"project": 905}
    )
    bind_control_trace(observation, judgment)
    return contract, observation, judgment, children, nested


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(
    "info_name,count_name", [("pageInfo", "totalCount"), ("page_info", "total_count")]
)
@pytest.mark.parametrize("shape", ["page", "connection"])
def test_selected_exact_total_preserves_arbitrary_id_identity(
    shape, structured, info_name, count_name
):
    contract, observation, judgment, _, _ = selected_metadata_control(
        shape, structured, {count_name: 1}, info_name
    )
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert result["passed"]
    assert result["identity_normalization"]["normalized_facts"]["result_ids"] == ["P01"]
    assert observation == before


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(
    "metadata",
    [
        {"hasNextPage": True},
        {"hasPreviousPage": True},
        {"totalCount": 2},
        {"totalCount": 1, "hasNextPage": True},
        {"totalCount": 1, "hasPreviousPage": True},
        {"hasNextPage": False},
        {"hasPreviousPage": False},
        {},
        {"hasNextPage": None, "hasPreviousPage": False},
        {"hasNextPage": 0, "hasPreviousPage": False},
        {"totalCount": True},
        {"totalCount": -1},
        {"totalCount": None},
        {"has_next_page": True, "total_count": 2},
        {"totalCount": 1, "total_count": 2},
    ],
)
def test_partial_unknown_or_contradictory_connection_cannot_certify_identity(
    structured, metadata
):
    contract, observation, judgment, _, _ = selected_metadata_control(
        "connection", structured, metadata
    )
    result = score(contract, observation, judgment)
    assert result["identity_normalization"]["mappings"] == []
    assert result["dimensions"]["R"]["status"] != "pass"
    assert not result["passed"]


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize(
    "metadata",
    [
        {"hasNextPage": False, "hasPreviousPage": False},
        {"has_next_page": False, "has_previous_page": False},
    ],
)
def test_selected_both_direction_flags_certify_complete_connection(
    structured, metadata
):
    contract, observation, judgment, _, _ = selected_metadata_control(
        "connection", structured, metadata
    )
    assert score(contract, observation, judgment)["passed"]


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("shape", ["page", "connection"])
@pytest.mark.parametrize(
    "selected_alias,other_count", [(True, 1), (True, 2), (False, 2)]
)
def test_distinct_info_aliases_require_consistent_selected_metadata(
    shape, structured, selected_alias, other_count
):
    contract, observation, judgment, children, nested = selected_metadata_control(
        shape, structured, {"totalCount": 1}
    )
    nested["page_info"] = {"total_count": other_count}
    if selected_alias:
        children.append(
            {"field": "page_info", "fields": ["total_count"]}
            if structured
            else {"page_info": ["total_count"]}
        )
        types = observation["trace"]["tool_calls"][0]["output"]["types"]
        wrapper = "ProjectPage" if shape == "page" else "ProjectConnection"
        types[wrapper]["fields"]["page_info"] = {"type": "ProjectPageInfo!"}
        types["ProjectPageInfo"]["fields"]["total_count"] = {"type": "Int!"}
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["passed"] is (not selected_alias or other_count == 1)
    if selected_alias and other_count != 1:
        assert result["identity_normalization"]["mappings"] == []


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("shape", ["page", "connection"])
@pytest.mark.parametrize(
    "mutation",
    [
        "unselected_info",
        "unselected_total",
        "duplicate_info",
        "duplicate_total",
        "missing_total",
        "wrong_info_shape",
        "wrong_collection_shape",
    ],
)
def test_only_unique_selected_actual_metadata_can_certify_collection(
    shape, structured, mutation
):
    contract, observation, judgment, children, nested = selected_metadata_control(
        shape, structured, {"totalCount": 1}
    )
    info_selection = children[-1]
    if mutation == "unselected_info":
        children.pop()
    elif mutation == "unselected_total":
        info_selection["fields" if structured else "pageInfo"] = ["hasNextPage"]
        nested["pageInfo"]["hasNextPage"] = False
    elif mutation == "duplicate_info":
        children.append(deepcopy(info_selection))
    elif mutation == "duplicate_total":
        info_selection["fields" if structured else "pageInfo"].append("totalCount")
    elif mutation == "missing_total":
        nested["pageInfo"].pop("totalCount")
    elif mutation == "wrong_info_shape":
        nested["pageInfo"] = [{"totalCount": 1}]
    else:
        key = "items" if shape == "page" else "edges"
        nested[key] = {"id": 905}
    bind_control_trace(observation, judgment)
    result = score(contract, observation, judgment)
    assert result["identity_normalization"]["mappings"] == []
    assert not result["passed"]


def complete_root_with_collection(shape="connection", total=1):
    collection = "edges" if shape == "connection" else "items"
    members = [{"node": {"id": 905}}] if shape == "connection" else [{"id": 905}]
    member_fields = [{"node": ["id"]}] if shape == "connection" else ["id"]
    nested = {collection: members, "pageInfo": {"totalCount": total}}
    fields = [
        "id",
        {"related": [{collection: member_fields}, {"pageInfo": ["totalCount"]}]},
    ]
    call = {
        "arguments": {"manager": "Container", "fields": fields},
        "output": {
            "data": [{"id": 407, "related": nested}],
            "total_count": 1,
            "has_more": False,
            "complete": True,
        },
    }
    return call, nested, fields[1]["related"]


def test_selected_connection_level_total_certifies_complete_edges():
    call, nested, fields = complete_root_with_collection()
    fields.pop()
    fields.append("totalCount")
    nested.pop("pageInfo")
    nested["totalCount"] = 1
    assert complete_identity_queries({"q": call}) == {"q"}


@pytest.mark.parametrize("shape", ["page", "connection"])
def test_selected_empty_collection_with_zero_total_is_complete(shape):
    call, nested, _ = complete_root_with_collection(shape, 0)
    nested["items" if shape == "page" else "edges"] = []
    assert complete_identity_queries({"q": call}) == {"q"}


@pytest.mark.parametrize("structured", [False, True])
def test_nested_connection_partial_flag_cannot_hide_below_selected_node(structured):
    contract, observation, judgment, children, nested = selected_metadata_control(
        "connection", structured, {"totalCount": 1}
    )
    member = children[0]["fields"] if structured else children[0]["edges"]
    node_fields = member[0]["fields"] if structured else member[0]["node"]
    child_fields = [
        {"edges": [{"node": ["id"]}]},
        {"pageInfo": ["hasNextPage", "hasPreviousPage"]},
    ]
    node_fields.append(
        {"field": "children", "fields": child_fields}
        if structured
        else {"children": child_fields}
    )
    nested["edges"][0]["node"]["children"] = {
        "edges": [{"node": {"id": 77}}],
        "pageInfo": {"hasNextPage": True, "hasPreviousPage": False},
    }
    bind_control_trace(observation, judgment)
    assert (
        score(contract, observation, judgment)["identity_normalization"]["mappings"]
        == []
    )
