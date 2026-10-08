"""Offline checks of the experimental adapter against production chat paths."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest


def test_profiles_resolve_through_production_configuration(settings):
    from experiments.gm_eval.profiles import bind_provider_factory, make_profile
    from experiments.gm_eval.scripted import ScriptedFactory
    from experiments.gm_eval.trace import TraceRecorder
    from general_manager.chat.planned.config import (
        build_profile_provider,
        get_planned_chat_settings,
        profile_for_role,
    )
    from general_manager.chat.providers.base import DoneEvent, Message, TextChunkEvent

    profile = make_profile("weak-fallback", weak_model="weak", strong_model="strong")
    settings.GENERAL_MANAGER = {"CHAT": profile.chat_settings()}
    factory = ScriptedFactory({"fallback": ["fallback reply"]})
    trace = TraceRecorder()

    async def run():
        with bind_provider_factory(factory, trace):
            planned = get_planned_chat_settings()
            configured = profile_for_role(planned, "fallback")
            provider = build_profile_provider(configured)
            assert provider.provider_config["model"] == "strong"
            return [
                event async for event in provider.complete([Message("user", "x")], [])
            ]

    events = asyncio.run(run())
    assert isinstance(events[0], TextChunkEvent)
    assert isinstance(events[-1], DoneEvent)
    assert trace.calls[0]["role"] == "fallback"
    assert trace.calls[0]["model"] == "strong"
    assert trace.calls[0]["reported_usage"] is None
    assert trace.calls[0]["reported_cost"] is None


def test_profile_bridge_without_explicit_binding_cannot_request():
    from experiments.gm_eval.profiles import ExperimentProvider, ProviderBindingError
    from general_manager.chat.providers.base import Message

    provider = ExperimentProvider.from_config({"model": "offline", "role": "planner"})

    async def run():
        return [item async for item in provider.complete([Message("user", "x")], [])]

    with pytest.raises(
        ProviderBindingError, match="explicit_provider_binding_required"
    ):
        asyncio.run(run())


def test_siwc_bridge_preserves_completion_and_no_usage_is_not_zero(settings):
    from experiments.gm_eval.profiles import (
        bind_provider_factory,
        make_profile,
        siwc_factory,
    )
    from experiments.gm_eval.trace import TraceRecorder
    from general_manager.chat.planned.config import (
        build_profile_provider,
        get_planned_chat_settings,
        profile_for_role,
    )
    from general_manager.chat.planned.provider_calls import complete_provider_round
    from general_manager.chat.providers.base import Message

    class Replay:
        async def stream(self, body):
            assert body["model"] == "model"
            assert body["reasoning"] == {"effort": "medium"}
            yield {"type": "response.output_text.delta", "delta": "read"}
            yield {"type": "response.completed", "response": {"output": []}}

    profile = make_profile("strong-only", strong_model="model")
    settings.GENERAL_MANAGER = {"CHAT": profile.chat_settings()}
    trace = TraceRecorder()

    async def run():
        with bind_provider_factory(siwc_factory(Replay()), trace):
            provider = build_profile_provider(
                profile_for_role(get_planned_chat_settings(), "planner")
            )
            return await complete_provider_round(
                provider, [Message("user", "x")], [], 1
            )

    assert asyncio.run(run()).text == "read"
    assert trace.calls[0]["reported_usage"] is None
    assert trace.calls[0]["request_count"] == 1


def test_trace_rejects_wire_secrets_and_hashes_source_files(tmp_path):
    from experiments.gm_eval.trace import TraceRecorder, source_hashes

    trace = TraceRecorder()
    trace.record(
        "tool",
        {
            "data": [{"code": "C01", "access_token": "do-not-save"}],
            "headers": {"Authorization": "secret"},
            "encrypted_content": "hidden",
        },
    )
    rendered = json.dumps(trace.events)
    assert "do-not-save" not in rendered
    assert "secret" not in rendered
    assert "hidden" not in rendered
    assert "C01" in rendered
    source = tmp_path / "source.py"
    source.write_text("first\n")
    first = source_hashes([source])
    source.write_text("second\n")
    assert source_hashes([source]) != first


def test_trace_redacts_credentials_inside_text_without_losing_usage():
    from experiments.gm_eval.trace import sanitize

    content = "REFERENCE_DATA=" + json.dumps(
        {
            "nested": {"encrypted_content": "opaque-secret"},
            "authorization": "Bearer tok_secret_material",
            "input_tokens": 12,
        }
    )
    result = sanitize(
        {
            "content": content,
            "log": "Bearer tok_secret_material sk-fake012345678901234567890 eyJheader123456.eyJpayload123456.signature123456",
            "input_tokens": 12,
        }
    )
    rendered = json.dumps(result)
    for secret in (
        "opaque-secret",
        "tok_secret_material",
        "sk-fake012345678901234567890",
        "signature123456",
    ):
        assert secret not in rendered
    assert result["input_tokens"] == 12
    assert '"input_tokens":12' in result["content"]
    assert "private-value" not in json.dumps(
        sanitize({"log": 'api-key="private-value"; client_secret="private-value"'})
    )


def test_generic_provider_usage_is_reported_without_inventing_reasoning(settings):
    from experiments.gm_eval.profiles import ExperimentProvider, bind_provider_factory
    from experiments.gm_eval.trace import TraceRecorder
    from general_manager.chat.providers.base import (
        DoneEvent,
        Message,
        TextChunkEvent,
        TokenUsage,
    )

    class Reported:
        async def complete(self, messages, tools):
            yield TextChunkEvent("ok")
            yield DoneEvent(TokenUsage(3, 7))

    trace = TraceRecorder()

    async def run():
        with bind_provider_factory(lambda _config: Reported(), trace):
            provider = ExperimentProvider.from_config(
                {"model": "offline", "role": "planner"}
            )
            return [
                event async for event in provider.complete([Message("user", "x")], [])
            ]

    asyncio.run(run())
    assert trace.calls[0]["reported_usage"] == {
        "input_tokens": 3,
        "output_tokens": 7,
        "reasoning_tokens": None,
    }


def _child(program):
    env = os.environ.copy()
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(  # noqa: S603 -- repository-owned offline test programs
        [sys.executable, "-B", "-c", program],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_script_reads_current_reference_before_native_tool_history():
    from experiments.gm_eval.scripted import make_script
    from general_manager.chat.providers.base import (
        Message,
        TextChunkEvent,
        ToolCallEvent,
    )

    provider = make_script({"turns": ["Read Customer"]}, None)({"role": "executor"})
    current = {
        "original_request": "Read Customer",
        "task_evidence": [{"evidence_id": "current-query"}],
    }
    messages = [
        Message("user", 'REFERENCE_DATA={"task_evidence":[]}'),
        Message("user", "REFERENCE_DATA=" + json.dumps(current)),
        Message(
            "assistant",
            "",
            tool_calls=(ToolCallEvent("read", "query", {"manager": "Customer"}),),
        ),
        Message(
            "tool",
            'REFERENCE_DATA={"task_evidence":[]}',
            tool_call_id="read",
            tool_name="query",
            tool_result={"data": []},
        ),
    ]

    async def run():
        return [event async for event in provider.complete(messages, [])]

    events = asyncio.run(run())
    assert isinstance(events[0], TextChunkEvent)
    assert json.loads(events[0].content) == {
        "action": "complete",
        "evidence_ids": ["current-query"],
    }


def test_actual_consumer_and_scheduler_forward_real_discovery_schema_and_query():
    result = _child("""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=5)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import make_script
