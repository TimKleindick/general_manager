"""Concrete choices travel through real shared GM queries across interfaces."""

import pytest

from tests.experiments.test_gm_eval_fixtures import _subprocess


@pytest.mark.parametrize(
    "manager,codes,labels",
    [
        ("Project", ["P01", "P02"], ["Aurora", "Borealis"]),
        ("Material", ["M01", "M02"], ["Copper", "Steel"]),
    ],
)
def test_shared_query_contract_supplies_selector_without_backend_specific_reader(
    manager, codes, labels
):
    result = _subprocess(f"""
import json
from experiments.gm_eval.runtime import bootstrap
from general_manager.chat.planned.evidence import EvidenceRecord, canonical_call_identity
from general_manager.chat.planned.selector_clarification import render_selector
r = bootstrap(manager_count=10)
try:
    args = {{'manager': {manager!r}, 'filters': {{'code_In': {codes!r}}}, 'fields': ['id', 'code', 'name']}}
    payload = r.tool('query', args)
    evidence = EvidenceRecord.create('current', 'selection', 'query', canonical_call_identity('query', args), {{'tool': 'query', 'manager': {manager!r}}}, payload)
    answer, ids = render_selector({{'language':'en', 'requirements':['record_selector'], 'selector':{{'evidence_id':'current','field':'name'}}}}, (evidence,))
    print('GM_EVAL_RESULT=' + json.dumps({{'answer':answer,'ids':ids,'payload':payload}}))
finally:
    r.close()
""")
    assert result["ids"] == ["current"]
    assert result["payload"]["complete"] is True
    for label in labels:
        assert '"' + label + '"' in result["answer"]
