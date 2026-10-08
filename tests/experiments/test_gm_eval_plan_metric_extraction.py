"""Prospective source/basis policy delivery; existing literal metric failures remain."""

import asyncio

import pytest

from experiments.gm_eval import adjudication
from experiments.gm_eval.cli import source_manifest
from general_manager.chat.providers.base import DoneEvent, TextChunkEvent, TokenUsage
from tests.experiments.test_gm_eval_adjudication import OfflineJudge, request, response
from tests.experiments.test_gm_eval_scoring import control, expected, score


def test_blind_judge_receives_explicit_existing_plan_identity_and_basis_rule():
    packet = request()
    provider = OfflineJudge(
        [TextChunkEvent(response(packet)), DoneEvent(TokenUsage(1, 2))]
    )
    result = asyncio.run(
        adjudication.adjudicate_turn(packet, judge_id="offline", judge=provider)
    )
    assert result["status"] == "completed"
    instruction = provider.seen[0].content
    assert (
        "Unadjusted existing shipment-plan quantities in pieces use shipped_quantity"
        in instruction
    )
    assert (
        "gross qualifier alone does not turn a plan series into a gross-versus-net comparison"
        in instruction
    )
    assert "Preserve an incorrect or unsupported qualifier" in instruction
    assert "never normalize away a net/gross" in instruction


@pytest.mark.parametrize(
    "metric", ["gross_shipped_quantity", "net_shipped_quantity", "shipped_weight"]
)
def test_no_global_metric_alias_or_scorer_relaxation_for_plan(metric):
    contract = expected("E010")
    observation, judgment = control(contract)
    observation["facts"]["metric"] = metric
    result = score(contract, observation, judgment)
    check = next(
        x for x in result["dimensions"]["C"]["checks"] if x["field"] == "metric"
    )
    assert check["status"] == "fail" and check["observed"] == metric
    assert check["expected"] == "shipped_quantity"


def test_concrete_selector_integration_is_version_hashed():
    manifest = source_manifest()
    assert "src/general_manager/chat/planned/selector_clarification.py" in manifest