from general_manager.chat.providers.base import DoneEvent,TextChunkEvent,TokenUsage,ToolCallEvent
case={"id":"feedback","turns":["Find active customers","Which active customers?"]}
def factory_for_case():
 observations=[]
 base=make_script(case,runtime)
 def factory(config):
  class Provider:
   async def complete(self,messages,tools):
    reference=json.loads(next(message.content for message in reversed(messages) if message.role=="user" and message.content.startswith(("REFERENCE_DATA=","RESOLVED_REFERENCE_DATA="))).split("=",1)[1])
    if "task" not in reference:
     async for event in base(config).complete(messages,tools):yield event
     return
    feedback=[message for message in messages if message.role=="tool"]
    assistant_calls=[call for message in messages for call in message.tool_calls]
    assert len(feedback)==len(assistant_calls)
    for message,call in zip(feedback,assistant_calls):
     assert message.tool_call_id==call.id and message.tool_name==call.name
     assert json.loads(message.content)==message.tool_result
    observations.append({"request":reference["original_request"],"tool_ids":[message.tool_call_id for message in feedback],"task_evidence_count":len(reference["task_evidence"])})
    if not feedback:
     yield ToolCallEvent("discovery","search_managers",{"query":"Customer"})
    elif len(feedback)==1:
     assert feedback[0].tool_call_id=="discovery"
     assert any(item["manager"]=="Customer" for item in feedback[0].tool_result)
     assert reference["task_evidence"]==[]
     yield ToolCallEvent("schema","get_manager_schema",{"manager":"Customer"})
    elif len(feedback)==2:
     assert feedback[-1].tool_call_id=="schema" and "active" in json.dumps(next((item["payload"] for item in reference["task_evidence"] if item["evidence_id"]==feedback[-1].tool_result.get("evidence_ref")),feedback[-1].tool_result))
     assert reference["task_evidence"]==[]
     yield ToolCallEvent("query","query",{"manager":"Customer","filters":{"active":True},"fields":["code","name","active"]})
    else:
     assert len(feedback)==3 and next((item["payload"] for item in reference["task_evidence"] if item["evidence_id"]==feedback[-1].tool_result.get("evidence_ref")),feedback[-1].tool_result)["data"]
     assert len(reference["task_evidence"])==1
     yield TextChunkEvent(json.dumps({"action":"complete","evidence_ids":[item["evidence_id"] for item in reference["task_evidence"]]}))
    yield DoneEvent(TokenUsage())
  return Provider()
 return factory,observations
async def run():
 reports=[]
 all_observations=[]
 for route in ("scheduler","consumer"):
  factory,observations=factory_for_case()
  report=await run_case(runtime,case,make_profile("weak-fallback"),factory,route=route)
  reports.append({"status":report["status"],"performance":report["model_performance_measured"],"turns":[{"terminal":turn["terminal"]["type"],"tools":[item["name"] for item in turn["tool_results"]],"persistence":turn["persistence_verified"]} for turn in report["turns"]]})
  all_observations.append(observations)
 return {"reports":reports,"observations":all_observations}
try:print(json.dumps(asyncio.run(run())))
finally:runtime.close()
""")
    assert result["reports"][0] == result["reports"][1]
    assert result["observations"][0] == result["observations"][1]
    for report in result["reports"]:
        assert report["status"] == "infrastructure_check"
        assert report["performance"] is False
        assert all(
            turn
            == {
                "terminal": "done",
                "tools": ["search_managers", "get_manager_schema", "query"],
                "persistence": True,
            }
            for turn in report["turns"]
        )
    for observations in result["observations"]:
        assert [item["tool_ids"] for item in observations] == [
            [],
            ["discovery"],
            ["discovery", "schema"],
            ["discovery", "schema", "query"],
        ] * 2


def test_actual_consumer_recovers_unsupported_block_after_real_schema_feedback():
    result = _child("""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=5)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import make_script
