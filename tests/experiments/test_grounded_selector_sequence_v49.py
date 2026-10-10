"""Actual two-turn identity clarification without premature business reads."""

import json
import os
from pathlib import Path
import pytest
from tests.experiments.test_gm_eval_harness import _child


@pytest.mark.parametrize("manager_count", [5, 50, 250])
def test_native_grounded_selector_then_explicit_user_identity(manager_count):
    result = _child(PROGRAM.replace("MANAGER_COUNT", str(manager_count)))
    if directory := os.environ.get("GM_SCHEMA_EVIDENCE_DIR"):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"selector-{manager_count}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    assert result["status"] == "infrastructure_check"
    assert result["question_coverage"] == {"resolved": 0, "total": 1}
    assert result["final_coverage"] == {"resolved": 1, "total": 1}
    assert result["first_turn_native_query_count"] == 1
    assert result["first_turn_business_reads"] == 0
    assert result["persisted_question_version"] == 2
    assert result["source_bound_question_seen_in_next_planner"]
    assert result["current_identity_verified_before_related_query"]
    assert result["all_requests_fit_guard"]
    assert result["persistence_verified"]
    assert result["HTTP_POSTs"] == 0 and not result["model_quality_measured"]


PROGRAM = r"""
import asyncio,json
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=MANAGER_COUNT,snapshot='RANK',variant='duplicate_customer',seed=17)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.siwc_eval.provider import payload
from general_manager.chat.providers.base import DoneEvent,TextChunkEvent,TokenUsage,ToolCallEvent
from general_manager.chat.planned.schema_projection import logical_messages
exchanges=[]
turn=0
stage=0
seen=False
reverified=False

def plan_value(second):
 requirements=[{'requirement_id':'customer_schema','kind':'schema','operation':None,'description':'Observe identity fields','schema':{'manager':'Customer','view':'overview','types':[],'snapshot':'current'}},
               {'requirement_id':'identity','kind':'query','operation':None,'description':'Read actual requested customer identity'}]
 if second:
  requirements.append({'requirement_id':'project_schema','kind':'schema','operation':None,'description':'Observe related project fields','schema':{'manager':'Project','view':'overview','types':[],'snapshot':'current'}})
 requirements.append({'requirement_id':'projects','kind':'query','operation':None,'description':'Read projects only after the identity is explicitly chosen and reverified'})
 return {'intent':'read','tasks':[{'task_id':'customer_work','objective':'Resolve the requested identity before related projects','depends_on':[],
       'requirements':requirements,'completion_criteria':[r['requirement_id'] for r in requirements],'routing_features':['multiple_queries']}]}

def factory(config):
 class Provider:
  async def complete(self,messages,tools):
   global turn,stage,seen,reverified
   body=payload(config['model'],messages,tools)
   body={**body,'reasoning':{'effort':'medium'}}
   guarded=len(json.dumps(body))
   item={'role':config['role'],'request':body,'guarded_characters':guarded,'wire_characters':len(json.dumps(body))}
   exchanges.append(item)
   ref=json.loads(next(m.content for m in reversed(logical_messages(messages)) if m.role=='user' and m.content.startswith(('REFERENCE_DATA=','RESOLVED_REFERENCE_DATA='))).split('=',1)[1])
   if 'catalog_and_schema_summary' in ref:
    turn+=1;stage=0
    value=plan_value(turn==2)
    if turn==2:
     qs=ref['choice_questions'];assert len(qs)==1 and qs[0]['topic']=='record_selector'
     assert 'selector_source_sha256' in qs[0]
     seen=True
     history=ref['conversation_context']
     latest=max(i for i,m in enumerate(history) if m['role']=='user')
     value={'plan':value,'choices':[{'question_id':qs[0]['question_id'],'state':'answered','applicability':'same_scope','user_quotes':[{'index':latest,'quote':'C04'}]}],'clarification_requests':[]}
    event=TextChunkEvent(json.dumps(value))
   elif 'resolved_evidence' in ref:
    assert turn==2
    event=TextChunkEvent(json.dumps({'answer':'The related verified projects for the chosen current identity are available.','evidence_ids':[e['evidence_id'] for e in ref['resolved_evidence']]}))
   else:
    now=stage;stage+=1
    if now==0:
     event=ToolCallEvent('customer-schema','get_manager_schema',{'manager':'Customer','requirement_id':'customer_schema'})
    elif now==1:
     schema=next(e['payload'] for e in ref['task_evidence'] if e['requirement_ids']==['customer_schema'])
     assert all(f in schema['output_fields'] for f in ['id','code','name'])
     event=ToolCallEvent('identity','query',{'manager':'Customer','fields':['id','code','name'],'filters':{'name':'Atlas'} if turn==1 else {'code':'C04'},'limit':100,'requirement_id':'identity'})
    elif turn==1:
     assert now==2
     source=next(e for e in ref['task_evidence'] if e['kind']=='query')
     assert len(source['payload']['data'])==2
     event=TextChunkEvent(json.dumps({'action':'clarify_selector','requirement_id':'identity','language':'de','selector':{'evidence_id':source['evidence_id'],'field':'code'}}))
    elif now==2:
     source=next(e for e in ref['task_evidence'] if e['requirement_ids']==['identity'])
     assert source['payload']['complete'] and len(source['payload']['data'])==1 and source['payload']['data'][0]['code']=='C04'
     reverified=True
     event=ToolCallEvent('project-schema','get_manager_schema',{'manager':'Project','requirement_id':'project_schema'})
    elif now==3:
     assert reverified
     schema=next(e['payload'] for e in ref['task_evidence'] if e['requirement_ids']==['project_schema'])
     assert all(f in schema['output_fields'] for f in ['id','code','name','customer'])
     event=ToolCallEvent('projects','query',{'manager':'Project','fields':['id','code','name',{'customer':['id','code']}],'filters':{'customer':{'code':'C04'}},'limit':100,'requirement_id':'projects'})
    else:
     assert now==4,ref.get('action_validation_error')
     event=TextChunkEvent(json.dumps({'action':'complete','evidence_ids':[e['evidence_id'] for e in ref['task_evidence'] if e['requirement_ids']]}))
   item['response']={'type':'tool_call','id':event.id,'name':event.name,'args':event.args} if isinstance(event,ToolCallEvent) else {'type':'text','text':event.content}
   yield event
   yield DoneEvent(TokenUsage())
 return Provider()
async def run():
 report=await run_case(runtime,{'id':'grounded-selector-flow','turns':['Welche Projekte laufen für Atlas?','Ich meine C04.']},make_profile('weak-only',weak_model='gpt-6.1-sol'),factory)
 first,last=report['turns']
 saved=next(r for r in first['durable_messages'] if r['role']=='assistant' and r.get('tool_result',{}).get('gm_clarification'))
 return {'status':report['status'],'manager_count':MANAGER_COUNT,'question_coverage':first['terminal']['orchestration']['coverage'],
     'final_coverage':last['terminal']['orchestration']['coverage'],'first_turn_native_query_count':sum(e['name']=='query' for e in first['tool_results']),
     'first_turn_business_reads':sum(e['name']=='query' and e['args'].get('manager')=='Project' for e in first['tool_calls']),
     'persisted_question_version':saved['tool_result']['gm_clarification']['version'],'source_bound_question_seen_in_next_planner':seen,
     'current_identity_verified_before_related_query':reverified,'all_requests_fit_guard':all(e['guarded_characters']<=200000 for e in exchanges),
     'persistence_verified':all(t['persistence_verified'] for t in report['turns']),'exchanges':exchanges,'native_turns':report['turns'],'source_hashes':report['source_hashes'],
     'cumulative_wire_characters':sum(e['wire_characters'] for e in exchanges),'HTTP_POSTs':0,'model_quality_measured':False}
try:print(json.dumps(asyncio.run(run())))
finally:runtime.close()
"""


