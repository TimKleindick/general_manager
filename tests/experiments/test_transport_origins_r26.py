"""Multiple exact durable origins can share bytes without selecting an authority."""

from copy import deepcopy
import json

import pytest

from experiments.gm_eval import adjudication as api
from experiments.gm_eval.profiles import (
    EvaluationProfile,
    ExperimentProvider,
    make_profile,
)
from general_manager.chat.planned.schema_projection import logical_messages
from tests.experiments.test_gm_eval_judge_transport import schema_request, schema_run


def repeated_run():
    run = schema_run()
    current = deepcopy(run["turns"][1])
    current["turn"] = 3
    run["turns"].append(current)
    # Both verified public views are equal: explicit overview and public default.
    prior = run["turns"][1]
    prior["events"][0]["args"].pop("view")
    prior["durable_messages"][-1]["tool_args"].pop("view")
    current["history"].insert(1, deepcopy(current["history"][0]))
    return run


def multi_request():
    return schema_request(repeated_run(), turn=2)


def test_two_verified_origins_keep_all_call_and_durable_coordinates():
    req = multi_request()
    proofs = req.get("schema_transport_sources", [])
    assert len(proofs) == 2
    for proof in proofs:
        assert proof["format"] == "gm.history-origins/1"
        assert [
            (o["call"]["source_turn"], o["call"]["source_call_id"])
            for o in proof["origins"]
        ] == [(0, "schema-0"), (1, "schema-1")]
        assert proof["origins_sha256"] == api._digest(proof["origins"])
    messages = api.judge_messages(req)
    history_slots = [
        s for s in messages[1].schema_slots if s.source == "judge_history_schema"
    ]
    assert len(history_slots) == 2
    for slot in history_slots:
        bindings = json.loads(slot.binding)["origin"]["origins"]
        assert [b["source_turn"] for b in bindings] == [0, 1]
        assert len({b["arguments_sha256"] for b in bindings}) == 2
        assert all(
            b["snapshot"] == "a" * 64 and b["view"] == "overview" for b in bindings
        )
    restored = json.loads(
        logical_messages(messages)[1].content.removeprefix("REFERENCE_DATA=")
    )
    assert restored == {"request_sha256": req["request_sha256"], **req["packet"]}
    assert restored["visible_history"] == req["packet"]["visible_history"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_durable",
        "tampered_durable",
        "unverified",
        "different_scope",
        "shared_durable",
        "wrong_manager_call",
        "wrong_view_call",
    ],
)
def test_incomplete_or_differently_scoped_origins_remain_literal(mutation):
    run = repeated_run()
    prior = run["turns"][1]
    if mutation == "missing_durable":
        prior["durable_messages"].pop()
    elif mutation == "tampered_durable":
        prior["durable_messages"][-1]["tool_result"]["snapshot"] = "b" * 64
    elif mutation == "unverified":
        prior["persistence_verified"] = False
    elif mutation == "different_scope":
        prior["events"][0]["args"]["fields"] = ["other"]
        prior["durable_messages"][-1]["tool_args"]["fields"] = ["other"]
    elif mutation in {"wrong_manager_call", "wrong_view_call"}:
        key, value = (
            ("manager", "Other")
            if mutation == "wrong_manager_call"
            else ("view", "full")
        )
        prior["events"][0]["args"][key] = value
        prior["durable_messages"][-1]["tool_args"][key] = value
    else:
        pair = deepcopy(prior["events"][:2])
        for event in pair:
            event["id"] = "another-call"
        prior["events"][2:2] = pair
    req = schema_request(run, turn=2)
    assert not req.get("schema_transport_sources")
    assert not any(
        s.source == "judge_history_schema"
        for s in api.judge_messages(req)[1].schema_slots
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_origin",
        "forged_call",
        "tampered_source",
        "duplicate_coordinate",
        "changed_scope",
        "origin_hash",
        "missing_task",
        "typed_task",
    ],
)
def test_missing_forged_or_tampered_origin_fails_before_transport(mutation):
    req = multi_request()
    assert req.get("schema_transport_sources"), "verified complete origin sets missing"
    proof = req["schema_transport_sources"][0]
    if mutation == "missing_origin":
        proof["origins"].pop()
    elif mutation == "origin_hash":
        proof["origins_sha256"] = "0" * 64
    else:
        origin = proof["origins"][1]
        if mutation == "forged_call":
            origin["call"]["source_call_id"] = "forged"
        elif mutation == "tampered_source":
            origin["durable"]["tool_result"]["snapshot"] = "b" * 64
        elif mutation == "duplicate_coordinate":
            proof["origins"][1] = deepcopy(proof["origins"][0])
        elif mutation in {"missing_task", "typed_task"}:
            if mutation == "missing_task":
                origin["call"].pop("task_id")
            else:
                origin["call"]["task_id"] = False
            proof["origins_sha256"] = api._digest(proof["origins"])
        else:
            origin["call"]["arguments"]["fields"] = ["other"]
            origin["durable"]["tool_args"]["fields"] = ["other"]
            proof["origins_sha256"] = api._digest(proof["origins"])
    req["request_sha256"] = api._request_digest(req)
    with pytest.raises(ValueError, match="invalid_schema_transport_source"):
        api.judge_messages(req)


def test_prospective_profiles_declare_separate_reference_version():
    profile = make_profile("strong-only")
    assert profile.schema_transport_version == "gm.schema-data/3"
    assert profile.reference_transport_version == "gm.reference-data/2"
    assert (
        profile.role_config("executor")["reference_transport_version"]
        == "gm.reference-data/2"
    )
    with pytest.raises(ValueError, match="unsupported_reference_transport_version"):
        EvaluationProfile(
            "strong-only",
            "weak",
            "strong",
            reference_transport_version="gm.reference-data/1",
        )
    with pytest.raises(ValueError, match="unsupported_reference_transport_version"):
        ExperimentProvider(
            {"model": "offline", "reference_transport_version": "gm.reference-data/1"}
        )
