"""Chat exposure covers real generated relation inputs before GraphQL executes."""

import pytest

from tests.experiments.test_gm_eval_fixtures import _subprocess


@pytest.fixture(scope="module")
def native_exposure():
    return _subprocess("""
import json
from experiments.gm_eval.runtime import bootstrap
r=bootstrap(manager_count=10)
from general_manager.api.graphql import GraphQL
from general_manager.chat.tools import get_manager_schema,ScopeChatContext
schema=GraphQL.get_schema()
original=schema.execute
calls=[]
def counted(*args,**kwargs):
 calls.append(True)
 return original(*args,**kwargs)
schema.execute=counted
results={}
try:
 for hidden in ['Customer','Material']:
  manager=GraphQL.manager_registry[hidden];manager.chat_exposed=False
  cases={
   'direct_filter':dict(manager='Project',filters={'customer':{'code':'C01'}},fields=['code']),
   'direct_argument':dict(manager='Project',filters={},arguments={'filter':{'customer':{'code':'C01'}}},fields=['code']),
   'nested_argument':dict(manager='Material',filters={},fields=[{'field':'projectsList','arguments':{'filter':{'customer':{'code':'C01'}}},'fields':[{'items':['code']}]}]),
  } if hidden=='Customer' else {
   operator:dict(manager='Project',filters={'materialsList':{operator:{'code':'M01'}}},fields=['code']) for operator in ['any','none']
  }
  detail=get_manager_schema('Project')
  outcomes={}
  for label,args in cases.items():
   calls.clear()
   try:outcomes[label]={'result':r.tool('query',args),'error':None}
   except Exception as error:outcomes[label]={'error':type(error).__name__,'message':str(error)}
   outcomes[label]['executions']=len(calls)
  results[hidden]={'detail':detail,'outcomes':outcomes,'hidden_detail':get_manager_schema(hidden)}
  results[hidden]['own_filter']=r.tool('query',dict(manager='Project',filters={'code':'P01'},fields=['code']))
  if hidden=='Customer':
   public=original('{'+detail['roots'][0]+'(filter:{customer:{code:"C01"}}){items{code}}}',context_value=ScopeChatContext.from_scope(r.scope))
   results['public']={'data':public.data,'errors':[str(error) for error in public.errors or []]}
  manager.chat_exposed=True
 results['restored']=r.tool('query',dict(manager='Project',filters={'customer':{'code':'C01'}},fields=['code']))
 print('GM_EVAL_RESULT='+json.dumps(results))
finally:r.close()
""")


@pytest.mark.parametrize(
    "hidden,field", [("Customer", "customer"), ("Material", "materialsList")]
)
def test_discovery_omits_hidden_relation_input_fields(native_exposure, hidden, field):
    record = native_exposure[hidden]
    assert record["hidden_detail"] is None
    assert field not in record["detail"]["filters"]
    inputs = {
        name: value
        for name, value in record["detail"]["types"].items()
        if value["kind"] == "input"
    }
    assert f"{hidden}FilterTypeDepth0" not in inputs
    assert field not in inputs["ProjectFilterTypeDepth1"]["fields"]


@pytest.mark.parametrize(
    "hidden,case",
    [
        ("Customer", "direct_filter"),
        ("Customer", "direct_argument"),
        ("Customer", "nested_argument"),
        ("Material", "any"),
        ("Material", "none"),
    ],
)
def test_hidden_relation_input_is_rejected_before_execution(
    native_exposure, hidden, case
):
    result = native_exposure[hidden]["outcomes"][case]
    assert result["error"] == "ChatReadContractError"
    assert "chat-exposed" in result["message"]
    assert result["executions"] == 0


def test_reexposed_relation_filter_still_uses_native_resolver(native_exposure):
    assert native_exposure["restored"]["data"] == [{"code": "P01"}, {"code": "P02"}]


@pytest.mark.parametrize("hidden", ["Customer", "Material"])
def test_hidden_relation_does_not_disable_own_scalar_filters(native_exposure, hidden):
    assert native_exposure[hidden]["own_filter"]["data"] == [{"code": "P01"}]


def test_chat_exposure_does_not_restrict_public_graphql(native_exposure):
    assert native_exposure["public"]["errors"] == []
    assert next(iter(native_exposure["public"]["data"].values()))["items"] == [
        {"code": "P01"},
        {"code": "P02"},
    ]
