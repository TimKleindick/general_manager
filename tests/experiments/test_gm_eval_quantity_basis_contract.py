"""Prospective judge transport; literal metrics and topics remain separate.

Wire replay responses are explicit test doubles, never model-quality evidence.
"""

import asyncio
from copy import deepcopy
import json

import pytest

from experiments.gm_eval import adjudication
from experiments.gm_eval.profiles import siwc_factory
from tests.experiments.test_gm_eval_adjudication import request, response, saved_run
from tests.experiments.test_gm_eval_scoring import control, expected, score


class WireReplay:
    def __init__(self, response):
        self.response = response
        self.bodies = []

    async def stream(self, body):
        self.bodies.append(body)
        yield {"type": "response.output_text.delta", "delta": self.response}
        yield {"type": "response.completed", "response": {"output": []}}


RULE_PARTS = (
    "Explicit gross actual shipment results or aggregate totals use gross_shipped_quantity",
    "even without a gross-versus-net comparison",
    "does not relabel historical year-by-year series",
    "Historical series of unadjusted shipped quantities use shipped_quantity",
    "gross qualifier alone does not turn a historical series into a gross-versus-net comparison",
    "Unadjusted existing shipment-plan quantities in pieces use shipped_quantity",
    "gross qualifier alone does not turn a plan series into a gross-versus-net comparison",
    "Net-after-returns claims",
    "Preserve an incorrect or unsupported qualifier",
)
ANSWERS = {
    "en": (
        "The actual shipment total is gross, before returns.",
        "The existing plan series is expressed in gross pieces.",
        "The actual quantity is net after returns.",
        "The historical year-by-year series shows gross shipped pieces, without deducting returns.",
    ),
    "de": (
        "Die tatsächliche Liefersumme ist brutto, vor Retouren.",
        "Die vorhandene Planserie ist in Bruttostück angegeben.",
        "Die tatsächliche Menge ist netto nach Retouren.",
        "Die historische Jahresreihe zeigt Bruttostück, ohne Retouren abzuziehen.",
    ),
    "fr": (
        "Le total des livraisons réelles est brut, avant les retours.",
        "La série du plan existant est exprimée en pièces brutes.",
        "La quantité réelle est nette après les retours.",
        "La série historique annuelle présente les pièces livrées brutes, sans déduire les retours.",
    ),
}


@pytest.mark.parametrize("language", ANSWERS)
@pytest.mark.parametrize("variant", range(4))
def test_actual_siwc_judge_body_receives_generic_basis_contract(language, variant):
    run = saved_run()
    answer = ANSWERS[language][variant]
    turn = run["turns"][0]
    turn["answer"] = answer
    turn["events"][-2]["content"] = answer
    original = request()
    from tests.experiments.test_gm_eval_adjudication import expectation

    packet = adjudication.build_adjudication_request(expectation(), run, 0)
    before = deepcopy(packet)
    value = json.loads(response(packet))
    # The base fixture cites wording absent from these translated answers.
    value["citations"] = []
    replay = WireReplay(json.dumps(value))
    provider = siwc_factory(replay)({"model": "offline", "reasoning": "medium"})
    result = asyncio.run(
        adjudication.adjudicate_turn(packet, judge_id="offline-wire", judge=provider)
    )
    assert result["status"] == "completed"
    assert packet == before
    assert len(replay.bodies) == 1
    body = replay.bodies[0]
    wire = json.dumps(body, ensure_ascii=False)
    assert body["reasoning"] == {"effort": "medium"}
    assert not body.get("tools")
    assert len(json.dumps(body)) <= 200000
    for part in RULE_PARTS:
        assert part in wire
    assert answer in wire
    assert original["schema_version"] == packet["schema_version"] == "1.5"


@pytest.mark.parametrize(
    "case_id,turn,wrong_metric",
    [
        ("E021", 0, "shipped_quantity"),
        ("E033", 0, "shipped_quantity"),
        ("E010", 0, "gross_shipped_quantity"),
        ("E010", 0, "net_shipped_quantity"),
        ("E019", 1, "gross_shipped_quantity"),
        ("E031", 1, "gross_shipped_quantity"),
        ("E043", 1, "gross_shipped_quantity"),
        ("E027", 0, "gross_shipped_quantity"),
    ],
)
def test_wrong_metric_still_fails_without_alias_repair(case_id, turn, wrong_metric):
    contract = expected(case_id, turn)
    observation, judgment = control(contract)
    assert score(contract, observation, judgment)["passed"]
    assert contract["facts"]["metric"] != wrong_metric
    observation["facts"]["metric"] = wrong_metric
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    check = next(
        c for c in result["dimensions"]["C"]["checks"] if c["field"] == "metric"
    )
    assert check["status"] == "fail"
    assert check["observed"] == wrong_metric
    assert observation == before


def test_metric_and_criterion_are_not_scorer_aliases():
    contract = expected("E030", 0)
    observation, judgment = control(contract)
    observation["facts"]["clarification_topics"] = ["metric", "horizon"]
    before = deepcopy(observation)
    result = score(contract, observation, judgment)
    check = next(
        c
        for c in result["dimensions"]["Q"]["checks"]
        if c["field"] == "clarification_topics"
    )
    assert check["status"] == "fail" and check["expected"] == ["criterion"]
    assert observation == before


def test_criterion_with_relevant_horizon_remains_admissible():
    contract = expected("E030", 0)
    observation, judgment = control(contract)
    observation["facts"]["clarification_topics"] = ["criterion", "horizon"]
    assert score(contract, observation, judgment)["passed"]
