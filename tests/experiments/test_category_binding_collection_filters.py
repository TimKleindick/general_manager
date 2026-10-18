"""Common collection input codecs must retain scalar category authority."""

from __future__ import annotations

import pytest

from tests.experiments.test_gm_eval_harness import _child


PROBE = r"""
import json, socket, sys

def no_network(*args, **kwargs):
    raise AssertionError("offline_category_probe_network_forbidden")

socket.socket.connect = no_network
from experiments.gm_eval.runtime import bootstrap
runtime = bootstrap(manager_count=int(sys.argv[4]))
try:
    from dataclasses import replace
    from graphql import get_named_type
    from general_manager.api.graphql import GraphQL
    from general_manager.chat.graphql_contract import clear_contract_cache
    from general_manager.chat.planned.models import SchemaBinding
    from tests.unit.test_chat_category_value_binding_r26 import _public_runner, _call

    manager, view, mutation = sys.argv[1:4]
    field = "family" if manager == "Material" else "code"
    value = "steel" if manager == "Material" else "C01"
    fields = (
        ["id", "code", "name", "family", "active", "isActive"]
        if manager == "Material" else ["id", "code", "name"]
    )
    overview = runtime.tool("get_manager_schema", {"manager": manager, "view": "overview"})
    root = overview["roots"][0]
    input_name = overview["root_fields"][root]["arguments"]["filter"]["type"]
    selectors = [overview["type"], input_name]
    runner = _public_runner(manager)
    task = runner.runtimes["task_1"].task
    if view == "detail":
        runner.runtimes["task_1"].task = replace(
            task,
            requirements=(
                replace(task.requirements[0], schema=SchemaBinding(manager, "detail", tuple(selectors), "current")),
                *task.requirements[1:],
            ),
        )
    args = {"manager": manager, "view": view}
    if view == "detail":
        args.update(types=selectors, snapshot=overview["snapshot"])
    schema, _ = _call(runner, "get_manager_schema", **args)
    lookup, _ = _call(
        runner, "query", manager=manager, fields=fields,
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    assert lookup["complete"] is True and lookup["has_more"] is False
    assert value in [row[field] for row in lookup["data"]]
    bound = {
        "manager": manager,
        "fields": fields,
        "filters": {field + "_Exact": value},
        "category_bindings": [{
            "schema_evidence_id": "task_1:schema:1",
            "query_evidence_id": "task_1:query:2",
            "field": field,
            "filter_path": ["filters", field + "_Exact"],
        }],
    }
    if manager == "Material":
        bound["filters"]["isActive_Exact"] = True
    result, progress = _call(runner, "query", **bound)
    expected = ["M02", "M04", "M05"] if manager == "Material" else ["C01"]
    assert "data" in result, result
    assert [row["code"] for row in result["data"]] == expected, result
    assert progress and result["complete"] is True
    if manager == "Material":
        legacy = next(row for row in result["data"] if row["code"] == "M05")
        assert legacy["active"] is False and legacy["isActive"] is True
    signature = schema["types"][input_name]["fields"][field + "_Exact"]
    assert signature["filter_contract"] == {
        "source": "interface_filter", "field": field, "operator": "exact",
    }
    if mutation != "none":
        native = GraphQL.get_schema().graphql_schema.get_type(input_name)
        wrappers = [
            get_named_type(item.type) for item in native.fields.values()
            if hasattr(get_named_type(item.type), "fields")
            and set(get_named_type(item.type).fields) == {"any", "none"}
        ]
        assert wrappers, "real collection wrapper required"
        wrapper = wrappers[0]
        if mutation == "container":
            wrapper.out_type = dict
        elif mutation == "default":
            wrapper.fields["any"].default_value = {}
        elif mutation == "field_removed":
            wrapper.fields.pop("none")
        elif mutation == "generated_field":
            wrapper.graphene_type._meta.fields["any"].default_value = {}
        else:
            raise AssertionError("unknown mutation")
        clear_contract_cache()
        current = runtime.tool("get_manager_schema", {"manager": manager, "view": "full"})
        assert "filter_contract" not in current["types"][input_name]["fields"][field + "_Exact"]
        before = len(runner.evidence.records)
        rejected, progress = _call(runner, "query", **bound)
        assert rejected["code"] == "invalid_category_binding" and not progress
        assert len(runner.evidence.records) == before
    print(json.dumps({"codes": expected, "view": view, "mutation": mutation, "manager_count": int(sys.argv[4]), "model_POSTs": 0}))
finally:
    runtime.close()
"""


def _probe(
    manager: str, view: str, mutation: str = "none", manager_count: int = 5
) -> dict:
    arguments = ["category-filter-probe", manager, view, mutation, str(manager_count)]
    return _child(
        f"import sys\nsys.argv = {arguments!r}\n" + PROBE,
        timeout=30,
    )


@pytest.mark.parametrize("manager", ["Material", "Customer"])
@pytest.mark.parametrize("view", ["full", "detail"])
@pytest.mark.parametrize("manager_count", [5, 10])
def test_collection_input_preserves_scalar_category_binding(
    manager, view, manager_count
):
    observed = _probe(manager, view, manager_count=manager_count)
    assert observed["codes"] == (
        ["M02", "M04", "M05"] if manager == "Material" else ["C01"]
    )
    assert observed["manager_count"] == manager_count
    assert observed["model_POSTs"] == 0


@pytest.mark.parametrize(
    "mutation", ["container", "default", "field_removed", "generated_field"]
)
def test_changed_collection_input_revokes_scalar_category_binding(mutation):
    observed = _probe("Material", "detail", mutation)
    assert observed["mutation"] == mutation
    assert observed["model_POSTs"] == 0
