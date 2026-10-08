"""Native unit choice, operational schema copies, rejected completion and parity."""

import json
import os
from pathlib import Path

import pytest

from tests.experiments.test_gm_eval_harness import _child
from tests.experiments.test_unit_conversion_sequence_v50 import PROGRAM


def program(manager_count, parity):
    value = PROGRAM.replace("MANAGER_COUNT", str(manager_count))
    value = value.replace(
        "exchanges=[]",
        "exchanges=[];observations=0;reference_frames=0;completion_rejections=0",
    )
    value = value.replace(
        "global turn,stage,seen,final_calc",
        "global turn,stage,seen,final_calc,observations,reference_frames,completion_rejections",
    )
    value = value.replace(
        "body={**payload",
        "observations += sum(m.schema_observation is not None for m in messages)\n   reference_frames += sum(m.projection_receipt is not None and m.projection_receipt.get('format')=='gm.reference-data/1' for m in messages)\n   body={**payload",
    )
    value = value.replace(
        "{'manager':'Shipment','requirement_id':'quantity_schema'}",
        "({'manager':'Shipment','requirement_id':'quantity_schema'} if turn==1 else {'manager':'Shipment'})",
    )
    old = "elif now==1:event=ToolCallEvent('factor-schema','get_manager_schema',{'manager':'Project','requirement_id':'factor_schema'})"
    new = """elif now==1:
     observed=next(e for e in ref['task_evidence'] if e['kind']=='schema')
     assert observed['requirement_ids']==[] and any(m.schema_observation is not None for m in messages)
     event=TextChunkEvent(json.dumps({'action':'complete','evidence_ids':[observed['evidence_id']]}))
    elif now==2:
     assert ref.get('action_validation_error',{}).get('code')=='invalid_completion_evidence'
     completion_rejections+=1
     event=ToolCallEvent('quantity-link','get_manager_schema',{'manager':'Shipment','requirement_id':'quantity_schema'})
    elif now==3:event=ToolCallEvent('factor-observation','get_manager_schema',{'manager':'Project'})
    elif now==4:event=ToolCallEvent('factor-schema','get_manager_schema',{'manager':'Project','requirement_id':'factor_schema'})"""
    assert old in value
    value = value.replace(old, new)
    # Shift the unchanged read/bind/calculate/complete sequence three rounds.
    value = value.replace(
        "elif now==2:\n     event=ToolCallEvent('population'",
        "elif now==5:\n     event=ToolCallEvent('population'",
    )
    value = value.replace("elif now==3:\n     schemas=", "elif now==6:\n     schemas=")
    value = value.replace(
        "elif now==4:\n     assert not ref.get", "elif now==7:\n     assert not ref.get"
    )
    value = value.replace("assert now==5 and", "assert now==8 and")
    if parity:
        old = "report=await run_case(runtime,{'id':'native-unit-conversion','turns':['Welche Gesamtmenge wurde für das Projekt P01 im Jahr 2025 geliefert?','Bitte in Kilogramm.']},make_profile('weak-only',weak_model='gpt-6.1-sol'),factory)"
        new = """global turn,stage,seen,final_calc
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
 report['capacity_parity']={'differences':diffs,'consumer':consumer,'scheduler':report.copy()}
 """
        assert old in value
        value = value.replace(old, new)
        value = value.replace(
            "'source_hashes':report['source_hashes'],",
            "'source_hashes':report['source_hashes'],'parity':report['capacity_parity'],",
        )
    value = value.replace(
        "'HTTP_POSTs':0,",
        "'operational_observation_messages':observations,'whole_reference_frames':reference_frames,'unlinked_completion_rejections':completion_rejections,'HTTP_POSTs':0,",
    )
    return value


@pytest.mark.parametrize("manager_count", [5, 50, 250])
@pytest.mark.parametrize("parity", [False, True])
def test_native_operational_observations_preserve_full_followup_and_consumer_parity(
    manager_count, parity
):
    result = _child(program(manager_count, parity))
    if directory := os.environ.get("GM_SCHEMA_EVIDENCE_DIR"):
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (
            path / f"reference-capacity-native-{manager_count}-parity-{parity}.json"
        ).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    assert result["status"] == "infrastructure_check"
    assert result["first_turn_queries_or_calculations"] == 0
    assert result["operational_observation_messages"] >= 2
    assert result["whole_reference_frames"] > 0
    assert result["unlinked_completion_rejections"] == (2 if parity else 1)
    assert result["complete_population"] and result["current_schema_sources"] == 2
    assert result["user_choice_bound"] and result["native_sum_matches"]
    assert result["all_requests_fit_guard"] and result["persistence_verified"]
    assert result["readonly_dimension_verified"]
    assert result["HTTP_POSTs"] == 0 and not result["model_quality_measured"]
    if parity:
        assert result["parity"]["differences"] == []
        assert result["parity"]["consumer"]["status"] == "infrastructure_check"
