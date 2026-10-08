"""Blind Judge transport contracts, tested without a provider or credentials."""

from copy import deepcopy
import json
import pytest
from experiments.gm_eval import adjudication as api
from tests.experiments.test_gm_eval_adjudication import request, response


def test_echo_instruction_matches_the_only_permitted_response_location():
    packet = request()
    shape = packet["packet"]["response_schema"]
    assert "context_sha256" not in shape.get("properties", {})
    assert (
        "semantic_checks.no_repeated_clarification.context_sha256"
        in api.JUDGE_INSTRUCTION
    )
    assert "only when no_repeated_clarification is requested" in api.JUDGE_INSTRUCTION
    assert "Never add context_sha256 at the top level" in api.JUDGE_INSTRUCTION
    assert "question. Echo context_sha256." not in api.JUDGE_INSTRUCTION


def test_correct_context_hash_at_top_level_is_still_rejected():
    packet = request()
    raw = json.loads(response(packet))
    raw["context_sha256"] = packet["packet"]["context_sha256"]
    result = api.parse_adjudication_response(
        packet, json.dumps(raw), judge_id="offline"
    )
    assert result["status"] == "judge_failure"
    assert result["observation"]["facts"] is None


def schema_run(*, history=True):
    from tests.experiments.test_gm_eval_adjudication import saved_run

    run = saved_run(two_turns=history)
    payload = {
        "manager": "Shipment",
        "schema_view": "overview",
        "snapshot": "a" * 64,
        "type": "ShipmentType",
        "fields": [
            {"name": "description", "description": "Shared schema label " * 500}
        ],
        "output_fields": [{"name": "code", "type": "String!"}],
        "type_manifest": {"ShipmentType": {"kind": "object"}},
    }
    args = {"manager": "Shipment", "view": "overview"}
    for turn_index, turn in enumerate(run["turns"]):
        pair = [
            {
                "type": "tool_call",
                "task_id": "inspect",
                "id": f"schema-{turn_index}",
                "name": "get_manager_schema",
                "args": deepcopy(args),
            },
            {
                "type": "tool_result",
                "task_id": "inspect",
                "id": f"schema-{turn_index}",
                "name": "get_manager_schema",
                "result": deepcopy(payload),
            },
        ]
        turn["events"][:0] = pair
        turn.pop("tool_calls", None)
        turn.pop("tool_results", None)
        turn["durable_messages"].append(
            {
                "role": "tool",
                "content": json.dumps(payload),
                "tool_name": "get_manager_schema",
                "tool_args": deepcopy(args),
                "tool_result": deepcopy(payload),
            }
        )
    if history:
        run["turns"][1]["history"].insert(
            0,
            {
                "role": "assistant",
                "content": "Historical tool data (get_manager_schema): "
                + json.dumps(payload),
            },
        )
    return run


def schema_request(run=None, *, turn=1):
    from tests.experiments.test_gm_eval_adjudication import expectation

    run = schema_run() if run is None else run
    contract = expectation()
    contract["turn_index"] = turn
    return api.build_adjudication_request(contract, run, turn)


def discovery_run():
    run = schema_run()
    result = [
        {
            "contract_version": 2,
            "manager": "Shipment",
            "description": "Shipment " * 500,
            "type": "database",
            "fields": [],
            "filters": [],
            "relations": [],
            "roots": [],
        }
    ]
    for turn in run["turns"]:
        turn["events"][0]["name"] = "search_managers"
        turn["events"][0]["args"] = {"query": "Shipment"}
        turn["events"][1]["name"] = "search_managers"
        turn["events"][1]["result"] = deepcopy(result)
        durable = turn["durable_messages"][-1]
        durable.update(
            tool_name="search_managers",
            tool_args={"query": "Shipment"},
            tool_result=deepcopy(result),
            content=json.dumps(result),
        )
    run["turns"][1]["history"][0]["content"] = (
        "Historical tool data (search_managers): " + json.dumps(result)
    )
    return run


def test_discovery_judge_projection_keeps_real_call_and_durable_binding():
    from general_manager.chat.planned.schema_projection import logical_messages

    req = schema_request(discovery_run())
    messages = api.judge_messages(req)
    assert {s.source for s in messages[1].schema_slots} == {
        "judge_discovery",
        "judge_history_schema",
    }
    binding = json.loads(messages[1].schema_slots[0].binding)
    assert binding["tool"] == "search_managers"
    assert "snapshot" not in binding and "view" not in binding
    assert binding["source_call_id"] == "schema-1"
    assert (
        req["schema_transport_sources"][0]["durable"]["tool_name"] == "search_managers"
    )
    restored = json.loads(
        logical_messages(messages)[1].content.removeprefix("REFERENCE_DATA=")
    )
    assert restored == {"request_sha256": req["request_sha256"], **req["packet"]}


