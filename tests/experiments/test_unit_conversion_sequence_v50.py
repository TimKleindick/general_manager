"""Fresh native unit clarification, schema proof, reads and conversion."""

import json
import os
from pathlib import Path

import pytest

from tests.experiments.test_gm_eval_harness import _child


def _save(name, result):
    if directory := os.environ.get("GM_SCHEMA_EVIDENCE_DIR"):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )


@pytest.mark.parametrize("manager_count", [5, 50, 250])
def test_native_unit_question_then_source_bound_conversion(manager_count):
    result = _child(PROGRAM.replace("MANAGER_COUNT", str(manager_count)))
    _save(f"unit-conversion-{manager_count}.json", result)
    assert result["status"] == "infrastructure_check"
    assert result["first_answer"] == "Welche Einheit soll ich verwenden?"
    assert result["first_turn_queries_or_calculations"] == 0
    assert result["user_choice_bound"] and result["native_sum_matches"]
    assert result["unit"] == "kilogram"
    assert result["complete_population"] and result["current_schema_sources"] == 2
    assert result["readonly_dimension_verified"]
    assert result["all_requests_fit_guard"] and result["persistence_verified"]
    assert result["HTTP_POSTs"] == 0 and not result["model_quality_measured"]


PROGRAM = r"""
import asyncio,json
from decimal import Decimal
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=MANAGER_COUNT,snapshot='RANK',variant='weight',seed=17)
from experiments.gm_eval.harness import run_case
from experiments.gm_eval.profiles import make_profile
from experiments.siwc_eval.provider import payload
from general_manager.chat.providers.base import DoneEvent,TextChunkEvent,TokenUsage,ToolCallEvent
from general_manager.chat.planned.schema_projection import logical_messages
exchanges=[]
turn=0;stage=0;seen=False;final_calc=None

def plan_value(second):
 reqs=[{'requirement_id':'quantity_schema','kind':'schema','description':'Observe Shipment actual quantity and unit contract','operation':None,'schema':{'manager':'Shipment','view':'overview','types':[],'snapshot':'current'}}]
 if second:
  reqs += [{'requirement_id':'factor_schema','kind':'schema','description':'Observe Project factor and actual ID contract','operation':None,'schema':{'manager':'Project','view':'overview','types':[],'snapshot':'current'}},
           {'requirement_id':'population','kind':'query','description':'Read complete original Shipment quantity and related Project factor/IDs','operation':None},
           {'requirement_id':'converted','kind':'calculation','description':'Convert the complete original quantities to the requested declared target unit','operation':'sum_products','binding':None}]
 return {'intent':'read','tasks':[{'task_id':'unit_work','objective':'Inspect Shipment quantity units before arithmetic' if not second else 'Convert complete Shipment quantity using the actual Project factor and current exposed dimensions', 'depends_on':[], 'requirements':reqs,'completion_criteria':[r['requirement_id'] for r in reqs], 'routing_features':['requires_calculation'] if second else []}]}

def factory(config):
 class Provider:
  async def complete(self,messages,tools):
   global turn,stage,seen,final_calc
   body={**payload(config['model'],messages,tools),'reasoning':{'effort':'medium'}}
   size=len(json.dumps(body));item={'role':config['role'],'request':body,'guarded_characters':size,'wire_characters':size};exchanges.append(item)
   ref=json.loads(next(m.content for m in reversed(logical_messages(messages)) if m.role=='user' and m.content.startswith(('REFERENCE_DATA=','RESOLVED_REFERENCE_DATA='))).split('=',1)[1])
   if 'catalog_and_schema_summary' in ref:
    turn+=1;stage=0;value=plan_value(turn==2)
    if turn==2:
     qs=ref['choice_questions'];assert len(qs)==1 and qs[0]['topic']=='unit'
     history=ref['conversation_context'];latest=max(i for i,m in enumerate(history) if m['role']=='user')
     value={'plan':value,'choices':[{'question_id':qs[0]['question_id'],'state':'answered','applicability':'same_scope','user_quotes':[{'index':latest,'quote':'Kilogramm'}]}],'clarification_requests':[]};seen=True
    event=TextChunkEvent(json.dumps(value))
   elif 'resolved_evidence' in ref:
    if turn==1:event=TextChunkEvent(json.dumps({'clarification':{'language':'de','requirements':['unit']}}))
    else:
     final_calc=next(e['payload'] for e in ref['resolved_evidence'] if e['kind']=='calculation')
     event=TextChunkEvent(json.dumps({'answer':str(final_calc['value'])+' '+final_calc['scope']['unit']+' from verified original quantities and factors.','evidence_ids':[e['evidence_id'] for e in ref['resolved_evidence']]}))
   else:
    now=stage;stage+=1
    if now==0:event=ToolCallEvent('quantity-schema','get_manager_schema',{'manager':'Shipment','requirement_id':'quantity_schema'})
    elif turn==1:
     assert now==1
     event=TextChunkEvent(json.dumps({'action':'complete','evidence_ids':[e['evidence_id'] for e in ref['task_evidence'] if e['requirement_ids']]}))
    elif now==1:event=ToolCallEvent('factor-schema','get_manager_schema',{'manager':'Project','requirement_id':'factor_schema'})
    elif now==2:
     event=ToolCallEvent('population','query',{'manager':'Shipment','fields':['id','quantity','unit',{'project':['id','code','kgPerPiece']}],'filters':{'project':{'code':'P01'},'shippedAt_Gte':'2025-01-01','shippedAt_Lt':'2026-01-01'},'limit':200,'requirement_id':'population'})
    elif now==3:
     schemas={e['requirement_ids'][0]:e['evidence_id'] for e in ref['task_evidence'] if e['kind']=='schema'}
     binding={'source_requirement_ids':['population'],'value_path':['quantity'],'group_by':[],'unit_path':['unit'],'conversion':{'factor_path':['project','kgPerPiece'],'factor_identity_path':['project','id'],'schema_requirement_ids':['quantity_schema','factor_schema'],'schema_evidence_ids':[schemas['quantity_schema'],schemas['factor_schema']]}}
     event=TextChunkEvent(json.dumps({'action':'bind_calculation','requirement_id':'converted','binding':binding}))
    elif now==4:
     assert not ref.get('action_validation_error'),ref.get('action_validation_error')
     source=next(e for e in ref['task_evidence'] if e['kind']=='query')
     event=TextChunkEvent(json.dumps({'action':'calculate','requirement_id':'converted','operation':'sum_products','operands':[{'evidence_id':source['evidence_id'],'path':['data',i,*path]} for i in range(len(source['payload']['data'])) for path in (['quantity'],['project','kgPerPiece'])]}))
    else:
     assert now==5 and not ref.get('action_validation_error'),ref.get('action_validation_error')
     event=TextChunkEvent(json.dumps({'action':'complete','evidence_ids':[e['evidence_id'] for e in ref['task_evidence'] if e['requirement_ids']]}))
   item['response']={'type':'tool_call','id':event.id,'name':event.name,'args':event.args} if isinstance(event,ToolCallEvent) else {'type':'text','text':event.content}
   yield event;yield DoneEvent(TokenUsage())
 return Provider()

async def run():
 report=await run_case(runtime,{'id':'native-unit-conversion','turns':['Welche Gesamtmenge wurde für das Projekt P01 im Jahr 2025 geliefert?','Bitte in Kilogramm.']},make_profile('weak-only',weak_model='gpt-6.1-sol'),factory)
 first,last=report['turns'];query=next(e['result'] for e in last['tool_results'] if e['name']=='query')
 assert final_calc is not None
 expected=sum((Decimal(str(r['quantity']))*Decimal(str(r['project']['kgPerPiece'])) for r in query['data']),Decimal(0))
 readonly_args={'manager':'Material'}
 readonly=runtime.tool('get_manager_schema',readonly_args)
 density_field=next(name for name,field in readonly['output_fields'].items() if field.get('unit_contract',{}).get('kind')=='factor')
 readonly_query_args={'manager':'Material','fields':['id','code',density_field],'limit':100}
 readonly_query=await asyncio.to_thread(runtime.tool,'query',readonly_query_args)
 return {'status':report['status'],'manager_count':MANAGER_COUNT,'first_answer':first['answer'],'first_turn_queries_or_calculations':sum(e['name']=='query' for e in first['tool_calls'])+sum(e['role']=='executor' and e['response'].get('type')=='text' and json.loads(e['response']['text']).get('action') in ('calculate','calculate_batch') for e in exchanges[:4]),'user_choice_bound':seen,'native_sum_matches':Decimal(str(final_calc['value']))==expected,'unit':final_calc['scope']['unit'],'complete_population':final_calc['scope']['population_complete'],'current_schema_sources':len(final_calc['scope']['conversion']['schema_sources']),'readonly_dimension_verified':readonly['output_fields'][density_field]['unit_contract']['target']['dimension']=={'[mass]':1},'readonly_schema':readonly,'readonly_query':readonly_query,'readonly_exchanges':[{'tool':'get_manager_schema','args':readonly_args,'response':readonly},{'tool':'query','args':readonly_query_args,'response':readonly_query}],'framework_calculation':final_calc,'native_query':query,'persistence_verified':all(t['persistence_verified'] for t in report['turns']),'all_requests_fit_guard':all(e['wire_characters']<=200000 for e in exchanges),'exchanges':exchanges,'native_turns':report['turns'],'source_hashes':report['source_hashes'],'HTTP_POSTs':0,'model_quality_measured':False}
try:print(json.dumps(asyncio.run(run())))
finally:runtime.close()
"""