from general_manager.chat.providers.base import DoneEvent,TextChunkEvent,TokenUsage,ToolCallEvent
case={"id":"invalid-block-repair","turns":["List active customers","List them again"]}
description="Read code, name and active; use an exposed filter or retain matching rows after reading; cover all pages."
def factory_for_case():
 observations=[]
 base=make_script(case,runtime)
 stages={}
 def factory(config):
  class Provider:
   async def complete(self,messages,tools):
    from general_manager.chat.planned.schema_projection import logical_messages
    messages=logical_messages(messages)
    reference=json.loads(next(message.content for message in reversed(messages) if message.role=="user" and message.content.startswith(("REFERENCE_DATA=","RESOLVED_REFERENCE_DATA="))).split("=",1)[1])
    if "catalog_and_schema_summary" in reference:
     requirements=[{"requirement_id":"schema","kind":"schema","description":"Inspect Customer fields before reading","operation":None},{"requirement_id":"query","kind":"query","description":description,"operation":None}]
     yield TextChunkEvent(json.dumps({"intent":"read","tasks":[{"task_id":"task_1","objective":"Read Customer records","depends_on":[],"requirements":requirements,"completion_criteria":["schema","query"],"routing_features":[]}]}))
    elif "task" not in reference:
     async for event in base(config).complete(messages,tools):yield event
     return
    else:
     request=reference["original_request"]
     stage=stages.get(request,0)
     stages[request]=stage+1
     feedback=[message for message in messages if message.role=="tool"]
     observations.append({"stage":stage,"description":reference["task"]["requirements"][1].get("description"),"error":reference.get("action_validation_error"),"tool_ids":[message.tool_call_id for message in feedback],"evidence":[item["kind"] for item in reference["task_evidence"]]})
     if stage==0:
      yield ToolCallEvent("schema-id","get_manager_schema",{"manager":"Customer","view":"full"})
     elif stage==1:
      assert feedback[-1].tool_call_id=="schema-id" and "active" in json.dumps(next((item["payload"] for item in reference["task_evidence"] if item["evidence_id"]==feedback[-1].tool_result.get("evidence_ref")),feedback[-1].tool_result))
      yield TextChunkEvent(json.dumps({"action":"block","reason":"active_customers_query_evidence_missing"}))
     elif stage==2:
      yield ToolCallEvent("query-id","query",{"manager":"Customer","fields":["code","name","active"],"filters":{"active":True}})
     else:
      assert stage==3 and next((item["payload"] for item in reference["task_evidence"] if item["evidence_id"]==feedback[-1].tool_result.get("evidence_ref")),feedback[-1].tool_result)["data"]
      assert all(row["active"] is True for row in next((item["payload"] for item in reference["task_evidence"] if item["evidence_id"]==feedback[-1].tool_result.get("evidence_ref")),feedback[-1].tool_result)["data"])
      yield TextChunkEvent(json.dumps({"action":"complete","evidence_ids":[item["evidence_id"] for item in reference["task_evidence"]]}))
    yield DoneEvent(TokenUsage())
  return Provider()
 return factory,observations
async def run():
 results=[]
 for route in ("scheduler","consumer"):
  factory,observations=factory_for_case()
  report=await run_case(runtime,case,make_profile("weak-fallback"),factory,route=route)
  results.append({"status":report["status"],"performance":report["model_performance_measured"],"observations":observations,"turns":[{"terminal":turn["terminal"]["type"],"tools":[item["name"] for item in turn["tool_results"]],"persistence":turn["persistence_verified"]} for turn in report["turns"]]})
 return results
try:print(json.dumps(asyncio.run(run())))
finally:runtime.close()
""")
    assert result[0] == result[1]
    for report in result:
        assert report["status"] == "infrastructure_check"
        assert report["performance"] is False
        assert (
            report["turns"]
            == [
                {
                    "terminal": "done",
                    "tools": ["get_manager_schema", "query"],
                    "persistence": True,
                }
            ]
            * 2
        )
        observations = report["observations"]
        assert [item["stage"] for item in observations] == [0, 1, 2, 3] * 2
        assert all(
            item["description"] and "cover all pages" in item["description"]
            for item in observations
        )
        for offset in (0, 4):
            assert observations[offset]["error"] is None
            assert observations[offset + 2]["error"]["path"] == "$.reason"
            assert observations[offset + 2]["tool_ids"] == ["schema-id"]
            assert observations[offset + 2]["evidence"] == ["schema"]
            assert (
                observations[offset + 3]["error"] == observations[offset + 2]["error"]
            )
            assert observations[offset + 3]["evidence"] == ["schema", "query"]


def test_harness_executes_real_query_and_durable_history():
    result = _child("""
import asyncio, json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import make_script
runtime=bootstrap(manager_count=5)
case={"id":"probe", "turns":["Find XYZ", "Which of those?"]}
try:
 report=asyncio.run(run_case(runtime, case, make_profile("weak-only"), make_script(case,runtime)))
 print(json.dumps(report))
finally:
 runtime.close()
