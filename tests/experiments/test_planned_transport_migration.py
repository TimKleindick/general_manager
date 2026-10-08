"""Actual Planned reads over native transports with offline providers and real SQL."""

import pytest

from tests.experiments.test_gm_eval_harness import _child


@pytest.mark.parametrize("transport", ["http", "sse", "consumer"])
@pytest.mark.parametrize("variant", ["normal", "empty", "bounded"])
def test_real_planned_reads_and_followups_never_construct_write_provider(
    transport, variant
):
    result = _child(
        PROGRAM.replace("TRANSPORT_VALUE", repr(transport)).replace(
            "VARIANT_VALUE", repr(variant)
        )
    )
    assert result["turns"] == 2
    assert result["roles"] == ["executor", "planner", "synthesizer"]
    assert result["queries"] == 2
    assert result["rows"] == ([0, 0] if variant == "empty" else [1, 1])


PROGRAM = r"""
import asyncio,json
from unittest.mock import patch
from types import SimpleNamespace
from asgiref.sync import async_to_sync
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=5)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile,bind_provider_factory
from experiments.gm_eval.scripted import make_script
from experiments.gm_eval.trace import TraceRecorder
from general_manager.chat.providers.base import ToolCallEvent
from general_manager.chat import views
from django.conf import settings
from django.test import RequestFactory,override_settings
from django.contrib.sessions.backends.db import SessionStore
transport=TRANSPORT_VALUE
variant=VARIANT_VALUE
case={"id":"migration", "turns":["Read customers", "Read those customers again"]}
base=make_script(case,runtime)
rows=[]
queries=[]
roles=set()
def factory(config):
 roles.add(config["role"])
 class Provider:
  async def complete(self,messages,tools):
   if config["role"]=="executor":
    reference=json.loads(next(message.content for message in reversed(messages) if message.role=="user" and message.content.startswith("REFERENCE_DATA=")).split("=",1)[1])
    for item in reference["task_evidence"]:
     if "data" in item["payload"]: rows.append(len(item["payload"]["data"]))
   async for event in base(config).complete(messages,tools):
    if isinstance(event,ToolCallEvent) and event.name=="query":
     args=dict(event.args)
     if variant=="empty": args["filters"]={"code":"NONEXISTENT"}
     elif variant=="bounded": args["filters"]={};args["limit"]=1
     queries.append(args)
     yield ToolCallEvent(event.id,event.name,args)
    else: yield event
 return Provider()
profile=make_profile("strong-only")
with patch("general_manager.chat.consumer.import_provider",side_effect=AssertionError("write provider constructed for read")), patch("general_manager.chat.views.import_provider",side_effect=AssertionError("write provider constructed for read")):
 if transport=="consumer":
  report=asyncio.run(run_case(runtime,case,profile,factory,route="consumer"))
  for turn in report["turns"]:
   assert turn["terminal"]["type"]=="done", turn["events"]
   assert turn["persistence_verified"], turn
  count=len(report["turns"])
 else:
  chat=profile.chat_settings(catalog=runtime.catalog)
  configuration={**settings.GENERAL_MANAGER,"CHAT":chat}
  trace=TraceRecorder()
  session=SessionStore();session.create()
  count=0
  with override_settings(GENERAL_MANAGER=configuration),bind_provider_factory(factory,trace):
   for text in case["turns"]:
    request=RequestFactory().post("/chat/",data=json.dumps({"text":text}),content_type="application/json")
    request.session=session;request.user=runtime.scope["user"];request._dont_enforce_csrf_checks=True
    if transport=="http":
     response=views.chat_http_view(request)
     events=json.loads(response.content)["events"]
    else:
     response=views.chat_sse_view(request)
     async def collect(): return b"".join([chunk async for chunk in response.streaming_content])
     wire=async_to_sync(collect)().decode()
     events=[json.loads(line[6:]) for line in wire.splitlines() if line.startswith("data: ")]
    assert response.status_code==200
    assert events[-1]["type"]=="done",events
    assert not any(event["type"]=="error" for event in events),events
    assert any(event["type"]=="tool_result" for event in events),events
    count+=1
  from general_manager.chat.models import ChatConversation,ChatMessage
  conversation=ChatConversation.objects.get(session_key=session.session_key)
  assert ChatMessage.objects.filter(conversation=conversation,role="user").count()==2
print(json.dumps({"turns":count,"roles":sorted(roles),"queries":len(queries),"rows":rows}))
"""
