"""JSON-distinct values never share origin authority through Python equality."""

from copy import deepcopy
import json

import pytest

from experiments.gm_eval import adjudication as api
from tests.experiments.test_gm_eval_judge_transport import (
    discovery_run,
    schema_request,
    schema_run,
)
from tests.experiments.test_gm_eval_adjudication import saved_run


PAIRS = [(False, 0), (True, 1), (2, 2.0), (0, False), (None, 0), ("0", 0), ([], {})]


def with_default(original):
    run = discovery_run()
    for turn in run["turns"]:
        result = turn["events"][1]["result"]
        result[0]["fields"] = [{"name": "flag", "default": deepcopy(original)}]
        turn["durable_messages"][-1]["tool_result"] = deepcopy(result)
    run["turns"][1]["history"][0]["content"] = (
        "Historical tool data (search_managers): "
        + json.dumps(run["turns"][0]["events"][1]["result"])
    )
    return run


@pytest.mark.parametrize("original,replacement", PAIRS)
def test_builder_does_not_select_json_distinct_durable_discovery_result(
    original, replacement
):
    run = with_default(original)
    run["turns"][0]["durable_messages"][-1]["tool_result"][0]["fields"][0][
        "default"
    ] = replacement
    req = schema_request(run)
    assert not req.get("schema_transport_sources")
    assert not any(
        slot.source == "judge_history_schema"
        for slot in api.judge_messages(req)[1].schema_slots
    )
    schema = api._schema(run, 1, None)
    assert (
        api._historical_discovery_calls(
            run, 1, schema, api._visible_history(run["turns"][1])
        )
        == []
    )


def test_builder_does_not_bind_float_contract_version_to_integer_source():
    run = discovery_run()
    run["turns"][0]["durable_messages"][-1]["tool_result"][0]["contract_version"] = 2.0
    assert not schema_request(run).get("schema_transport_sources")


@pytest.mark.parametrize("original,replacement", PAIRS)
def test_bound_discovery_proof_type_change_is_rejected_before_provider(
    original, replacement
):
    req = schema_request(with_default(original))
    assert req["schema_transport_sources"]
    req["schema_transport_sources"][0]["durable"]["tool_result"][0]["fields"][0][
        "default"
    ] = replacement
    req["request_sha256"] = api._request_digest(req)
    with pytest.raises(api.AdjudicationError, match="invalid_schema_transport_source"):
        api.judge_messages(req)


@pytest.mark.parametrize("original,replacement", PAIRS)
def test_schema_argument_binding_is_json_exact(original, replacement):
    run = schema_run()
    for turn in run["turns"]:
        turn["events"][0]["args"]["extension"] = {"value": deepcopy(original)}
        turn["durable_messages"][-1]["tool_args"] = deepcopy(turn["events"][0]["args"])
    run["turns"][0]["durable_messages"][-1]["tool_args"]["extension"]["value"] = (
        replacement
    )
    assert not schema_request(run).get("schema_transport_sources")
    valid = schema_run()
    for turn in valid["turns"]:
        turn["events"][0]["args"]["extension"] = {"value": deepcopy(original)}
        turn["durable_messages"][-1]["tool_args"] = deepcopy(turn["events"][0]["args"])
    req = schema_request(valid)
    req["schema_transport_sources"][0]["durable"]["tool_args"]["extension"]["value"] = (
        replacement
    )
    req["request_sha256"] = api._request_digest(req)
    with pytest.raises(api.AdjudicationError, match="invalid_schema_transport_source"):
        api.judge_messages(req)


@pytest.mark.parametrize("part", ["args", "durable_result", "history_result"])
@pytest.mark.parametrize("original,replacement", PAIRS[:3])
def test_historical_query_provenance_uses_exact_json_args_and_results(
    part, original, replacement
):
    run = deepcopy(saved_run(two_turns=True))
    turn = run["turns"][0]
    turn["events"][0]["args"]["variables"] = {"selected": deepcopy(original)}
    turn["events"][1]["result"]["data"][0]["selected"] = deepcopy(original)
    # saved_run aliases its event/durable dictionaries; deliberately separate them.
    durable = deepcopy(turn["durable_messages"][0])
    turn["durable_messages"][0] = durable
    durable["tool_args"] = deepcopy(turn["events"][0]["args"])
    durable["tool_result"] = deepcopy(turn["events"][1]["result"])
    visible = deepcopy(turn["events"][1]["result"])
    if part == "args":
        durable["tool_args"]["variables"]["selected"] = replacement
    elif part == "durable_result":
        durable["tool_result"]["data"][0]["selected"] = replacement
    else:
        visible["data"][0]["selected"] = replacement
    history = [
        {
            "role": "assistant",
            "content": "Historical tool data (query): " + json.dumps(visible),
        }
    ]
    calls, original_evidence, reused = api._history_sources(
        run, 1, run["schema_index"], history
    )
    assert calls == original_evidence == reused == []


@pytest.mark.parametrize("original,replacement", PAIRS[:3])
def test_packet_and_observation_cannot_bind_json_distinct_tool_results(
    original, replacement
):
    req = schema_request(with_default(original))
    req["packet"] = deepcopy(req["packet"])
    req["packet"]["tool_calls"][0]["output"][0]["fields"][0]["default"] = replacement
    req["request_sha256"] = api._request_digest(req)
    with pytest.raises(api.AdjudicationError, match="stale_adjudication_evidence"):
        api.judge_messages(req)


def test_identical_json_with_reordered_object_keys_keeps_origin_and_roundtrip():
    from general_manager.chat.planned.schema_projection import logical_messages

    run = with_default({"active": False, "limit": 2, "nested": [None, "2", 2.0]})
    for turn in run["turns"]:
        durable = turn["durable_messages"][-1]
        durable["tool_args"] = dict(reversed(list(durable["tool_args"].items())))
        durable["tool_result"][0] = dict(
            reversed(list(durable["tool_result"][0].items()))
        )
    req = schema_request(run)
    assert req["schema_transport_sources"]
    restored = json.loads(
        logical_messages(api.judge_messages(req))[1].content.removeprefix(
            "REFERENCE_DATA="
        )
    )
    assert json.dumps(restored, sort_keys=True) == json.dumps(
        {"request_sha256": req["request_sha256"], **req["packet"]}, sort_keys=True
    )