""")
    assert result["status"] == "infrastructure_check"
    assert len(result["turns"]) == 2
    assert result["turns"][0]["tool_results"][0]["result"]["data"]
    assert any(
        "Historical tool data" in m["content"] for m in result["turns"][1]["history"]
    )
    assert all(turn["persistence_verified"] for turn in result["turns"])
    assert result["trace"]["provider_calls"]
    assert result["reference_version"] == "1.10"
    for name in (
        "contract.py",
        "models.py",
        "planner.py",
        "validation.py",
        "schema_projection.py",
        "provider_calls.py",
    ):
        assert any(
            path.endswith(f"/chat/planned/{name}") for path in result["source_hashes"]
        )


@pytest.mark.parametrize("final_only", [False, True])
def test_siwc_child_repair_reaches_real_query_and_consumer_parity(final_only):
    result = _child(
        "FINAL_ONLY="
        + repr(final_only)
        + "\n"
        + """
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=5)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile,siwc_factory
case={"id":"siwc-child-repair","turns":["List active customers","List them again"]}
requirement={"requirement_id":"rows","kind":"query","description":"Read all active Customer rows with code, name and active.","operation":None}
def task(task_id,depends_on):
 return {"task_id":task_id,"objective":"Read active Customer rows","depends_on":depends_on,"requirements":[requirement],"completion_criteria":["rows"],"routing_features":["has_dependency"] if depends_on else []}
