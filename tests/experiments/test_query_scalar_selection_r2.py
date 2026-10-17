"""Native-valid leaf selections must not masquerade as pagination wrappers."""

from copy import deepcopy

from graphql import build_schema, graphql_sync
import pytest

from experiments.gm_eval.query_completeness import complete_identity_queries
from tests.experiments.test_gm_eval_scoring import bind_control_trace, score
from tests.experiments.test_query_identity_completeness_r1 import (
    complete_root_with_collection,
    selected_metadata_control,
)


def native_scalar_control(shape, structured, location, name, signature, value):
    contract, observation, judgment, _, _ = selected_metadata_control(
        shape, structured, {"totalCount": 1}
    )
    query = observation["trace"]["tool_calls"][-1]
    schema = observation["trace"]["tool_calls"][0]["output"]
    fields = query["arguments"]["fields"]
    row = query["output"]["data"][0]
    project_fields = row_fields = fields[-1]["fields" if structured else "projects"]
    collection = "items" if shape == "page" else "edges"
    members = project_fields[0]["fields" if structured else collection]
    if shape == "connection":
        members = members[0]["fields" if structured else "node"]
    native_owner = {
        "root": "ShipmentType",
        "manager": "ProjectType",
        "metadata": "ProjectPageInfo",
    }[location]
    if location == "manager":
        fields = members
        row = row["projects"][collection][0]
        if shape == "connection":
            row = row["node"]
    elif location == "metadata":
        fields = row_fields[-1]["fields" if structured else "pageInfo"]
        row = row["projects"]["pageInfo"]
    fields.append({"field": name} if structured else name)
    row[name] = deepcopy(value)
    owner_fields = schema["types"][native_owner].setdefault("fields", {})
    owner_fields[name] = {"type": signature}
    native_fields = {
        "ShipmentType": "id: Int!, code: String!, quantity: Int!, unit: String!, source: String!, shippedAt: String!, projectId: Int!, projects: Wrapper",
        "ProjectType": "id: Int!, code: String!, name: String!",
        "ProjectPageInfo": "totalCount: Int!",
    }
    native_fields[native_owner] += f", {name}: {signature}"
    wrapper = "ProjectPage" if shape == "page" else "ProjectConnection"
    for typename, definitions in native_fields.items():
        # The root capture now also declares the actual unrelated scalar leaves.
        if typename == "ShipmentType":
            owner_fields = schema["types"][typename]["fields"]
            owner_fields.update(
                {
                    field: {"type": field_type}
                    for field, field_type in (
                        ("id", "Int!"),
                        ("code", "String!"),
                        ("quantity", "Int!"),
                        ("unit", "String!"),
                        ("source", "String!"),
                        ("shippedAt", "String!"),
                        ("projectId", "Int!"),
                    )
                }
            )
        native_fields[typename] = definitions.replace("Wrapper", wrapper)
    sdl = "scalar JSON\n" + "\n".join(
        f"type {typename} {{ {definitions} }}"
        for typename, definitions in native_fields.items()
    )
    sdl += """
        type ProjectPage { items: [ProjectType!]!, pageInfo: ProjectPageInfo! }
        type ProjectConnection { edges: [ProjectEdge!]!, pageInfo: ProjectPageInfo! }
        type ProjectEdge { node: ProjectType! }
        type ShipmentPage { items: [ShipmentType!]!, pageInfo: ProjectPageInfo! }
        type Query { shipmentList: ShipmentPage! }
    """

    def render(chosen):
        terms = []
        for field in chosen:
            if isinstance(field, str):
                terms.append(field)
            else:
                field_name = field["field"] if "field" in field else next(iter(field))
                children = (
                    field.get("fields") if "field" in field else field[field_name]
                )
                terms.append(
                    field_name + " { " + render(children) + " }"
                    if isinstance(children, list)
                    else field_name
                )
        return " ".join(terms)

    projection = (
        "{ shipmentList { items { "
        + render(query["arguments"]["fields"])
        + " } pageInfo { totalCount } } }"
    )
    actual = graphql_sync(
        build_schema(sdl),
        projection,
        root_value={
            "shipmentList": {
                "items": query["output"]["data"],
                "pageInfo": {"totalCount": 1},
            }
        },
    )
    assert not actual.errors
    assert actual.data["shipmentList"]["items"] == query["output"]["data"]
    bind_control_trace(observation, judgment)
    return contract, observation, judgment


@pytest.mark.parametrize("shape", ["page", "connection"])
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("location", ["root", "manager", "metadata"])
@pytest.mark.parametrize("name", ["items", "edges"])
@pytest.mark.parametrize(
    "signature,value",
    [
        ("String!", "ordinary_leaf"),
        ("Int!", 27),
        ("Boolean!", True),
        ("[String!]!", ["a", "b"]),
        ("String", None),
        ("JSON", {"edges": [{"node": {"id": 77}}], "pageInfo": {"hasNextPage": True}}),
    ],
)
def test_native_scalar_named_like_collection_preserves_complete_identity(
    shape, structured, location, name, signature, value
):
    contract, observation, judgment = native_scalar_control(
        shape, structured, location, name, signature, value
    )
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    assert observation == before
    assert result["passed"], result["identity_normalization"]
    assert result["dimensions"]["R"]["status"] == "pass"
    assert result["identity_normalization"]["normalized_facts"]["result_ids"] == ["P01"]


@pytest.mark.parametrize("shape", ["page", "connection"])
@pytest.mark.parametrize("extra", ["leaf", "compound"])
def test_mixed_or_duplicate_compound_collection_selection_stays_incomplete(
    shape, extra
):
    call, _, fields = complete_root_with_collection(shape)
    collection = "items" if shape == "page" else "edges"
    fields.append(collection if extra == "leaf" else deepcopy(fields[0]))
    assert complete_identity_queries({"q": call}) == set()
