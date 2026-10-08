"""Actual discovery/details/query exchanges, with offline wire-size evidence."""

import json
import os
from pathlib import Path

import pytest

from tests.experiments.test_gm_eval_harness import _child


@pytest.mark.parametrize("manager_count", [5, 50, 250])
def test_complete_selective_sequences_execute_across_three_real_interfaces(
    manager_count,
):
    report = _child(PROGRAM.replace("MANAGER_COUNT", str(manager_count)))
    if evidence_dir := os.environ.get("GM_SCHEMA_EVIDENCE_DIR"):
        destination = Path(evidence_dir)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"sequence-{manager_count}.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert report["hidden_relation_rejected"]
    assert report["snapshot_invalidated"]
    assert report["manager_count"] == manager_count
    assert report["live_calls"] == 0
    assert report["model_performance_measured"] is False
    assert set(report["interfaces"]) == {"database", "readonly", "calculation"}
    for sequence in report["sequences"]:
        assert sequence["status"] == "infrastructure_check"
        assert sequence["query_rows"] > 0
        assert sequence["selected_definitions_exact"]
        assert sequence["persistence_verified"]
        assert sequence["cumulative_request_characters"] == sum(
            r["request_characters"] for r in sequence["exchanges"]
        )
        assert sequence["guard_admissible"] == all(
            r["guarded_characters"] <= 200000 for r in sequence["exchanges"]
        )
        if sequence["selective"]:
            assert sequence["guard_admissible"]
        # Explicit full is only an offline size comparator. Overflow is measured,
        # never submitted to a provider and never a production guard exception.
        names = [item["name"] for item in sequence["tools"]]
        assert names[0] == "search_managers" and names[-1] == "query"
        assert names.count("get_manager_schema") == (3 if sequence["selective"] else 1)
        assert any(
            path.endswith("/chat/schema_inspection.py")
            for path in sequence["source_hashes"]
        )


