"""Actual native queries retain source attribution through blind packet assembly."""

from __future__ import annotations

import pytest

from tests.experiments.test_gm_eval_fixtures import _subprocess


@pytest.fixture(scope="module")
def native_provenance():
    return _subprocess("""
import copy,json
from experiments.gm_eval.runtime import bootstrap
r=bootstrap(manager_count=10)
from experiments.gm_eval.adjudication import build_adjudication_request
from tests.experiments.test_gm_eval_adjudication import saved_run,expectation
selections={
 'single_short':[{'customer':['code']}],
 'single_structured':[{'field':'customer','fields':['code']}],
 'page_short':[{'materialsList':[{'items':['code']},{'pageInfo':['totalCount']}]}],
 'page_structured':[{'field':'materialsList','arguments':{'pageSize':1},'fields':[{'field':'items','fields':[{'field':'code'}]},{'pageInfo':['totalCount']}]}],
 'page_mixed':[{'materialsList':[{'field':'items','fields':['code']}]}],
 'metadata_only':[{'materialsList':[{'pageInfo':['totalCount']}]}],
 'empty_page':[{'field':'materialsList','arguments':{'filter':{'code':'DOES-NOT-EXIST'}},'fields':[{'items':['code']},{'pageInfo':['totalCount']}]}],
 'deeper':[{'materialsList':[{'items':['code',{'projectsList':[{'items':['code',{'customer':['name']}]}]}]}]}],
}
results={}
try:
 for label,fields in selections.items():
  args={'manager':'Project','filters':{'code':'P01'},'fields':fields}
  output=r.tool('query',args)
  # Explicit synthetic trace scaffolding, with untouched real query inputs/output.
  run=saved_run();run['schema_index']=r.schema_index;run['trace']={'provider_calls':[]}
  turn=run['turns'][0]
  turn['events'][0]['args']=args
  turn['events'][1]['result']=output
  turn['durable_messages']=[]
  request=build_adjudication_request(expectation(),run,0)
  results[label]={'output':output,'manager_fields':request['packet']['tool_calls'][0]['manager_fields'],'evidence':request['packet']['evidence']}
 print('GM_EVAL_RESULT='+json.dumps(results))
finally:r.close()
""")


@pytest.mark.parametrize("label", ["single_short", "single_structured"])
def test_single_relation_provenance_normalizes_both_selection_forms(
    native_provenance, label
):
    record = native_provenance[label]
    assert record["output"]["data"][0]["customer"] == {"code": "C01"}
    assert record["manager_fields"]["Customer"] == ["code"]
    assert next(row for row in record["evidence"] if row["manager"] == "Customer")[
        "fields"
    ] == ["code"]


@pytest.mark.parametrize("label", ["page_short", "page_structured", "page_mixed"])
def test_page_provenance_credits_returned_item_fields_not_wrapper_metadata(
    native_provenance, label
):
    record = native_provenance[label]
    assert record["output"]["data"][0]["materialsList"]["items"][0]["code"] == "M01"
    assert record["manager_fields"]["Material"] == ["code"]
    assert next(row for row in record["evidence"] if row["manager"] == "Material")[
        "fields"
    ] == ["code"]


@pytest.mark.parametrize("label", ["metadata_only", "empty_page"])
def test_missing_items_do_not_create_a_nested_manager_source(native_provenance, label):
    record = native_provenance[label]
    assert "Material" not in record["manager_fields"]
    assert not any(row["manager"] == "Material" for row in record["evidence"])


def test_page_path_recurses_to_actual_deeper_manager_fields(native_provenance):
    record = native_provenance["deeper"]
    assert record["manager_fields"]["Material"] == ["code", "projectsList"]
    assert record["manager_fields"]["Customer"] == ["name"]
    assert "code" in record["manager_fields"]["Project"]
