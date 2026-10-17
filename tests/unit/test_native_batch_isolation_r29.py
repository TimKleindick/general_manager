"""Fixture reuse keeps independent namespaces and request scopes."""

from tests.utils.native_batch import run_native_programs
import pytest


def test_shared_native_preparation_keeps_request_and_program_state_separate():
    setup = """
import json,os
from experiments.gm_eval.runtime import bootstrap
runtime=bootstrap(manager_count=5,snapshot='RANK',variant='weight',seed=17)
"""
    programs = {
        "first": setup
        + """
marker='first-only'
runtime.scope['session_key']='first-only'
runtime.scope['user'].case_marker='first-only'
print(json.dumps({'pid':os.getpid(),'database':runtime.database_path}))
runtime.close()
""",
        "second": setup
        + """
print(json.dumps({'pid':os.getpid(),'database':runtime.database_path,
                  'marker_leaked':'marker' in globals(),
                  'user_marker_leaked':hasattr(runtime.scope['user'],'case_marker'),
                  'session_key':runtime.scope['session_key']}))
runtime.close()
""",
    }
    results = run_native_programs(programs, manager_count=5)
    assert results["first"]["pid"] == results["second"]["pid"]
    assert results["first"]["database"] == results["second"]["database"]
    assert results["second"]["marker_leaked"] is False
    assert results["second"]["user_marker_leaked"] is False
    assert results["second"]["session_key"] == "gm-eval-17"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            "runtime=bootstrap(manager_count=50,snapshot='RANK',variant='weight',seed=17)",
            "fixture_configuration_mismatch",
        ),
        (
            "from general_manager.chat.graphql_contract import clear_contract_cache\n"
            "clear_contract_cache()",
            "schema_rebuilt_in_shared_fixture",
        ),
        (
            "runtime.managers['Customer'].Interface._model.objects.filter(code='C01').update(name='changed')",
            "source_value_mismatch",
        ),
    ],
)
def test_shared_native_preparation_rejects_changed_fixture_or_schema(body, reason):
    source = (
        "import json\nfrom experiments.gm_eval.runtime import bootstrap\n"
        + body
        + "\nprint(json.dumps({'finished':True}))\n"
    )
    if "runtime.managers" in body:
        source = source.replace(
            body,
            "runtime=bootstrap(manager_count=5,snapshot='RANK',variant='weight',seed=17)\n"
            + body,
        )
    with pytest.raises(AssertionError, match=reason):
        run_native_programs({"changed": source}, manager_count=5)


def test_shared_native_preparation_rejects_changes_to_prior_conversations():
    setup = "import json\nfrom general_manager.chat.models import ChatConversation\n"
    programs = {
        "first": setup
        + "ChatConversation.objects.create(session_key='first-case')\n"
        + "print(json.dumps({'finished':True}))\n",
        "second": setup
        + "ChatConversation.objects.filter(session_key='first-case').update(session_key='second-case')\n"
        + "print(json.dumps({'finished':True}))\n",
    }
    with pytest.raises(AssertionError, match="prior_conversation_changed"):
        run_native_programs(programs, manager_count=5)