PROGRAM = r"""
import asyncio,json,re
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=MANAGER_COUNT)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.siwc_eval.provider import payload
from general_manager.chat.providers.base import DoneEvent,TextChunkEvent,TokenUsage,ToolCallEvent
from general_manager.chat.planned.schema_projection import logical_messages
from general_manager.chat.graphql_contract import manager_schema,ChatReadContractError

def named(signature): return re.sub(r"[\[\]!]", "", signature)
def observation(reference, feedback):
 result=feedback[-1].tool_result
 return next((item["payload"] for item in reference["task_evidence"] if item["evidence_id"]==result.get("evidence_ref")),result)

async def sequence(manager,selective):
 exchanges=[]
 overview=None
 definitions={}
 exact=True
 stage=0
 def factory(config):
  class Provider:
   async def complete(self,messages,tools):
    nonlocal overview,definitions,exact,stage
    body=payload(config["model"],messages,tools)
    guarded=len(json.dumps(body))
    body={**body,"reasoning":{"effort":"medium"}}
    exchange={"role":config["role"],"request":body,"guarded_characters":guarded,"request_characters":len(json.dumps(body))}
    exchanges.append(exchange)
    logical=logical_messages(messages)
    reference=json.loads(next(message.content for message in reversed(logical) if message.role=="user" and message.content.startswith(("REFERENCE_DATA=","RESOLVED_REFERENCE_DATA="))).split("=",1)[1])
    feedback=[message for message in logical if message.role=="tool"]
    if "catalog_and_schema_summary" in reference:
     response={"intent":"read","tasks":[{"task_id":"task_1","objective":"Read "+manager,"depends_on":[],
       "requirements":[{"requirement_id":"schema","kind":"schema","description":"Observe executable signatures","operation":None,
                        "schema":{"manager":manager,"view":"overview","types":[],"snapshot":"current"}},
                       {"requirement_id":"rows","kind":"query","description":"Read the selected population","operation":None}],
       "completion_criteria":["schema","rows"],"routing_features":[]}]}
     event=TextChunkEvent(json.dumps(response))
    elif "resolved_evidence" in reference:
     response={"answer":"Offline infrastructure sequence completed.","evidence_ids":[item["evidence_id"] for item in reference["resolved_evidence"]]}
     event=TextChunkEvent(json.dumps(response))
    else:
     current=stage
     stage+=1
     if current==0:
      event=ToolCallEvent("discover","search_managers",{"query":"calculated net planned revenue exchange rate" if manager=="ProjectCommercial" else manager})
     elif current==1:
      assert any(item["manager"]==manager for item in feedback[-1].tool_result)
      event=ToolCallEvent("overview","get_manager_schema",{"manager":manager,**({} if selective else {"view":"full"})})
     elif current==2:
      overview=observation(reference,feedback)
      assert overview["schema_view"]==("overview" if selective else "full")
      root=overview["roots"][0]
      args=overview["root_fields"][root]["arguments"]
      if selective:
       types=[named(args[key]["type"]) for key in ("filter","orderBy")]
       assert all(name in overview["type_manifest"] for name in types)
       event=ToolCallEvent("inputs","get_manager_schema",{"manager":manager,"view":"detail","types":types,"snapshot":overview["snapshot"]})
      else:
       definitions=overview["types"]
       event=query_call(manager,overview,definitions)
     elif selective and current==3:
      detail=observation(reference,feedback)
      full=manager_schema(manager)
      exact=exact and all(value==full["types"][name] for name,value in detail["types"].items())
      definitions.update(detail["types"])
      enums=sorted({named(field["type"]) for definition in detail["types"].values() for field in definition.get("fields",{}).values()
                    if detail["type_manifest"][named(field["type"])]["kind"]=="enum"})
      assert enums
      event=ToolCallEvent("enums","get_manager_schema",{"manager":manager,"view":"detail","types":enums,"snapshot":overview["snapshot"]})
     elif selective and current==4:
      detail=observation(reference,feedback)
      full=manager_schema(manager)
      exact=exact and all(value==full["types"][name] for name,value in detail["types"].items())
      definitions.update(detail["types"])
      event=query_call(manager,overview,definitions)
     else:
      result=observation(reference,feedback)
      assert result["data"] and result.get("status")!="error",result
      event=TextChunkEvent(json.dumps({"action":"complete","evidence_ids":[item["evidence_id"] for item in reference["task_evidence"] if item["requirement_ids"]]}))
    exchange["response"]={"type":"tool_call","id":event.id,"name":event.name,"arguments":event.args} if isinstance(event,ToolCallEvent) else {"type":"text","text":event.content}
    yield event
    yield DoneEvent(TokenUsage())
  return Provider()
 case={"id":"selective-"+manager,"turns":["Read "+manager+" with observed native filters and ordering"]}
 report=await run_case(runtime,case,make_profile("strong-only",strong_model="gpt-6-astra"),factory)
 turn=report["turns"][0]
 assert any(item["name"]=="query" for item in turn["tool_results"]),{ "manager":manager,"selective":selective,"errors":[{k:c.get(k) for k in ("role","error_type","error_code")} for c in report["trace"]["provider_calls"] if c.get("error_type")],"responses":[e.get("response") for e in exchanges]}
 query=next(item["result"] for item in turn["tool_results"] if item["name"]=="query")
 return {"manager":manager,"interface":runtime.managers[manager].fixture_interface,"selective":selective,
         "status":report["status"],"selected_definitions_exact":exact,"query_rows":len(query["data"]),
         "persistence_verified":turn["persistence_verified"],"tools":turn["tool_calls"],"results":turn["tool_results"],
         "guard_admissible":all(r["guarded_characters"]<=200000 for r in exchanges),
         "size_comparator_only":not selective,"exchanges":exchanges,"cumulative_request_characters":sum(r["request_characters"] for r in exchanges),
         "source_hashes":report["source_hashes"]}

def query_call(manager,overview,definitions):
 root=overview["roots"][0]
 signatures=overview["root_fields"][root]["arguments"]
 field="year" if manager=="ProjectCommercial" else "code"
 value=2027 if manager=="ProjectCommercial" else "C01" if manager=="Customer" else "M02"
 outputs=overview.get("output_fields",definitions[overview["type"]]["fields"] if not overview.get("output_fields") else {})
 assert field in outputs
 filter_type=named(signatures["filter"]["type"])
 assert field in definitions[filter_type]["fields"]
 order_type=named(signatures["orderBy"]["type"])
 order_fields=definitions[order_type]["fields"]
 assert field in definitions[named(order_fields["field"]["type"])]["values"]
 assert "ASC" in definitions[named(order_fields["direction"]["type"])]["values"]
 return ToolCallEvent("query","query",{"manager":manager,"root":root,"fields":[field],
                     "arguments":{"filter":{field:value},"orderBy":[{"field":field,"direction":"ASC"}]}})

async def run():
 result=[]
 for manager in ("Customer","Material","ProjectCommercial"):
  for selective in (True,False): result.append(await sequence(manager,selective))
 old=runtime.tool("get_manager_schema",{"manager":"Customer"})
 relation=next(r for r in old["relations"] if r["target"]=="Project")
 target=runtime.tool("get_manager_schema",{"manager":"Project"})
 reference_name=next(name for name,info in old["type_manifest"].items() if info.get("manager")=="Project")
 reference=runtime.tool("get_manager_schema",{"manager":"Customer","view":"detail","types":[reference_name],"snapshot":old["snapshot"]})
 assert reference["types"][reference_name]=={"kind":"reference","manager":"Project"}
 assert runtime.tool("get_manager_schema",{"manager":"Project","view":"detail","types":[target["type"]],"snapshot":old["snapshot"]})["code"]=="schema_snapshot_mismatch"
 runtime.managers["Project"].chat_exposed=False
 fresh=runtime.tool("get_manager_schema",{"manager":"Customer"})
 invalid=runtime.tool("get_manager_schema",{"manager":"Customer","view":"detail","types":[old["type"]],"snapshot":old["snapshot"]})
 try:
  runtime.tool("query",{"manager":"Customer","fields":[{relation["name"]:[{"items":["code"]}]}]})
  hidden={"rejected":False}
 except ChatReadContractError as error:
  hidden={"rejected":True,"exception":type(error).__name__,"message":str(error)}
 assert relation["name"] not in fresh["output_fields"]
 assert reference_name not in fresh["type_manifest"]
 assert runtime.tool("get_manager_schema",{"manager":"Project"}) is None
 runtime.managers["Project"].chat_exposed=True
 exact_discovery=runtime.tool("search_managers",{"query":"ProjectCommercial"})
 return {"exact_name_search_probe":{"query":"ProjectCommercial","returned_managers":[row["manager"] for row in exact_discovery],"exact_manager_returned":any(row["manager"]=="ProjectCommercial" for row in exact_discovery)},
         "hidden_relation_rejected":hidden["rejected"],"snapshot_invalidated":invalid.get("code")=="schema_snapshot_mismatch",
         "visibility_controls":{"old":old,"target":target,"reference":reference,"fresh":fresh,"invalid":invalid,"hidden":hidden},
         "manager_count":runtime.manager_count,"live_calls":0,"model_performance_measured":False,
         "interfaces":sorted({s["interface"] for s in result}),"sequences":result}
try: print(json.dumps(asyncio.run(run())))
finally: runtime.close()
"""
