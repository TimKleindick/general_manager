"""Structured clarification follows real production delivery and persistence paths."""

import pytest
from tests.experiments.test_gm_eval_harness import _child
from tests.unit.test_chat_planned_clarification import QUESTIONS


@pytest.mark.parametrize("route", ["scheduler", "consumer"])
@pytest.mark.parametrize("language", ["de", "en", "fr"])
@pytest.mark.parametrize("fallback", [False, True])
def test_structured_clarification_is_delivered_and_persisted(route, language, fallback):
    result = _child(
        "config = "
        + repr({"route": route, "language": language, "fallback": fallback})
        + "\n"
        + r"""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import ScriptedFactory
runtime=bootstrap(manager_count=5)
from general_manager.chat.providers.base import ToolCallEvent
case=dict(next(c for c in load_catalog() if c["id"]=="E001"))
case["turns"]=["Which customer is most important?"]
plan={"intent":"read","tasks":[{"task_id":"task_1","objective":"Inspect Customer schema to ground the missing criterion and time period.","depends_on":[],"requirements":[{"requirement_id":"customer_schema","kind":"schema","description":"Inspect Customer schema only.","operation":None}],"completion_criteria":["customer_schema"],"routing_features":[]}]}
question=json.dumps({"clarification":{"language":config["language"],"requirements":["criterion","horizon"]}})
executor=[ToolCallEvent("schema", "get_manager_schema", {"manager":"Customer","view":"full","requirement_id":"customer_schema"}),json.dumps({"action":"complete","evidence_ids":["task_1:schema:1"]})]
responses={"planner":[json.dumps(plan)],"executor":list(executor),"executor":list(executor),"synthesizer":[json.dumps({"answer":"P02 wins with42 EUR?","evidence_ids":[]}) if config["fallback"] else question],"fallback":[question]}
try:
    result=asyncio.run(run_case(runtime,case,make_profile("weak-only"),ScriptedFactory(responses),route=config["route"]))
    print(json.dumps(result))
finally:runtime.close()
"""
    )
    turn = result["turns"][0]
    assert turn["terminal"]["type"] == "done", result
    assert turn["answer"] == QUESTIONS[language]
    assert turn["persistence_verified"] is True
    assert [
        row["content"] for row in turn["durable_messages"] if row["role"] == "assistant"
    ] == [QUESTIONS[language]]
    roles = [call["role"] for call in result["trace"]["provider_calls"]]
    assert roles.count("synthesizer") == 1
    assert roles.count("fallback") == int(fallback)
    assert all(call["completed"] for call in result["trace"]["provider_calls"])
    assert result["mode"] == "offline" and result["model_performance_measured"] is False
