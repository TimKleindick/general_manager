"""Offline production requests distinguish an open standard from a measurement.

Injected SIWC responses test contract transport and rendering, not model accuracy.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from experiments.gm_eval.profiles import siwc_factory
from general_manager.chat.planned.budget import RoundBudget
from general_manager.chat.planned.clarification import QUESTIONS
from general_manager.chat.planned.planner import plan_request
from general_manager.chat.planned.synthesis import synthesize_answer
from tests.unit.test_chat_planned_planner import _plan, _settings as planner_settings
from tests.unit.test_chat_planned_synthesis import (
    _settings as synthesis_settings,
    _store,
)


RULE_PARTS = (
    "Use criterion when an open evaluative request",
    "Use metric when the analytical objective is already defined",
    "only choices still unresolved",
    "do not ask again",
)
PROMPTS = {
    "en": {
        "criterion": "Which project is most important?",
        "metric": "Analyze the development of deliveries; which measure should we use?",
        "resolved": "Compare planned net revenue in EUR for the next calendar year.",
    },
    "de": {
        "criterion": "Welches Projekt ist am wichtigsten?",
        "metric": "Analysiere die Entwicklung der Lieferungen; welche Messgröße verwenden wir?",
        "resolved": "Vergleiche den geplanten Nettoumsatz in EUR für das nächste Kalenderjahr.",
    },
    "fr": {
        "criterion": "Quel projet est le plus important ?",
        "metric": "Analysons l\N{RIGHT SINGLE QUOTATION MARK}évolution des livraisons ; quel indicateur utiliser ?",
        "resolved": "Comparez le chiffre d\N{RIGHT SINGLE QUOTATION MARK}affaires net prévu en EUR pour la prochaine année civile.",
    },
}


class WireReplay:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.bodies = []

    async def stream(self, body):
        self.bodies.append(body)
        yield {"type": "response.output_text.delta", "delta": self.responses.pop(0)}
        yield {"type": "response.completed", "response": {"output": []}}


@pytest.mark.parametrize("role", ["planner", "synthesizer"])
@pytest.mark.parametrize("choice", ["criterion", "metric", "resolved"])
@pytest.mark.parametrize("language", PROMPTS)
def test_production_requests_transmit_choice_rule_and_visible_user_scope(
    role, choice, language
):
    prompt = PROMPTS[language][choice]
    response = (
        json.dumps(_plan())
        if role == "planner"
        else json.dumps(
            {"answer": "No rows.", "evidence_ids": ["ev-query-1"]}
            if choice == "resolved"
            else {"clarification": {"language": language, "requirements": [choice]}}
        )
    )
    replay = WireReplay(response)
    provider = siwc_factory(replay)({"model": "offline", "reasoning": "medium"})
    module = "planner" if role == "planner" else "synthesis"
    with patch(
        "general_manager.chat.planned." + module + ".build_profile_provider",
        return_value=provider,
    ):
        if role == "planner":
            result = asyncio.run(
                plan_request(prompt, [], planner_settings(), RoundBudget(()), {})
            )
            assert result.plan.intent == "read"
        else:
            result = asyncio.run(
                synthesize_answer(
                    prompt, _store(), {}, synthesis_settings(), RoundBudget(())
                )
            )
            assert result.answer == (
                "No rows." if choice == "resolved" else QUESTIONS[language][choice]
            )
    assert len(replay.bodies) == 1
    body = replay.bodies[0]
    wire = json.dumps(body, ensure_ascii=False)
    assert body["reasoning"] == {"effort": "medium"}
    assert not body.get("tools")
    for part in RULE_PARTS:
        assert part in wire
    assert prompt in wire


@pytest.mark.parametrize("language", PROMPTS)
@pytest.mark.parametrize("choice", ["criterion", "metric"])
def test_existing_synthesis_fallback_transmits_same_choice_rule(language, choice):
    replay = WireReplay(
        [
            json.dumps({"answer": "Ungrounded winner", "evidence_ids": []}),
            json.dumps(
                {"clarification": {"language": language, "requirements": [choice]}}
            ),
        ]
    )
    factory = siwc_factory(replay)
    with patch(
        "general_manager.chat.planned.synthesis.build_profile_provider",
        side_effect=lambda _profile: factory(
            {"model": "offline", "reasoning": "medium"}
        ),
    ):
        result = asyncio.run(
            synthesize_answer(
                PROMPTS[language][choice],
                _store(),
                {},
                synthesis_settings(),
                RoundBudget(()),
            )
        )
    assert result.answer == QUESTIONS[language][choice]
    assert len(replay.bodies) == 2
    for body in replay.bodies:
        instruction = json.dumps(body, ensure_ascii=False)
        assert all(part in instruction for part in RULE_PARTS)
        assert body["reasoning"] == {"effort": "medium"}
        assert not body.get("tools")