@pytest.mark.parametrize("manager_count", [5, 50, 250])
def test_native_unit_conversion_matches_consumer_and_scheduler(manager_count):
    program = PROGRAM.replace("MANAGER_COUNT", str(manager_count))
    original = "report=await run_case(runtime,{'id':'native-unit-conversion','turns':['Welche Gesamtmenge wurde für das Projekt P01 im Jahr 2025 geliefert?','Bitte in Kilogramm.']},make_profile('weak-only',weak_model='gpt-6.1-sol'),factory)"
    replacement = """global turn,stage,seen,final_calc
 from experiments.gm_eval.history_parity import provider_messages
 case={'id':'native-unit-conversion','turns':['Welche Gesamtmenge wurde für das Projekt P01 im Jahr 2025 geliefert?','Bitte in Kilogramm.']}
 profile=make_profile('weak-only',weak_model='gpt-6.1-sol')
 report=await run_case(runtime,case,profile,factory)
 turn=0;stage=0;seen=False;final_calc=None
 consumer=await run_case(runtime,case,profile,factory,route='consumer')
 keys=('user','answer','history','tool_calls','tool_results','terminal','durable_messages','persistence_verified')
 diffs=[{'turn':i,'field':key} for i,(left,right) in enumerate(zip(report['turns'],consumer['turns'],strict=True),1) for key in keys if left[key]!=right[key]]
 compared=[]
 for side in (report,consumer):
  compared.append([{key:(provider_messages(call,side['turns'][call['turn']-1]['history_schema_sources']) if key=='messages' else call.get(key)) for key in ('turn','role','model','strong','messages','tools_sha256','text','tool_calls','reported_usage','completed','error_type','error_code')} for call in side['trace']['provider_calls']])
 if compared[0]!=compared[1]:diffs.append({'field':'provider_role_traces'})
 report['unit_parity']={'differences':diffs,'scheduler':report.copy(),'consumer':consumer}
 """
    assert original in program
    program = program.replace(original, replacement)
    program = program.replace(
        "'source_hashes':report['source_hashes'],",
        "'source_hashes':report['source_hashes'],'parity':report['unit_parity'],",
    )
    result = _child(program)
    _save(f"unit-conversion-parity-{manager_count}.json", result)
    assert result["parity"]["differences"] == []
    assert result["parity"]["consumer"]["status"] == "infrastructure_check"
    assert len(result["exchanges"]) == 24
    assert result["all_requests_fit_guard"] and result["native_sum_matches"]
    assert result["HTTP_POSTs"] == 0 and not result["model_quality_measured"]