@pytest.mark.parametrize(
    "mutation",
    [
        "no_durable",
        "wrong_args",
        "wrong_result",
        "unverified",
        "user_role",
        "noncanonical",
    ],
)
def test_unbound_discovery_history_stays_literal(mutation):
    run = discovery_run()
    prior, current = run["turns"]
    durable = prior["durable_messages"][-1]
    if mutation == "no_durable":
        prior["durable_messages"].pop()
    elif mutation == "wrong_args":
        durable["tool_args"]["query"] = "Other"
    elif mutation == "wrong_result":
        durable["tool_result"][0]["manager"] = "Other"
    elif mutation == "unverified":
        prior["persistence_verified"] = False
    elif mutation == "user_role":
        current["history"][0]["role"] = "user"
    else:
        current["history"][0]["content"] += " "
    messages = api.judge_messages(schema_request(run))
    assert not any(s.source == "judge_history_schema" for s in messages[1].schema_slots)


def test_judge_projection_restores_every_text_role_index_and_digest():
    from general_manager.chat.planned.schema_projection import (
        REFERENCE_VERSION,
        logical_messages,
        transport_projection,
    )

    req = schema_request()
    before = deepcopy(req)
    messages = api.judge_messages(req)
    assert messages[1].logical_content is not None
    original = logical_messages(messages)[1]
    restored = json.loads(original.content.removeprefix("REFERENCE_DATA="))
    assert restored == {"request_sha256": req["request_sha256"], **req["packet"]}
    assert restored["visible_history"] == req["packet"]["visible_history"]
    assert restored["context_sha256"] == req["packet"]["context_sha256"]
    slots = messages[1].schema_slots
    assert {s.source for s in slots} == {"judge_schema", "judge_history_schema"}
    projection = transport_projection(messages[1].content)
    assert projection["format"] == REFERENCE_VERSION
    root = projection["occurrences"][0]
    assert root["path"] == [] and root["source"] == "judge_reference"
    assert json.loads(root["binding"]) == {
        "scope": "judge",
        "owner": req["request_sha256"],
    }
    assert root["sha256"] == messages[1].reference_binding.sha256
    assert root["text_format"] is None
    assert len(projection["occurrences"][1:]) == 2
    assert [
        (row["path"], row["source"], row["binding"])
        for row in projection["occurrences"][1:]
    ] == [(list(slot.path), slot.source, slot.binding) for slot in slots]
    assert len(json.dumps(messages[1].content)) < len(json.dumps(original.content))
    history_binding = json.loads(
        next(s.binding for s in slots if s.source == "judge_history_schema")
    )
    assert history_binding["origin"]["source_call_id"] == "schema-0"
    assert history_binding["origin"]["source_turn"] == 0
    assert req == before


@pytest.mark.parametrize(
    "mutation",
    [
        "no_durable",
        "wrong_args",
        "wrong_result",
        "unverified",
        "user_role",
        "noncanonical",
    ],
)
def test_unbound_history_stays_literal(mutation):
    run = schema_run()
    prior = run["turns"][0]
    history = run["turns"][1]["history"][0]
    durable = prior["durable_messages"][-1]
    if mutation == "no_durable":
        prior["durable_messages"].pop()
    elif mutation == "wrong_args":
        durable["tool_args"]["manager"] = "Other"
    elif mutation == "wrong_result":
        durable["tool_result"]["snapshot"] = "b" * 64
    elif mutation == "unverified":
        prior["persistence_verified"] = False
    elif mutation == "user_role":
        history["role"] = "user"
    elif mutation == "noncanonical":
        history["content"] += " "
    req = schema_request(run)
    messages = api.judge_messages(req)
    assert not any(s.source == "judge_history_schema" for s in messages[1].schema_slots)
    from general_manager.chat.planned.schema_projection import logical_messages

    restored = logical_messages(messages)[1].content.removeprefix("REFERENCE_DATA=")
    assert json.loads(restored)["visible_history"] == req["packet"]["visible_history"]


