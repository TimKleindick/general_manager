"""Regressions using ordinary generated manager schemas and real synthetic data."""

from tests.experiments.test_gm_eval_fixtures import _subprocess


def test_seven_generated_graphql_counterparts_work_through_native_read_tools():
    result = _subprocess("""
import json
from experiments.gm_eval.runtime import bootstrap
r=bootstrap(manager_count=10)
try:
 verified=r.verify()
 queries=[
 {'manager':'Material','fields':['code','isActive'],'filters':{'family':'steel'}},
 {'manager':'Material','fields':['code','densityGCm3'],'filters':{'code':'M01'}},
 {'manager':'ProjectCommercial','fields':['year'],'filters':{}},
 {'manager':'Project','fields':['code'],'filters':{'customer':{'code':'C01'}}},
 {'manager':'Project','fields':['code'],'filters':{'materialsList':{'any':{'code':'M01'}}}},
 {'manager':'Project','fields':['code',{'materialsList':[{'items':['code']},{'pageInfo':['totalCount']}]}],'filters':{'code':'P01'}},
 {'manager':'Shipment','fields':['code','shippedAt','quantity'],'filters':{'shippedAt_Gte':'2026-09-01','shippedAt_Lt':'2026-10-01'}},
 ]
 results=[r.tool('query',q) for q in queries]
 print('GM_EVAL_RESULT='+json.dumps({'results':results,'gaps':verified['gaps'],'source':r.source_integrity()}))
finally:r.close()
""")
    assert result["gaps"] == []
    results = result["results"]
    assert {r["code"] for r in results[0]["data"]} == {"M02", "M04", "M05"}
    assert all(r["isActive"] is True for r in results[0]["data"])
    assert results[1]["data"] == [{"code": "M01", "densityGCm3": 8.96}]
    assert results[2]["data"]
    assert {r["code"] for r in results[3]["data"]} == {"P01", "P02"}
    assert results[4]["data"]
    assert results[5]["data"][0]["materialsList"]["items"]
    assert all(
        "2026-09-01" <= r["shippedAt"] < "2026-10-01" for r in results[6]["data"]
    )


def test_250_manager_context_is_selective_and_below_existing_200k_guard():
    result = _subprocess("""
import json
from types import SimpleNamespace
from experiments.gm_eval.runtime import bootstrap
r=bootstrap(manager_count=250)
from general_manager.chat.consumer import ChatConsumer
from general_manager.chat.views import _planned_catalog_summary
try:
 settings=SimpleNamespace(catalog_source=r.catalog)
 http=_planned_catalog_summary(settings)
 websocket=ChatConsumer._planned_catalog_summary(settings)
 detail=r.tool('get_manager_schema',{'manager':'Material'})
 print('GM_EVAL_RESULT='+json.dumps({'equal':http==websocket,'context_chars':len(json.dumps(http)),'detail_chars':len(json.dumps(detail)),'managers':len(http['schema']),'native':detail['contract_version'],'keys':[list(s) for s in http['schema'].values()]}))
finally:r.close()
""")
    assert result["equal"] and result["native"] == 2
    assert result["managers"] == 250
    assert (
        result["context_chars"] < 150_000
    )  # Leave room for messages and tool definitions.
    assert result["detail_chars"] < 100_000
    assert all("fields" not in keys and "types" not in keys for keys in result["keys"])