class Replay:
 def __init__(self):
  self.stages={}
  self.observations=[]
 async def stream(self,body):
  assert body["reasoning"]=={"effort":"medium"}
  reference=json.loads(next(item["content"] for item in reversed(body["input"]) if item.get("role")=="user" and isinstance(item.get("content"),str) and item["content"].startswith(("REFERENCE_DATA=","RESOLVED_REFERENCE_DATA="))).split("=",1)[1])
  if "catalog_and_schema_summary" in reference:
   reply={"intent":"read","tasks":[task("root",[])]}
  elif "resolved_evidence" in reference:
   assert reference["resolved_evidence"]
   reply={"answer":"Active customer rows have been read.","evidence_ids":[item["evidence_id"] for item in reference["resolved_evidence"]]}
  else:
   task_id=reference["task"]["task_id"]
   key=(reference["original_request"],task_id)
   stage=self.stages.get(key,0)
   self.stages[key]=stage+1
   feedback=[item for item in body["input"] if item.get("type")=="function_call_output"]
   observation={"task_id":task_id,"stage":stage,"error":reference.get("action_validation_error"),"evidence":[item["kind"] for item in reference["task_evidence"]],"tool_ids":[item["call_id"] for item in feedback]}
   self.observations.append(observation)
   if task_id=="root" and stage==0:
    reply={"action":"spawn_children","children":[{"objective":"Read active Customer rows"}]}
   elif task_id=="root" and stage==1:
    error=reference.get("action_validation_error")
    assert error and error["path"]=="$.children[0].completion_criteria", reference
    assert "required field" in error["expected"]
    assert "required_action_schema" in reference
    assert reference["task_evidence"]==[]
    reply={"action":"spawn_children","children":[task("child",["root"])]}
   elif task_id=="child" and stage==0:
    assert reference.get("action_validation_error") is None
    assert not feedback and not reference["task_evidence"]
    yield {"type":"response.completed","response":{"status":"completed","output":[{"type":"function_call","id":"fc-query","status":"completed","call_id":"active-query","name":"query","namespace":"gm","arguments":json.dumps({"manager":"Customer","fields":["code","name","active"],"filters":{"active":True}})}]}}
    return
   else:
    assert (task_id,stage) in (("root",2),("child",1))
    assert reference.get("action_validation_error") is None
    assert observation["evidence"]==["query"]
    if task_id=="child":
     output=json.loads(feedback[-1]["output"])
     rows=next((item["payload"] for item in reference["task_evidence"] if item["evidence_id"]==output.get("evidence_ref")),output)["data"]
     assert rows and all(row["active"] is True for row in rows)
    reply={"action":"complete","evidence_ids":[item["evidence_id"] for item in reference["task_evidence"]]}
  content=json.dumps(reply)
  if not FINAL_ONLY:
   yield {"type":"response.output_text.delta","output_index":0,"content_index":0,"item_id":"message","delta":content}
  yield {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","id":"message","status":"completed","role":"assistant","content":[{"type":"output_text","text":content}]}]}}
async def run():
 reports=[]
 for route in ("scheduler","consumer"):
  transport=Replay()
  factory=siwc_factory(transport)
  factory.offline_only=True
  report=await run_case(runtime,case,make_profile("strong-only"),factory,route=route)
  reports.append({"status":report["status"],"performance":report["model_performance_measured"],"observations":transport.observations,"turns":[{"terminal":turn["terminal"]["type"],"tools":[item["name"] for item in turn["tool_results"]],"persistence":turn["persistence_verified"]} for turn in report["turns"]]})
 return reports
try:print(json.dumps(asyncio.run(run())))
finally:runtime.close()
"""
    )
    assert result[0] == result[1]
    for report in result:
        assert report["status"] == "infrastructure_check", report
        assert report["performance"] is False
        assert (
            report["turns"]
            == [{"terminal": "done", "tools": ["query"], "persistence": True}] * 2
        )
        observations = report["observations"]
        assert [(item["task_id"], item["stage"]) for item in observations] == [
            ("root", 0),
            ("root", 1),
            ("child", 0),
            ("child", 1),
            ("root", 2),
        ] * 2
        for offset in (0, 5):
            assert observations[offset]["error"] is None
            assert observations[offset + 1]["error"]["code"] == "invalid_plan"
            assert observations[offset + 1]["evidence"] == []
            assert observations[offset + 2]["error"] is None
            assert observations[offset + 3]["tool_ids"] == ["active-query"]
            assert observations[offset + 4]["evidence"] == ["query"]


@pytest.mark.parametrize("final_only", [False, True])
def test_siwc_tool_batch_queries_real_rows_with_consumer_parity(final_only):
    result = _child(
        "FINAL_ONLY="
        + repr(final_only)
        + "\n"
        + """
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=5)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile,siwc_factory
case={"id":"siwc-tool-batch","turns":["List active and inactive customers","List both groups again"]}
class Replay:
 def __init__(self):
  self.stages={}
  self.observations=[]
  self.requests=0
 async def stream(self,body):
  self.requests+=1
  wire=next(item["content"] for item in reversed(body["input"]) if item.get("role")=="user" and isinstance(item.get("content"),str) and item["content"].startswith(("REFERENCE_DATA=","RESOLVED_REFERENCE_DATA=")))
  assert wire==self.wire_reference
  reference=self.reference
  if "catalog_and_schema_summary" in reference:
   reply={"intent":"read","tasks":[{"task_id":"root","objective":"Read active and inactive Customer rows","depends_on":[],"requirements":[{"requirement_id":group,"kind":"query","description":"Read Customer code, name and active for "+group,"operation":None} for group in ("active_rows","inactive_rows")],"completion_criteria":["active_rows","inactive_rows"],"routing_features":["multiple_queries"]}]}
  elif "resolved_evidence" in reference:
   assert len(reference["resolved_evidence"])==2,reference
   reply={"answer":"Active: C01, C02. Inactive: C03.","evidence_ids":[item["evidence_id"] for item in reference["resolved_evidence"]]}
  else:
   key=reference["original_request"]
   stage=self.stages.get(key,0)
   self.stages[key]=stage+1
   native=[item for item in body["input"] if item.get("type") in ("function_call","function_call_output")]
   assert reference.get("action_validation_error") is None
   if stage==0:
    assert native==[],native
    assert reference["task_evidence"]==[]
    calls=[{"type":"function_call","id":"fc-"+name,"status":"completed","call_id":name,"name":"query","namespace":"gm","arguments":json.dumps({"manager":"Customer","fields":["code","name","active"],"filters":{"active":active},"requirement_id":name.replace("-","_")})} for name,active in (("active-rows",True),("inactive-rows",False))]
    if not FINAL_ONLY:
     for index,item in enumerate(calls):
      yield {"type":"response.output_item.done","output_index":index,"item":item}
    yield {"type":"response.completed","response":{"status":"completed","output":calls}}
    return
   assert stage==1,stage
   assert [item["type"] for item in native]==["function_call","function_call","function_call_output","function_call_output"],native
   assert [item["call_id"] for item in native]==["active-rows","inactive-rows","active-rows","inactive-rows"]
   outputs=[json.loads(item["output"]) for item in native[2:]]
   outputs=[next((item["payload"] for item in reference["task_evidence"] if item["evidence_id"]==output.get("evidence_ref")),output) for output in outputs]
   assert sorted(row["code"] for row in outputs[0]["data"])==["C01","C02"]
   assert [row["code"] for row in outputs[1]["data"]]==["C03"]
   assert all(row["active"] for row in outputs[0]["data"])
   assert all(not row["active"] for row in outputs[1]["data"])
   evidence=reference["task_evidence"]
   assert [item["kind"] for item in evidence]==["query","query"]
   self.observations.append({"native_ids":[item["call_id"] for item in native],"groups":[sorted(row["code"] for row in item["data"]) for item in outputs]})
   reply={"action":"complete","evidence_ids":[item["evidence_id"] for item in evidence]}
  content=json.dumps(reply)
  yield {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","id":"message","status":"completed","role":"assistant","content":[{"type":"output_text","text":content}]}]}}
async def run():
 reports=[]
 for route in ("scheduler","consumer"):
  transport=Replay()
  base=siwc_factory(transport)
  def factory(config):
   provider=base(config)
   class DecodedReplay:
    async def complete(self,messages,tools):
     from general_manager.chat.planned.schema_projection import logical_messages
     prefix=('REFERENCE_DATA=','RESOLVED_REFERENCE_DATA=')
     transport.wire_reference=next(m.content for m in reversed(messages) if m.role=='user' and m.content.startswith(prefix))
     transport.reference=json.loads(next(m.content for m in reversed(logical_messages(messages)) if m.role=='user' and m.content.startswith(prefix)).split('=',1)[1])
     async for event in provider.complete(messages,tools):yield event
   return DecodedReplay()
  factory.offline_only=True
  report=await run_case(runtime,case,make_profile("strong-only"),factory,route=route)
  reports.append({"status":report["status"],"performance":report["model_performance_measured"],"requests":transport.requests,"observations":transport.observations,"turns":[{"terminal":turn["terminal"]["type"],"tools":[item["name"] for item in turn["tool_results"]],"persistence":turn["persistence_verified"],"answer":turn["answer"]} for turn in report["turns"]]})
 return reports
try:print(json.dumps(asyncio.run(run())))
finally:runtime.close()
"""
    )
    assert result[0] == result[1]
    for report in result:
        assert report["status"] == "infrastructure_check", report
        assert report["performance"] is False
        assert report["requests"] == 8
        assert (
            report["turns"]
            == [
                {
                    "terminal": "done",
                    "tools": ["query", "query"],
                    "persistence": True,
                    "answer": "Active: C01, C02. Inactive: C03.",
                }
            ]
            * 2
        )
        assert (
            report["observations"]
            == [
                {
                    "native_ids": [
                        "active-rows",
                        "inactive-rows",
                        "active-rows",
                        "inactive-rows",
                    ],
                    "groups": [["C01", "C02"], ["C03"]],
                }
            ]
            * 2
        )


@pytest.mark.parametrize("case_id", ["E007", "E008", "E009", "E010", "E100", "E101"])
def test_consumer_parity_has_identical_real_tools_and_history(case_id):
    result = _child(f"""
import asyncio, json
from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import compare_consumer_parity
from experiments.gm_eval.profiles import make_profile
case=next(c for c in load_catalog() if c["id"]=={case_id!r})
runtime=bootstrap(manager_count=case["managers"],snapshot=case["snapshot"],variant=case["fixture_variant"])
try:
 print(json.dumps(asyncio.run(compare_consumer_parity(runtime,case,make_profile("weak-only")))))
finally:
 runtime.close()
""")
    assert result["parity_passed"] is True
    assert result["scheduler"]["status"] in {
        "infrastructure_check",
        "interface_capability_gap",
    }
    assert result["consumer"]["status"] == result["scheduler"]["status"]
    assert result["compared_turns"] >= 1
    if case_id == "E101":
        assert result["compared_turns"] == 2
        for side in (result["scheduler"], result["consumer"]):
            first, second = side["turns"]
            assert (
                first["user"]
                == "Welche Stahlwerkstoffe haben wir aktuell in der Datenbank?"
            )
            assert {"role": "user", "content": first["user"]} in second["history"]
            assert {"role": "assistant", "content": first["answer"]} in second[
                "history"
            ]
            rows = second["tool_results"][0]["result"]["data"]
            assert {row["code"] for row in rows} == {"M02", "M04", "M05"}
            assert second["persistence_verified"]
    if case_id in {"E007", "E008", "E100"}:
        for side in (result["scheduler"], result["consumer"]):
            first = side["turns"][0]
            assert first["answer"].endswith("?")
            assert first["tool_calls"][0]["name"] == "get_manager_schema"
            assert first["tool_results"][0]["result"]
            later_history = side["turns"][1]["history"]
            assert {"role": "user", "content": first["user"]} in later_history
            assert {"role": "assistant", "content": first["answer"]} in later_history
            assert side["turns"][1]["tool_calls"][0]["name"] == "query"
            if len(side["turns"]) > 2:
                second = side["turns"][1]
                assert {"role": "user", "content": second["user"]} in side["turns"][2][
                    "history"
                ]


def test_real_readonly_plan_tool_executes_native_contract():
    result = _child("""
import json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import offline_check
runtime=bootstrap(manager_count=10,snapshot="TREND")
try: print(json.dumps(offline_check(runtime,"E010")))
finally: runtime.close()
""")
    assert result["status"] == "infrastructure_check"
    assert result["turns"][0]["tool_calls"][0]["name"] == "query"
    assert result["turns"][0]["tool_calls"][0]["args"]["manager"] == "ShipmentPlan"
    assert result["observed_capability_gaps"] == []
    assert result["task_quality_status"] == "unscored"


def test_evidence_free_clarification_matches_consumer_failure():
    result = _child("""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import ScriptedFactory
runtime=bootstrap(manager_count=5)
plan=json.dumps({"intent":"read","tasks":[{"task_id":"clarify","objective":"Ask for a metric","depends_on":[],"requirements":[],"completion_criteria":[],"routing_features":[]}]})
def factory():
 return ScriptedFactory({"planner":[plan], **{role:[json.dumps({"action":"complete","evidence_ids":[]})]*8 for role in ("executor","executor","fallback")}})
async def run():
 case={"id":"clarify-probe","turns":["Most important?"]}
 return [await run_case(runtime,case,make_profile("weak-only"),factory(),route=route) for route in ("scheduler","consumer")]
try: print(json.dumps(asyncio.run(run())))
finally:runtime.close()
""")
    assert [side["turns"][0]["terminal"]["code"] for side in result] == [
        "provider_failed",
        "provider_failed",
    ]
    assert all(not side["turns"][0]["answer"] for side in result)
    assert all(
        "error_type" not in call
        for side in result
        for call in side["trace"]["provider_calls"]
    )


def test_siwc_fresh_role_round_does_not_replay_another_roles_reasoning(settings):
    from experiments.gm_eval.profiles import siwc_factory
    from general_manager.chat.providers.base import Message

    class Replay:
        def __init__(self):
            self.bodies = []

        async def stream(self, body):
            self.bodies.append(body)
            yield {"type": "response.output_text.delta", "delta": "ok"}
            yield {
                "type": "response.completed",
                "response": {
                    "output": [
                        {"type": "reasoning", "encrypted_content": "private-reasoning"}
                    ],
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 4,
                        "output_tokens_details": {"reasoning_tokens": 2},
                    },
                },
            }

    replay = Replay()
    factory = siwc_factory(replay)

    async def run():
        for role in ("planner", "synthesizer"):
            provider = factory(
                {"model": "offline", "role": role, "reasoning": "medium"}
            )
            _ = [item async for item in provider.complete([Message("user", role)], [])]
            assert provider.reported_usage["reasoning_tokens"] == 2

    asyncio.run(run())
    assert "private-reasoning" not in json.dumps(replay.bodies[1])
    assert replay.bodies[1]["input"] == [{"role": "user", "content": "synthesizer"}]


def test_persistence_tracks_arguments_and_uncached_failures():
    from experiments.gm_eval.harness import _turn_record
    from experiments.gm_eval.trace import TraceRecorder

    empty = {"data": [], "total_count": 0, "has_more": False}
    failed = {"status": "error", "code": "tool_failed"}
    events, rows = [], [{"role": "user", "content": "read"}]
    trace = TraceRecorder(turn=1)
    for code, result, duplicate in [
        ("P20", empty, False),
        ("P21", empty, False),
        ("P20", empty, True),
        ("P22", failed, False),
        ("P22", failed, False),
    ]:
        args = {"manager": "Project", "filters": {"code": code}, "fields": ["code"]}
        events.extend(
            [
                {"type": "tool_call", "name": "query", "id": code, "args": args},
                {"type": "tool_result", "name": "query", "id": code, "result": result},
            ]
        )
        trace.audit({"event_type": "planned_tool_result", "duplicate": duplicate})
        if not duplicate:
            rows.append(
                {
                    "role": "tool",
                    "tool_name": "query",
                    "tool_args": args,
                    "tool_result": result,
                }
            )
    record = _turn_record(1, "read", events, trace, rows, 0, 0)
    assert record["persistence_verified"] is True
    assert (
        _turn_record(1, "read", events, trace, rows[:-1], 0, 0)["persistence_verified"]
        is False
    )


@pytest.mark.parametrize(
    "failure, expected",
    [
        ("transport", "transport_failure"),
        ("script", "harness_failure"),
        ("invalid", "model_task_failure"),
        ("budget", "budget_exhausted"),
    ],
)
def test_underlying_failures_are_not_reported_as_infrastructure_success(
    failure, expected
):
    result = _child(f"""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import ScriptedFactory
from experiments.siwc_eval.errors import EvalError
runtime=bootstrap(manager_count=5)
response={{"transport":EvalError("transport_error"),"budget":EvalError("request_budget_exhausted"),"invalid":"not a plan","script":None}}[{failure!r}]
factory=ScriptedFactory({{}} if response is None else {{"planner":[response]*2,"fallback":[response]}})
try: print(json.dumps(asyncio.run(run_case(runtime,{{"id":"failure","turns":["Read Customer"]}},make_profile("weak-fallback"),factory))))
finally:runtime.close()
""")
    assert result["status"] == expected
    assert expected in result["failure_flags"]
    assert result["model_performance_measured"] is False


def test_fallback_routes_to_strong_model_after_invalid_weak_plan():
    result = _child("""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import make_script,ScriptedFactory
runtime=bootstrap(manager_count=5)
case={"id":"fallback","turns":["Read Customer"]}
valid=make_script(case,runtime)
invalid=ScriptedFactory({"planner":["invalid","invalid"]})
def factory(config):return invalid(config) if config["role"]=="planner" else valid(config)
try:print(json.dumps(asyncio.run(run_case(runtime,case,make_profile("weak-fallback",weak_model="weak",strong_model="strong"),factory))))
finally:runtime.close()
""")
    assert result["status"] == "infrastructure_check"
    calls = result["trace"]["provider_calls"]
    assert [call["role"] for call in calls[:3]] == [
        "planner",
        "planner",
        "fallback",
    ]
    assert [call["model"] for call in calls[:3]] == ["weak", "weak", "strong"]
    assert result["trace"]["strong_model_requests"] == 1
    assert result["turns"][0]["tool_results"][0]["result"]["data"]


def test_real_scheduler_finishes_past_old_subtree_budget_using_real_reads():
    result = _child("""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
runtime=bootstrap(manager_count=5)
from general_manager.chat.providers.base import ToolCallEvent,DoneEvent,TextChunkEvent,TokenUsage
def task(name,count,dependencies):
 requirements=[{"requirement_id":f"q{i}","kind":"query","description":"Read Customer rows","operation":None} for i in range(count)]
 return {"task_id":name,"objective":"Read Customer","depends_on":dependencies,"requirements":requirements,"completion_criteria":[r["requirement_id"] for r in requirements],"routing_features":(["has_dependency"] if dependencies else [])+(["multiple_queries"] if count>1 else [])}
def factory(config):
 class Provider:
  reported_usage=None
  async def complete(self,messages,tools):
   from general_manager.chat.planned.schema_projection import logical_messages
   reference=json.loads(next(message.content for message in reversed(logical_messages(messages)) if message.role=="user" and message.content.startswith(("REFERENCE_DATA=","RESOLVED_REFERENCE_DATA="))).split("=",1)[1])
   if "catalog_and_schema_summary" in reference:
    reply={"intent":"read","tasks":[task("root",1,[])]}
   elif "resolved_evidence" in reference:
    reply={"answer":"Customer rows read.","evidence_ids":[e["evidence_id"] for e in reference["resolved_evidence"]]}
   elif reference["task"]["task_id"]=="root" and reference["task_evidence"]:
    reply={"action":"complete","evidence_ids":[e["evidence_id"] for e in reference["task_evidence"]]}
   elif reference["task"]["task_id"]=="root":
    reply={"action":"spawn_children","children":[task("child_a",7,["root"]),task("child_b",7,["root"])]}
   elif len(reference["task_evidence"])<7:
    offset=len(reference["task_evidence"])+(10 if reference["task"]["task_id"]=="child_b" else 0)
    yield ToolCallEvent(f"read-{offset}","query",{"manager":"Customer","fields":["code"],"filters":{},"limit":1,"offset":offset,"requirement_id":"q"+str(len(reference["task_evidence"]))})
    yield DoneEvent(TokenUsage())
    return
   else:
    reply={"action":"complete","evidence_ids":[e["evidence_id"] for e in reference["task_evidence"]]}
   yield TextChunkEvent(json.dumps(reply))
   yield DoneEvent(TokenUsage())
 return Provider()
factory.offline_only=True
try:print(json.dumps(asyncio.run(run_case(runtime,{"id":"budget","turns":["Read Customer"]},make_profile("weak-only"),factory))))
finally:runtime.close()
""")
    assert result["status"] == "infrastructure_check"
    assert result["turns"][0]["terminal"]["type"] == "done"
    assert result["model_performance_measured"] is False
    assert len(result["trace"]["provider_calls"]) == 20
    assert len(result["turns"][0]["tool_results"]) == 14
    assert all("data" in item["result"] for item in result["turns"][0]["tool_results"])
    assert result["turns"][0]["persistence_verified"]


@pytest.mark.parametrize("manager_count", [5, 250])
def test_simple_scalar_cases_run_real_tools_at_small_and_large_scale(manager_count):
    result = _child(f"""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.catalog import load_catalog
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import make_script
runtime=bootstrap(manager_count={manager_count})
cases=[case for case in load_catalog() if case["managers"]=={manager_count} and case["contract"] in {{"steel_inventory_scope","active_customers","ongoing_projects"}}]
async def run():
 return [await run_case(runtime,case,make_profile("weak-only"),make_script(case,runtime)) for case in cases]
try:print(json.dumps(asyncio.run(run())))
finally:runtime.close()
""")
    assert len(result) == 3
    for report in result:
        assert report["status"] == "infrastructure_check"
        assert report["manager_count"] == manager_count
        assert report["mode"] == "offline"
        assert report["model_performance_measured"] is False
        turn = report["turns"][0]
        assert turn["tool_calls"][0]["name"] == "query"
        assert turn["tool_results"][0]["result"]["data"]
        assert turn["persistence_verified"]
        for key in (
            "prompt_sha256",
            "fixture_sha256",
            "source_sha256",
            "schema_sha256",
        ):
            assert len(report[key]) == 64
        assert any(
            path.endswith("planned/calculations.py") for path in report["source_hashes"]
        )
        assert any(
            path.endswith("planned/evidence_selection.py")
            for path in report["source_hashes"]
        )
        assert report["business_glossary"]
        assert report["business_glossary"] in turn["history"][0]["content"]


def test_deadline_and_primary_failure_follow_canonical_reporting_hierarchy():
    from experiments.gm_eval.cli import report_has_failures
    from experiments.gm_eval.harness import _failure_flags
    from experiments.gm_eval.scoring import FAILURE_HIERARCHY
    from experiments.gm_eval.trace import TraceRecorder

    turn = {
        "persistence_verified": True,
        "terminal": {"type": "error", "code": "deadline_exceeded"},
    }
    flags = _failure_flags([turn], TraceRecorder(), [])
    assert flags == ["budget_exhausted"]
    assert report_has_failures({"status": flags[0], "failure_flags": flags})
    assert turn["terminal"]["code"] == "deadline_exceeded"
    flags = _failure_flags([turn], TraceRecorder(), [{"tool": "query"}])
    assert flags == ["interface_capability_gap", "budget_exhausted"]
    assert flags == [flag for flag in FAILURE_HIERARCHY if flag in flags]


def test_native_queries_execute_and_invalid_arguments_remain_model_errors():
    reports = _child("""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.gm_eval.scripted import make_script
runtime=bootstrap(manager_count=5)
from general_manager.chat.providers.base import ToolCallEvent
queries=[
 {"manager":"ProjectCommercial","fields":["year"],"filters":{}},
 {"manager":"ProjectCommercial","fields":["invented_field"],"filters":{}},
 {"manager":"Project","fields":["code"],"filters":{"customer":{"invented_filter":"x"}}},
 {"manager":"Project","fields":["code"],"filters":{"customer":{"code":"C02"}}},
 {"manager":"Material","fields":["code","densityGCm3"],"filters":{"code":"M02"}},
 {"manager":"Project","fields":["code",{"materialsList":[{"items":["code"]}]}],"filters":{"code":"P02"}},
 {"manager":"ProjectCommercial","fields":["notARealField"],"filters":{"project":{"code":"P01"}}},
]
async def run():
 reports=[]
 for arguments in queries:
  case={"id":"alternate","turns":["Read Customer"]}
  delegate=make_script(case,runtime)
  def factory(config):
   provider=delegate(config)
   class Wrapper:
    reported_usage=None
    async def complete(self,messages,tools):
     async for event in provider.complete(messages,tools):
      yield ToolCallEvent(event.id,event.name,arguments) if isinstance(event,ToolCallEvent) else event
   return Wrapper()
  reports.append(await run_case(runtime,case,make_profile("weak-only"),factory))
 return reports
try:print(json.dumps(asyncio.run(run())))
finally:runtime.close()
""")
    assert [report["status"] for report in reports] == [
        "infrastructure_check",
        "model_task_failure",
        "model_task_failure",
        "infrastructure_check",
        "infrastructure_check",
        "infrastructure_check",
        "model_task_failure",
    ]
    for report in (reports[0], reports[3], reports[4], reports[5]):
        assert report["observed_capability_gaps"] == []
    for report in (reports[1], reports[2], reports[6]):
        assert report["tool_failure_diagnostics"] == []
        errors = [
            item["result"] for turn in report["turns"] for item in turn["tool_results"]
        ]
        assert any(result.get("code") == "invalid_graphql_request" for result in errors)
        assert all(result.get("status") == "error" for result in errors)