@pytest.mark.parametrize(
    "mutation", ["index", "source_turn", "call", "payload", "history_text"]
)
def test_bound_history_tampering_fails_before_provider(mutation):
    req = schema_request()
    assert req.get("schema_transport_sources")
    if mutation == "history_text":
        req["packet"]["visible_history"][0]["content"] += " "
        req["observation"]["trace"]["conversation_context"]["visible_history"] = (
            deepcopy(req["packet"]["visible_history"])
        )
        from experiments.gm_eval.semantic_contracts import context_digest

        req["packet"]["context_sha256"] = context_digest(
            req["observation"]["trace"]["conversation_context"]
        )
    else:
        proof = req["schema_transport_sources"][0]
        if mutation == "index":
            proof["history_index"] = 999
        elif mutation == "source_turn":
            proof["call"]["source_turn"] = 1
        elif mutation == "call":
            proof["durable"]["tool_args"]["manager"] = "Other"
        elif mutation == "payload":
            proof["durable"]["tool_result"]["snapshot"] = "b" * 64
    req["request_sha256"] = api._request_digest(req)
    with pytest.raises(ValueError):
        api.judge_messages(req)


def test_legacy_request_without_bindings_remains_valid_and_literal():
    req = request()
    assert "schema_transport_sources" not in req
    message = api.judge_messages(req)[1]
    assert message.logical_content is None
    assert json.loads(message.content) == {
        "request_sha256": req["request_sha256"],
        **req["packet"],
    }


def test_native_default_overview_call_has_a_bound_schema_slot():
    run = schema_run()
    for turn in run["turns"]:
        for event in turn["events"]:
            if (
                event.get("name") == "get_manager_schema"
                and event["type"] == "tool_call"
            ):
                event["args"].pop("view")
        turn["durable_messages"][-1]["tool_args"].pop("view")
    messages = api.judge_messages(schema_request(run))
    assert len(messages[1].schema_slots) == 2
    assert messages[1].logical_content is not None


def test_request_build_and_binding_validation_need_no_django_or_provider_setup():
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(  # noqa: S603 -- fixed interpreter and test script, no user input
        [
            sys.executable,
            "-B",
            "-c",
            "from tests.experiments.test_gm_eval_judge_transport import schema_request; from experiments.gm_eval.adjudication import _validate_request; r=schema_request(); _validate_request(r); assert r['schema_transport_sources']",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_invalid_bound_source_never_enters_injected_provider():
    import asyncio
    from tests.experiments.test_gm_eval_adjudication import OfflineJudge

    req = schema_request()
    req["schema_transport_sources"][0]["history_index"] = -1
    req["request_sha256"] = api._request_digest(req)
    provider = OfflineJudge([])
    result = asyncio.run(api.adjudicate_turn(req, judge_id="offline", judge=provider))
    assert result["status"] == "judge_failure"
    assert result["judge_calls"] == 0
    assert provider.seen == []


def test_distinct_snapshots_and_definitions_are_preserved_by_transport():
    from general_manager.chat.planned.schema_projection import logical_messages

    run = schema_run()
    output = next(
        e["result"]
        for e in run["turns"][1]["events"]
        if e.get("name") == "get_manager_schema" and e["type"] == "tool_result"
    )
    output["snapshot"] = "b" * 64
    output["types"] = {"Status": {"kind": "enum", "values": ["OPEN", "ARCHIVED"]}}
    run["turns"][1]["durable_messages"][-1]["tool_result"] = deepcopy(output)
    req = schema_request(run)
    messages = api.judge_messages(req)
    restored = json.loads(
        logical_messages(messages)[1].content.removeprefix("REFERENCE_DATA=")
    )
    assert restored == {"request_sha256": req["request_sha256"], **req["packet"]}
    assert restored["tool_calls"][0]["output"]["snapshot"] == "b" * 64
    assert restored["tool_calls"][0]["output"]["types"]["Status"]["values"] == [
        "OPEN",
        "ARCHIVED",
    ]
    assert len(messages[1].schema_slots) == 2


def test_ambiguous_historical_schema_origin_remains_literal():
    run = schema_run()
    turn = run["turns"][0]
    original = turn["events"][:2]
    duplicate = deepcopy(original)
    for event in duplicate:
        event["id"] = "other-schema-call"
    turn["events"][2:2] = duplicate
    req = schema_request(run)
    assert not req.get("schema_transport_sources")
    assert not any(
        s.source == "judge_history_schema"
        for s in api.judge_messages(req)[1].schema_slots
    )
