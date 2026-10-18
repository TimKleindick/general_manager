"""Native selective discovery/binding, including next-hop enums and cross-query proof."""

import json
import os
from pathlib import Path

from tests.experiments.test_gm_eval_harness import _child


def test_pending_details_reach_verified_cross_query_and_synthesis_at_250_managers():
    result = _child(PROGRAM)
    if destination := os.environ.get("GM_SCHEMA_EVIDENCE_DIR"):
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "demand-schema-250.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
    assert result["status"] == "infrastructure_check", result
    assert result["failure_flags"] == []
    assert result["persistence_verified"]
    assert result["discovery_unlinked"]
    assert result["enum_names"]
    assert result["full_schema_calls"] == 0
    assert result["model_performance_measured"] is False
    assert result["calculation"]["value"] == 110
    scope = result["calculation"]["scope"]
    assert scope["cross_query"]["entity"]["manager"] == "Project"
    assert scope["cross_query"]["resolved_unit"]["term"]["dimension"] == {
        "[piece_count]": 1
    }
    assert all(
        source["query_schema_witness"]["stable"]
        for source in scope["cross_query"]["sources"]
    )
    assert all(row["characters"] <= 300000 for row in result["exchanges"])
    assert result["exchanges"][-1]["role"] == "synthesizer"


