"""Real native reads and binding/calculation/completion, with offline providers."""

import json
import os
from pathlib import Path

import pytest

from tests.experiments.test_gm_eval_harness import _child


@pytest.mark.parametrize("manager_count", [5, 50, 250])
def test_complete_native_deferred_calculation_sequence(manager_count):
    result = _child(PROGRAM.replace("MANAGER_COUNT", str(manager_count)))
    if directory := os.environ.get("GM_SCHEMA_EVIDENCE_DIR"):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"deferred-calculation-{manager_count}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    assert result["status"] == "infrastructure_check"
    assert result["persistence_verified"]
    assert result["all_guarded_requests_fit"]
    assert result["native_quantity_sum_matches_calculation"]
    assert result["binding_hint_visible_before_assignment"]
    assert result["binding_hint_absent_after_assignment"]
    assert result["scope_population_complete"]
    assert result["scope_unit"] == "pieces"
    assert result["HTTP_POSTs"] == 0
    assert result["model_performance_measured"] is False
    assert any(
        path.endswith("/deferred_calculations.py") for path in result["source_hashes"]
    )
    assert result["cumulative_request_characters"] == sum(
        x["request_characters"] for x in result["exchanges"]
    )


PROGRAM = r"""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=MANAGER_COUNT)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.siwc_eval.provider import payload
from general_manager.chat.providers.base import DoneEvent,TextChunkEvent,TokenUsage,ToolCallEvent
from general_manager.chat.planned.schema_projection import logical_messages

exchanges=[]
stage=0
before=False
after=False
def factory(config):
 class Provider:
  async def complete(self,messages,tools):
   global stage,before,after
   body=payload(config["model"],messages,tools)
   guarded=len(json.dumps(body))
   body={**body,"reasoning":{"effort":"medium"}}
   item={"role":config["role"],"request":body,"guarded_characters":guarded,"request_characters":len(json.dumps(body))}
   exchanges.append(item)
   logical=logical_messages(messages)
   ref=json.loads(next(m.content for m in reversed(logical) if m.role=="user" and m.content.startswith(("REFERENCE_DATA=","RESOLVED_REFERENCE_DATA="))).split("=",1)[1])
   if "catalog_and_schema_summary" in ref:
    response={"intent":"read","tasks":[{"task_id":"quantity_task","objective":"Sum the complete observed native shipment quantity in its actual unit","depends_on":[],
      "requirements":[{"requirement_id":"schema","kind":"schema","description":"Inspect the exposed shipment fields","operation":None,"schema":{"manager":"Shipment","view":"overview","types":[],"snapshot":"current"}},
                      {"requirement_id":"rows","kind":"query","description":"Read all exposed shipment quantities and actual units","operation":None},
                      {"requirement_id":"total","kind":"calculation","description":"Sum the complete observed quantity in pieces","operation":"sum","binding":None}],
      "completion_criteria":["schema","rows","total"],"routing_features":["requires_calculation"]}]}
    event=TextChunkEvent(json.dumps(response))
   elif "resolved_evidence" in ref:
    event=TextChunkEvent(json.dumps({"answer":"The complete verified quantity sum is available in pieces.","evidence_ids":[e["evidence_id"] for e in ref["resolved_evidence"]]}))
   else:
    now=stage
    stage+=1
    if now==0:
     assert ref["deferred_calculations"][0]["required_action"]=="bind_calculation"
     event=ToolCallEvent("schema","get_manager_schema",{"manager":"Shipment"})
    elif now==1:
     schema=next(e["payload"] for e in ref["task_evidence"] if e["kind"]=="schema")
     assert "quantity" in schema["output_fields"] and "unit" in schema["output_fields"]
     event=ToolCallEvent("rows","query",{"manager":"Shipment","fields":["quantity","unit"],"filters":{},"limit":200})
    elif now==2:
     source=next(e for e in ref["task_evidence"] if e["kind"]=="query")
     hints=ref["deferred_calculations"][0]["possible_source_requirements"]
     before=any(source["evidence_id"] in h["evidence_ids"] for h in hints)
     event=TextChunkEvent(json.dumps({"action":"bind_calculation","requirement_id":"total","binding":{"source_requirement_ids":["rows"],"value_path":["quantity"],"group_by":[],"unit_path":["unit"]}}))
    elif now==3:
     after="deferred_calculations" not in ref
     source=next(e for e in ref["task_evidence"] if e["kind"]=="query")
     event=TextChunkEvent(json.dumps({"action":"calculate","requirement_id":"total","operation":"sum","operands":[{"evidence_id":source["evidence_id"],"path":["data",i,"quantity"]} for i in range(len(source["payload"]["data"]))]}))
    else:
     assert now==4,ref.get("action_validation_error")
     event=TextChunkEvent(json.dumps({"action":"complete","evidence_ids":[e["evidence_id"] for e in ref["task_evidence"] if e["requirement_ids"]]}))
   item["response"]={"type":"tool_call","id":event.id,"name":event.name,"arguments":event.args} if isinstance(event,ToolCallEvent) else {"type":"text","text":event.content}
   yield event
   yield DoneEvent(TokenUsage())
 return Provider()

async def run():
 report=await run_case(runtime,{"id":"native-deferred-calculation","turns":["Sum all exposed native shipment quantities in pieces."]},make_profile("strong-only",strong_model="gpt-6-astra"),factory)
 turn=report["turns"][0]
 query=next(e["result"] for e in turn["tool_results"] if e["name"]=="query")
 synthesis=next(x for x in report["trace"]["provider_calls"] if x["role"]=="synthesizer")
 ref=json.loads(next(m["content"] for m in synthesis["messages"] if m["content"].startswith("RESOLVED_REFERENCE_DATA=")).split("=",1)[1])
 calc=next(e["payload"] for e in ref["resolved_evidence"] if e["kind"]=="calculation")
 return {"status":report["status"],"persistence_verified":turn["persistence_verified"],"all_guarded_requests_fit":all(x["guarded_characters"]<=200000 for x in exchanges),
         "native_quantity_sum_matches_calculation":calc["value"]==sum(r["quantity"] for r in query["data"]),"binding_hint_visible_before_assignment":before,"binding_hint_absent_after_assignment":after,
         "scope_population_complete":calc["scope"]["population_complete"],"scope_unit":calc["scope"]["unit"],"manager_count":MANAGER_COUNT,"exchanges":exchanges,
         "cumulative_request_characters":sum(x["request_characters"] for x in exchanges),"native_query":query,"framework_calculation":calc,"source_hashes":report["source_hashes"],
         "HTTP_POSTs":0,"model_performance_measured":False}
try:print(json.dumps(asyncio.run(run())))
finally:runtime.close()
"""