@pytest.mark.parametrize("manager_count", [5, 50, 250])
def test_native_selector_question_and_followup_match_consumer_and_scheduler(
    manager_count,
):
    program = PROGRAM.replace("MANAGER_COUNT", str(manager_count))
    original = "report=await run_case(runtime,{'id':'grounded-selector-flow','turns':['Welche Projekte laufen für Atlas?','Ich meine C04.']},make_profile('weak-only',weak_model='gpt-6.1-sol'),factory)"
    replacement = """global turn,stage,seen,reverified
 from experiments.gm_eval.history_parity import provider_messages
 case={'id':'grounded-selector-flow','turns':['Welche Projekte laufen für Atlas?','Ich meine C04.']}
 profile=make_profile('weak-only',weak_model='gpt-6.1-sol')
 report=await run_case(runtime,case,profile,factory)
 turn=0;stage=0;seen=False;reverified=False
 consumer=await run_case(runtime,case,profile,factory,route='consumer')
 keys=('user','answer','history','tool_calls','tool_results','terminal','durable_messages','persistence_verified')
 diffs=[{'turn':i,'field':key} for i,(left,right) in enumerate(zip(report['turns'],consumer['turns'],strict=True),1) for key in keys if left[key]!=right[key]]
 compared=[]
 for side in (report,consumer):
  compared.append([{key:(provider_messages(call,side['turns'][call['turn']-1]['history_schema_sources']) if key=='messages' else call.get(key)) for key in ('turn','role','model','strong','messages','tools_sha256','text','tool_calls','reported_usage','completed','error_type','error_code')} for call in side['trace']['provider_calls']])
 if compared[0]!=compared[1]:diffs.append({'field':'provider_role_traces'})
 report['selector_parity']={'differences':diffs,'scheduler':report.copy(),'consumer':consumer}
 """
    assert original in program
    program = program.replace(original, replacement)
    program = program.replace(
        "'source_hashes':report['source_hashes'],",
        "'source_hashes':report['source_hashes'],'parity':report['selector_parity'],",
    )
    result = _child(program)
    if directory := os.environ.get("GM_SCHEMA_EVIDENCE_DIR"):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"selector-parity-{manager_count}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    assert result["parity"]["differences"] == []
    assert result["parity"]["consumer"]["status"] == "infrastructure_check"
    assert result["current_identity_verified_before_related_query"]
    assert result["all_requests_fit_guard"]
    assert len(result["exchanges"]) == 22
    assert result["HTTP_POSTs"] == 0 and not result["model_quality_measured"]