PROGRAM = r"""

import asyncio,json,re,socket
def no_network(*a,**k): raise AssertionError("offline probe forbids network")
socket.socket.connect=no_network
socket.socket.connect_ex=no_network
socket.getaddrinfo=no_network
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=250,snapshot="RANK",variant="returns",seed=17)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import contextual_probe_plan
from experiments.siwc_eval.provider import payload
from general_manager.chat.providers.base import DoneEvent,TextChunkEvent,TokenUsage,ToolCallEvent
from general_manager.chat.planned.schema_projection import logical_messages
from general_manager.chat.graphql_contract import manager_schema
managers=["Project","Customer","Shipment","ShipmentReturn"]
schema_ids={m:"schema_"+m.lower() for m in managers}
requirements=[{"requirement_id":schema_ids[m],"kind":"schema","description":"Observe required native input/output definitions for "+m,"operation":None,"schema":{"manager":m,"view":"detail","types":[],"snapshot":"current"}} for m in managers]
for id,kind,op in [("projects","query",None),("customers","query",None),("paths","path",None),("shipments","query",None),("returns","query",None),("gross_total","calculation","sum"),("returned_total","calculation","sum"),("net_total","calculation","difference")]:
 r={"requirement_id":id,"kind":kind,"description":id,"operation":op}
 if kind=="calculation":r["binding"]=None
 requirements.append(r)
plan={"intent":"read","tasks":[{"task_id":"net","objective":"Net shipped quantity after returns for the requested entity and period","depends_on":[],"requirements":requirements,"completion_criteria":[r["requirement_id"] for r in requirements],"routing_features":["requires_calculation","multiple_queries"]}]}
stage=0;overviews={};base_views={};selected={};exchanges=[];discovery_unlinked=False;enum_names=[]
named=lambda t:re.sub(r"[\[\]!]","",t)
def text(x): return TextChunkEvent(json.dumps(x))
def found(ref,req):
 items=[x for x in ref["task_evidence"] if req in x["requirement_ids"]]
 assert items,(req,ref.get("action_validation_error"))
 return items[-1]
def by_call(ref,call):
 return next(x["payload"] for x in ref["task_evidence"] if x.get("kind")=="schema" and x["payload"].get("manager")==call)
def factory(config):
 class Provider:
  async def complete(self,messages,tools):
   global stage,discovery_unlinked,enum_names
   body={**payload(config["model"],messages,tools),"reasoning":{"effort":config["reasoning"]}}
   exchanges.append({"role":config["role"],"characters":len(json.dumps(body))})
   assert exchanges[-1]["characters"]<=300000
   logical=logical_messages(messages)
   ref=json.loads(next(m.content for m in reversed(logical) if m.role=="user" and m.content.startswith(("REFERENCE_DATA=","RESOLVED_REFERENCE_DATA="))).split("=",1)[1])
   if config["role"]=="planner":
    response=contextual_probe_plan(plan,ref)
    definition=ref["application_definition_source"]["text"]
    quote="Shipment quantities are gross integer pieces; returns and kg are separate."
    assert quote in definition
    index=max(i for i,m in enumerate(ref["conversation_context"]) if m["role"]=="user")
    response["definition_bindings"]=[{"topic":t,"definition_quote":quote,"scope_quote":{"index":index,"quote":ref["original_request"]}} for t in ["metric","unit"]]
    events=[text(response)]
   elif config["role"]=="synthesizer":
    net=next(x for x in ref["resolved_evidence"] if x["kind"]=="calculation" and x["payload"]["operation"]=="difference")
    value=net["payload"]["value"];assert value==110
    exchanges[-1]["calculation"]=net["payload"]
    events=[text({"answer":f"Aurora net shipments in 2025: {value} pieces, 120 gross less 10 returned.","evidence_ids":[x["evidence_id"] for x in ref["resolved_evidence"]]})]
   else:
    i=stage;stage+=1
    if i==0:events=[ToolCallEvent("discover","search_managers",{"query":"net shipped quantity returns"})]
    elif i==1:events=[ToolCallEvent("overview_"+m,"get_manager_schema",{"manager":m}) for m in managers]
    elif i==2:
     for m in managers:
      overviews[m]=by_call(ref,m)
      root=overviews[m]["roots"][0];args=overviews[m]["root_fields"][root]["arguments"]
      selected[m]=[overviews[m]["type"],named(args["filter"]["type"])]
      if m=="Project":selected[m].append(named(args["orderBy"]["type"]))
     events=[ToolCallEvent("discover_types_"+m,"get_manager_schema",{"manager":m,"view":"detail","types":selected[m],"snapshot":overviews[m]["snapshot"]}) for m in managers]
    elif 3<=i<=6:
     if i==3:
      discovery_unlinked=all(not x["requirement_ids"] for x in ref["task_evidence"] if x["kind"]=="schema")
      assert discovery_unlinked
      assert len(ref["pending_schema_bindings"])==4
      for m in managers:
       base_views[m]=next(x["payload"] for x in ref["task_evidence"] if x["kind"]=="schema" and x["payload"]["manager"]==m and x["payload"]["schema_view"]=="detail")
       root=overviews[m]["roots"][0];f=named(overviews[m]["root_fields"][root]["arguments"]["filter"]["type"])
       if m in ["Shipment","ShipmentReturn"]:
        selected[m].extend([named(base_views[m]["types"][f]["fields"]["project"]["type"]),named(overviews[m]["output_fields"]["project"]["type"])])
       if m=="Project":
        order=named(overviews[m]["root_fields"][root]["arguments"]["orderBy"]["type"])
        enum_names=[named(v["type"]) for v in base_views[m]["types"][order]["fields"].values()]
        assert all(base_views[m]["type_manifest"][name]["kind"]=="enum" for name in enum_names)
        selected[m].extend(enum_names)
       selected[m]=sorted(set(selected[m]))
     m=managers[i-3]
     events=[text({"action":"bind_schema","requirement_id":schema_ids[m],"types":selected[m],"snapshot":overviews[m]["snapshot"]})]
    elif i==7:
     assert "pending_schema_bindings" not in ref
     events=[ToolCallEvent("linked_details_"+m,"get_manager_schema",{"manager":m,"view":"detail","types":selected[m],"snapshot":overviews[m]["snapshot"],"requirement_id":schema_ids[m]}) for m in managers]
    elif i==8:
     for m in managers:
      selected_view=found(ref,schema_ids[m])["payload"]
      full=manager_schema(m)
      assert all(value==full["types"][name] for name,value in selected_view["types"].items())
     events=[
      ToolCallEvent("project","query",{"manager":"Project","filters":{"name_Icontains":"Aurora"},"fields":["id","code","name"],"arguments":{"orderBy":[{"field":"code","direction":"ASC"}]},"requirement_id":"projects"}),
      ToolCallEvent("customer","query",{"manager":"Customer","filters":{"name_Icontains":"Aurora"},"fields":["id","code","name"],"requirement_id":"customers"}),
      ToolCallEvent("gross","query",{"manager":"Shipment","filters":{"project":{"id_Exact":"1"},"shippedAt_Gte":"2025-01-01","shippedAt_Lt":"2026-01-01"},"fields":["id","code",{"project":["id"]},"shippedAt","quantity","unit"],"requirement_id":"shipments"}),
      ToolCallEvent("returned","query",{"manager":"ShipmentReturn","filters":{"project":{"id_Exact":"1"},"returnedAt_Gte":"2025-01-01","returnedAt_Lt":"2026-01-01"},"fields":["id","code",{"project":["id"]},"returnedAt","quantity"],"requirement_id":"returns"})]
    elif i==9:events=[ToolCallEvent("path_"+m,"find_path",{"from_manager":m,"to_manager":"Project","requirement_id":"paths"}) for m in ["Shipment","ShipmentReturn"]]
    elif i in [10,11]:
     gross=i==10
     events=[text({"action":"bind_calculation","requirement_id":"gross_total" if gross else "returned_total","binding":{"source_requirement_ids":["shipments" if gross else "returns"],"value_path":["quantity"],"group_by":[],"unit_path":["unit"] if gross else None}})]
    elif i==12:
     calculations=[]
     for req,q in [("gross_total","shipments"),("returned_total","returns")]:
      ev=found(ref,q);assert ev["payload"]["complete"]
      calculations.append({"action":"calculate","requirement_id":req,"operation":"sum","operands":[{"evidence_id":ev["evidence_id"],"path":["data",j,"quantity"]} for j in range(len(ev["payload"]["data"]))]})
     events=[text({"action":"calculate_batch","calculations":calculations})]
    elif i==13:
     reqs=["gross_total","returned_total"];schemas=[schema_ids[m] for m in ["Shipment","ShipmentReturn","Project"]]
     binding={"source_requirement_ids":reqs,"value_path":None,"group_by":[],"unit_path":None,"cross_query":{"sources":[{"source_requirement_id":req,"calculation_evidence_id":found(ref,req)["evidence_id"],"entity_path":["project","id"],"period_path":[date],"entity_filter_path":["project","id_Exact"],"period_start_filter_path":[date+"_Gte"],"period_end_filter_path":[date+"_Lt"]} for req,date in zip(reqs,["shippedAt","returnedAt"])],"schema_requirement_ids":schemas,"schema_evidence_ids":[found(ref,s)["evidence_id"] for s in schemas]}}
     events=[text({"action":"bind_calculation","requirement_id":"net_total","binding":binding})]
    elif i==14:events=[text({"action":"calculate","requirement_id":"net_total","operation":"difference","operands":[{"evidence_id":found(ref,req)["evidence_id"],"path":["value"]} for req in ["gross_total","returned_total"]]})]
    elif i==15:events=[text({"action":"complete","evidence_ids":[x["evidence_id"] for x in ref["task_evidence"] if x["requirement_ids"]]})]
    else:raise AssertionError(ref.get("action_validation_error"))
   for event in events:yield event
   yield DoneEvent(TokenUsage())
 return Provider()
try:
 run=asyncio.run(run_case(runtime,{"id":"selective-cross-query","turns":["What was Aurora's net shipped quantity in 2025 after returns?"]},make_profile("weak-only",weak_model="gpt-6.1-sol",strong_model="gpt-6.1-sol"),factory))
 calculations=[x["calculation"] for x in exchanges if "calculation" in x]
 print(json.dumps({"status":run["status"],"failure_flags":run["failure_flags"],"turn_terminal":run["turns"][0]["terminal"],"persistence_verified":run["turns"][0]["persistence_verified"],"exchanges":exchanges,"discovery_unlinked":discovery_unlinked,"enum_names":enum_names,"full_schema_calls":sum(x["args"].get("view")=="full" for x in run["turns"][0]["tool_calls"] if x["name"]=="get_manager_schema"),"model_performance_measured":run["model_performance_measured"],"calculation":calculations[-1] if calculations else None}))
finally:runtime.close()
"""
